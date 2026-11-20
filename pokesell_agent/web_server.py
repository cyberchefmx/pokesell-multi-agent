"""Secure Backend-for-Frontend (BFF) FastAPI Server for PokeSell Ops Command Center.

Enforces strict security controls aligned with Mandatory Secure Web Guidelines:
- Binds strictly to `127.0.0.1` by default (never `0.0.0.0` during local runs/tests).
- Strict Content-Security-Policy (no `unsafe-inline` or `unsafe-eval`),
  `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Permissions-Policy`,
  and `Cache-Control: no-store`.
- HTTP method allow-list (`GET`, `POST`, `HEAD`, `OPTIONS` only).
- Server-side finite-TTL session management with `HttpOnly`, `SameSite=Strict`
  cookies, explicit session invalidation on logout, Synchronizer CSRF token
  verification on all `POST` mutations, and per-client sliding-window rate limiting.
- Zero hardcoded secrets: session signing secret is retrieved via `SecretManagerService`.
- TODO(security): Integrate OAuth 2.0 / Google Cloud IAP + WebAuthn MFA when
  deploying multi-tenant externally beyond localhost.
"""

from __future__ import annotations

from collections import defaultdict, deque
import hmac
import os
from pathlib import Path
import secrets
import time
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field

from pokesell_agent.agent import default_orchestrator
from pokesell_agent.database import get_ledger_db
from pokesell_agent.observability import audit_logger, span_recorder
from pokesell_agent.pii_redaction import default_pii_redactor, mask_buyer_handle_or_contact
from pokesell_agent.schemas import (
    GradingCompany,
    MarketplacePlatform,
    SellingFormat,
)
from pokesell_agent.secrets import get_secret_manager
from pokesell_agent.tools import (
    assign_fulfillment_pull_bag_and_packaging_tier,
    execute_buyer_refund_or_sale_void,
    finalize_stream_shipping_manifest_batch,
    generate_stream_revenue_and_inventory_insights,
    lookup_buyer_purchase_history_and_vip_tier,
    record_livestream_card_sale,
    register_pokemon_inventory_lot,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"
SESSION_TTL_SECONDS = 3600
MAX_REQUESTS_PER_MINUTE = int(os.environ.get("POKESELL_RATE_LIMIT_RPM", "180"))


class _SessionStore:
    """Server-side finite-TTL session store with Synchronizer CSRF tokens."""

    def __init__(self, ttl_seconds: int = SESSION_TTL_SECONDS) -> None:
        self.ttl_seconds = ttl_seconds
        self._sessions: dict[str, dict[str, Any]] = {}

    def _signing_secret(self) -> str:
        return get_secret_manager().get_secret(
            "POKESELL_DASHBOARD_SESSION_SECRET",
            allow_ephemeral_fallback=True,
        )

    def create_session(self) -> tuple[str, str]:
        now = time.time()
        self._prune_expired(now)
        raw_id = secrets.token_urlsafe(32)
        csrf_token = secrets.token_hex(32)
        sig = hmac.new(
            self._signing_secret().encode("utf-8"),
            raw_id.encode("utf-8"),
            "sha256",
        ).hexdigest()[:24]
        session_id = f"{raw_id}.{sig}"
        self._sessions[session_id] = {
            "csrf_token": csrf_token,
            "expires_at": now + self.ttl_seconds,
        }
        return session_id, csrf_token

    def get_valid_session(self, session_id: str | None) -> dict[str, Any] | None:
        if not session_id:
            return None
        now = time.time()
        record = self._sessions.get(session_id)
        if record is None:
            return None
        if float(record["expires_at"]) < now:
            self._sessions.pop(session_id, None)
            return None
        return record

    def invalidate(self, session_id: str | None) -> None:
        if session_id:
            self._sessions.pop(session_id, None)

    def _prune_expired(self, now: float) -> None:
        expired = [
            sid for sid, data in self._sessions.items() if float(data["expires_at"]) < now
        ]
        for sid in expired:
            self._sessions.pop(sid, None)


_session_store = _SessionStore()
_rate_buckets: dict[str, deque[float]] = defaultdict(deque)


def _cookie_name_for_request(request: Request) -> str:
    """Use `__Host-` cookie prefix on HTTPS; localhost HTTP uses `__Secure-` fallback note."""
    if request.url.scheme == "https":
        return "__Host-pokesell_session"
    # Browser spec rejects `__Host-` without Secure/HTTPS on http://127.0.0.1 local dev
    return "pokesell_local_session"


def _extract_session_cookie(request: Request) -> str | None:
    return request.cookies.get("__Host-pokesell_session") or request.cookies.get(
        "pokesell_local_session"
    )


def _require_authenticated_session(request: Request, *, check_csrf: bool) -> str:
    """Verify server-side session (or Bearer API secret) and enforce CSRF on mutations."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        presented = auth_header.removeprefix("Bearer ").strip()
        expected = get_secret_manager().get_secret(
            "POKESELL_API_BEARER_TOKEN",
            allow_ephemeral_fallback=True,
        )
        if presented and hmac.compare_digest(presented, expected):
            return "bearer-api-session"
        raise HTTPException(status_code=401, detail="Invalid Bearer token.")

    sid = _extract_session_cookie(request)
    session = _session_store.get_valid_session(sid)
    if session is None or sid is None:
        raise HTTPException(
            status_code=401,
            detail="Session expired or missing. Refresh via /api/v1/session/bootstrap.",
        )

    if check_csrf:
        csrf_header = request.headers.get("X-CSRF-Token", "")
        if not csrf_header or not hmac.compare_digest(
            csrf_header, str(session["csrf_token"])
        ):
            raise HTTPException(
                status_code=403,
                detail="CSRF token missing or invalid.",
            )
    return sid


# ---------------------------------------------------------------------------
# Strict Request Payload Schemas for BFF Endpoints
# ---------------------------------------------------------------------------


class ChatPromptPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    prompt: str = Field(..., min_length=1, max_length=600)
    stream_id: str = Field(default="STREAM-LIVE-001", min_length=4, max_length=48)
    human_approval_token: str | None = Field(default=None, max_length=80)


class QuickSalePayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stream_id: str = Field(default="STREAM-LIVE-001", min_length=4, max_length=48)
    platform: MarketplacePlatform
    buyer_handle: str = Field(..., min_length=2, max_length=64)
    item_title: str = Field(..., min_length=2, max_length=160)
    selling_format: SellingFormat
    hammer_price_usd: float = Field(..., gt=0.0, le=25000.0)
    quantity: int = Field(default=1, ge=1, le=200)
    sku: str | None = Field(default=None, max_length=48)
    source_storage_location: str | None = Field(default=None, max_length=64)
    rtyh_packs_ripped: int = Field(default=0, ge=0, le=100)
    rtyh_hit_card: str | None = Field(default=None, max_length=160)
    buyer_notes: str | None = Field(default=None, max_length=300)


class QuickInventoryPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sku: str = Field(..., min_length=3, max_length=48)
    item_title: str = Field(..., min_length=3, max_length=160)
    set_name: str = Field(default="Pokemon TCG", min_length=2, max_length=80)
    selling_format: SellingFormat
    storage_location: str = Field(..., min_length=3, max_length=64)
    grading_company: GradingCompany = Field(default=GradingCompany.RAW)
    grade_score: float | None = Field(default=None, ge=1.0, le=10.0)
    cost_basis_usd: float = Field(default=0.0, ge=0.0, le=25000.0)
    quantity_available: int = Field(default=1, ge=1, le=5000)


class HitlRefundPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sale_id: str = Field(..., min_length=6, max_length=32)
    reason: str = Field(..., min_length=8, max_length=300)
    restock_inventory: bool = True
    human_approval_token: str | None = Field(default=None, max_length=80)


class HitlManifestPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    stream_id: str = Field(..., min_length=4, max_length=48)
    human_approval_token: str | None = Field(default=None, max_length=80)


def create_web_app() -> FastAPI:
    """Create the hardened FastAPI Live-Stream Command Center application."""
    allowed_origins = [
        o.strip()
        for o in os.environ.get(
            "POKESELL_ALLOWED_ORIGINS",
            "http://127.0.0.1:8080,http://localhost:8080",
        ).split(",")
        if o.strip() and o.strip() != "*"
    ]

    web_app = FastAPI(
        title="PokeSell Ops — Live-Stream Command Center BFF",
        version="1.0.0",
        docs_url=None,
        redoc_url=None,
    )

    web_app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-CSRF-Token", "Authorization"],
    )

    @web_app.middleware("http")
    async def security_headers_method_and_ratelimit_middleware(
        request: Request,
        call_next: Any,
    ) -> Response:
        # 1. Enforce HTTP method allow-list (disable PUT, DELETE, TRACE, PATCH)
        if request.method not in ("GET", "POST", "HEAD", "OPTIONS"):
            return JSONResponse(
                status_code=405,
                content={"status": "error", "message": "HTTP method not allowed."},
            )

        # 2. Sliding-window per-client rate limiting
        client_ip = request.client.host if request.client else "127.0.0.1"
        now = time.monotonic()
        bucket = _rate_buckets[client_ip]
        while bucket and (now - bucket[0]) > 60.0:
            bucket.popleft()
        if len(bucket) >= MAX_REQUESTS_PER_MINUTE:
            return JSONResponse(
                status_code=429,
                content={"status": "error", "message": "Rate limit exceeded. Slow down."},
            )
        bucket.append(now)

        try:
            response: Response = await call_next(request)
        except Exception:
            # Fail closed with generic error message; never expose SQL or stack traces
            response = JSONResponse(
                status_code=500,
                content={"status": "error", "message": "Internal server error."},
            )

        # 3. Strict security headers on every HTTP response
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; connect-src 'self'; object-src 'none'; "
            "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        )
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    web_app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @web_app.get("/")
    async def serve_dashboard_index(request: Request) -> Response:
        sid = _extract_session_cookie(request)
        session = _session_store.get_valid_session(sid)
        resp = FileResponse(STATIC_DIR / "index.html")
        if session is None:
            new_sid, _ = _session_store.create_session()
            cookie_name = _cookie_name_for_request(request)
            resp.set_cookie(
                key=cookie_name,
                value=new_sid,
                max_age=SESSION_TTL_SECONDS,
                httponly=True,
                secure=(request.url.scheme == "https"),
                samesite="strict",
                path="/",
            )
        return resp

    @web_app.get("/api/v1/session/bootstrap")
    async def bootstrap_session(request: Request, response: Response) -> dict[str, Any]:
        sid = _extract_session_cookie(request)
        session = _session_store.get_valid_session(sid)
        if session is None or sid is None:
            sid, csrf_token = _session_store.create_session()
            cookie_name = _cookie_name_for_request(request)
            response.set_cookie(
                key=cookie_name,
                value=sid,
                max_age=SESSION_TTL_SECONDS,
                httponly=True,
                secure=(request.url.scheme == "https"),
                samesite="strict",
                path="/",
            )
        else:
            csrf_token = str(session["csrf_token"])
        return {"status": "authenticated", "csrf_token": csrf_token}

    @web_app.post("/api/v1/session/logout")
    async def logout_session(request: Request, response: Response) -> dict[str, str]:
        sid = _require_authenticated_session(request, check_csrf=True)
        _session_store.invalidate(sid)
        response.delete_cookie("__Host-pokesell_session", path="/")
        response.delete_cookie("pokesell_local_session", path="/")
        return {"status": "logged_out"}

    @web_app.get("/api/v1/dashboard")
    async def get_dashboard_snapshot(
        request: Request,
        stream_id: str | None = None,
    ) -> dict[str, Any]:
        _ = _require_authenticated_session(request, check_csrf=False)
        db = get_ledger_db()
        insights = generate_stream_revenue_and_inventory_insights(stream_id=None)
        open_bags = db.list_open_pull_bags(stream_id=None)
        inventory = db.list_inventory()
        recent_sales = db.list_recent_sales(limit=20)
        for sale in recent_sales:
            sale["buyer_handle"] = mask_buyer_handle_or_contact(str(sale.get("buyer_handle", "")))
            if sale.get("buyer_notes_redacted"):
                sale["buyer_notes_redacted"] = default_pii_redactor.redact_text(
                    str(sale["buyer_notes_redacted"])
                )
        return {
            "status": "success",
            "insights": insights,
            "open_pull_bags": open_bags,
            "inventory": inventory,
            "recent_sales": recent_sales,
            "recent_audit_events": audit_logger.get_audit_trail()[-8:],
            "recent_trace_spans": span_recorder.get_recorded_spans()[-8:],
        }

    @web_app.get("/api/v1/buyers/lookup")
    async def lookup_buyer_endpoint(
        request: Request,
        buyer_handle: str,
        stream_id: str = "STREAM-LIVE-001",
    ) -> dict[str, Any]:
        _ = _require_authenticated_session(request, check_csrf=False)
        return lookup_buyer_purchase_history_and_vip_tier(
            buyer_handle=buyer_handle,
            active_stream_id=stream_id,
        )

    @web_app.get("/api/v1/bags/{bag_number}")
    async def inspect_pull_bag_endpoint(
        request: Request,
        bag_number: str,
    ) -> dict[str, Any]:
        _ = _require_authenticated_session(request, check_csrf=False)
        return assign_fulfillment_pull_bag_and_packaging_tier(bag_number=bag_number)

    @web_app.post("/api/v1/chat")
    async def chat_with_coordinator(
        request: Request,
        payload: ChatPromptPayload,
    ) -> dict[str, Any]:
        sid = _require_authenticated_session(request, check_csrf=True)
        return await default_orchestrator.handle_operator_prompt(
            session_id=sid[:24],
            prompt=payload.prompt,
            stream_id=payload.stream_id,
            human_approval_token=payload.human_approval_token,
        )

    @web_app.post("/api/v1/sales")
    async def post_quick_sale(
        request: Request,
        payload: QuickSalePayload,
    ) -> dict[str, Any]:
        _ = _require_authenticated_session(request, check_csrf=True)
        return record_livestream_card_sale(
            stream_id=payload.stream_id,
            platform=payload.platform.value,
            buyer_handle=payload.buyer_handle,
            item_title=payload.item_title,
            selling_format=payload.selling_format.value,
            hammer_price_usd=payload.hammer_price_usd,
            quantity=payload.quantity,
            sku=payload.sku,
            source_storage_location=payload.source_storage_location,
            rtyh_packs_ripped=payload.rtyh_packs_ripped,
            rtyh_hit_card=payload.rtyh_hit_card,
            buyer_notes=payload.buyer_notes,
        )

    @web_app.post("/api/v1/inventory")
    async def post_inventory_lot(
        request: Request,
        payload: QuickInventoryPayload,
    ) -> dict[str, Any]:
        _ = _require_authenticated_session(request, check_csrf=True)
        return register_pokemon_inventory_lot(
            sku=payload.sku,
            item_title=payload.item_title,
            set_name=payload.set_name,
            selling_format=payload.selling_format.value,
            storage_location=payload.storage_location,
            grading_company=payload.grading_company.value,
            grade_score=payload.grade_score,
            cost_basis_usd=payload.cost_basis_usd,
            quantity_available=payload.quantity_available,
        )

    @web_app.post("/api/v1/hitl/refund")
    async def post_hitl_refund(
        request: Request,
        payload: HitlRefundPayload,
    ) -> dict[str, Any]:
        _ = _require_authenticated_session(request, check_csrf=True)
        return execute_buyer_refund_or_sale_void(
            sale_id=payload.sale_id,
            reason=payload.reason,
            restock_inventory=payload.restock_inventory,
            human_approval_token=payload.human_approval_token,
        )

    @web_app.post("/api/v1/hitl/finalize-manifest")
    async def post_hitl_manifest(
        request: Request,
        payload: HitlManifestPayload,
    ) -> dict[str, Any]:
        _ = _require_authenticated_session(request, check_csrf=True)
        return finalize_stream_shipping_manifest_batch(
            stream_id=payload.stream_id,
            human_approval_token=payload.human_approval_token,
        )

    return web_app


web_app = create_web_app()


def main() -> None:
    import uvicorn

    # Bind strictly to 127.0.0.1 by default for local security
    host = os.environ.get("POKESELL_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("POKESELL_BIND_PORT", "8080"))
    uvicorn.run(web_app, host=host, port=port)


if __name__ == "__main__":
    main()
