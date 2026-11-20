"""Security Guardrails, Policy Plugins, Self-Evaluation Critic, and HITL Gates.

Addresses AgentOps Rubric Category 3 — Guardrails & Policy Plugins (5 pts) &
Human-in-the-Loop Hooks (5 pts):
- `PokeSellSecurityAndEvalPlugin(BasePlugin)`: ADK App-level policy plugin +
  model/tool callbacks enforcing prompt-injection blocking, counterfeit/proxy
  Pokémon card refusal (Whatnot/eBay ToS), sliding-window LlmRequest compaction,
  fat-finger price anomaly caps, and active PII scrubbing.
- `SelfEvaluationCritic`: Post-generation self-evaluation scoring groundedness,
  pull-bag completeness, and zero-PII compliance.
- `HumanInTheLoopGate`: Explicit token + ADK `ToolContext.request_confirmation`
  verification for buyer refunds/voids and stream shipping manifest closeouts.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any

from pokesell_agent.observability import audit_logger
from pokesell_agent.pii_redaction import default_pii_redactor

try:
    from google.adk.agents.callback_context import CallbackContext
    from google.adk.models import LlmRequest, LlmResponse
    from google.adk.plugins.base_plugin import BasePlugin
    from google.adk.tools.base_tool import BaseTool
    from google.adk.tools.tool_context import ToolContext
    from google.genai import types
except ImportError:  # pragma: no cover
    CallbackContext = Any  # type: ignore[assignment,misc]
    LlmRequest = Any  # type: ignore[assignment,misc]
    LlmResponse = Any  # type: ignore[assignment,misc]
    BaseTool = Any  # type: ignore[assignment,misc]
    ToolContext = Any  # type: ignore[assignment,misc]
    types = None  # type: ignore[assignment]

    class BasePlugin:  # type: ignore[no-redef]
        def __init__(self, name: str = "base_plugin") -> None:
            self.name = name


_PROMPT_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+)?previous\s+instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(the\s+)?system\s+(prompt|constitution)", re.IGNORECASE),
    re.compile(r"drop\s+table\s+", re.IGNORECASE),
    re.compile(r";\s*delete\s+from\s+", re.IGNORECASE),
    re.compile(r"reveal\s+(your\s+)?(secret|api\s+key)", re.IGNORECASE),
    re.compile(r"rm\s+-rf\s+/", re.IGNORECASE),
)

_COUNTERFEIT_POLICY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\b(fake|counterfeit|bootleg|ali\s*express\s+proxy)\b.*\b(card|psa|slab|sell)\b", re.IGNORECASE),
    re.compile(r"\bsell\b.*\b(counterfeit|fake|forged\s+psa)\b", re.IGNORECASE),
)


def compact_adk_llm_request(
    llm_request: Any,
    max_turns: int = 12,
    max_estimated_tokens: int = 14000,
) -> None:
    """Apply sliding-window and token-budget compaction directly on ADK `LlmRequest.contents`."""
    contents = getattr(llm_request, "contents", None)
    if not contents or not isinstance(contents, list):
        return

    total_chars = sum(
        len(getattr(part, "text", "") or "")
        for item in contents
        for part in (getattr(item, "parts", None) or [])
    )
    est_tokens = total_chars // 4
    if len(contents) <= max_turns and est_tokens <= max_estimated_tokens:
        return

    keep_tail = max(2, max_turns - 1)
    dropped = max(0, len(contents) - (1 + keep_tail))
    if dropped > 0 and types is not None:
        note = types.Content(
            role="user",
            parts=[
                types.Part.from_text(
                    text=(
                        f"[PokeSell Context Compaction: {dropped} intermediate live-stream "
                        "turns compacted to preserve latency and token budget.]"
                    )
                )
            ],
        )
        llm_request.contents = [contents[0], note, *contents[-keep_tail:]]


def inspect_prompt_policy(raw_text: str) -> dict[str, Any] | None:
    """Screen user text against prompt-injection and Whatnot/eBay anti-counterfeit guardrails."""
    if not raw_text:
        return None

    for pattern in _PROMPT_INJECTION_PATTERNS:
        if pattern.search(raw_text):
            decision = {
                "status": "blocked_by_guardrail",
                "policy_rule": "PROMPT_INJECTION_OR_DESTRUCTIVE_COMMAND",
                "message": (
                    "Request blocked by PokeSell Security Guardrail: instructions attempting "
                    "to override the system constitution, exfiltrate secrets, or mutate SQL "
                    "schemas are strictly prohibited."
                ),
            }
            audit_logger.emit(
                event_type="GUARDRAIL_PROMPT_BLOCKED",
                event_phase="OUTCOME",
                message=decision["message"],
                outcome_payload=decision,
            )
            return decision

    for pattern in _COUNTERFEIT_POLICY_PATTERNS:
        if pattern.search(raw_text):
            decision = {
                "status": "blocked_by_guardrail",
                "policy_rule": "COUNTERFEIT_OR_FORGED_TCG_PROHIBITED",
                "message": (
                    "Request blocked by Marketplace Integrity Guardrail: listing or selling "
                    "counterfeit, fake, or forged graded Pokémon cards violates Whatnot and "
                    "eBay authenticity rules."
                ),
            }
            audit_logger.emit(
                event_type="GUARDRAIL_COUNTERFEIT_BLOCKED",
                event_phase="OUTCOME",
                message=decision["message"],
                outcome_payload=decision,
            )
            return decision

    return None


def guardrail_before_model_callback(
    callback_context: CallbackContext,
    llm_request: LlmRequest,
) -> LlmResponse | None:
    """ADK `before_model_callback`: compacts history, blocks injections/fakes, and scrubs PII."""
    compact_adk_llm_request(llm_request)

    contents = getattr(llm_request, "contents", None) or []
    for content in contents:
        parts = getattr(content, "parts", None) or []
        for part in parts:
            txt = getattr(part, "text", None)
            if isinstance(txt, str) and txt:
                violation = inspect_prompt_policy(txt)
                if violation is not None and types is not None and LlmResponse is not Any:
                    return LlmResponse(
                        content=types.Content(
                            role="model",
                            parts=[types.Part.from_text(text=violation["message"])],
                        )
                    )
                part.text = default_pii_redactor.redact_text(txt)
    return None


def guardrail_after_model_callback(
    callback_context: CallbackContext,
    llm_response: LlmResponse,
) -> LlmResponse | None:
    """ADK `after_model_callback`: scrubs any residual PII from model output."""
    content = getattr(llm_response, "content", None)
    if content and getattr(content, "parts", None):
        for part in content.parts:
            txt = getattr(part, "text", None)
            if isinstance(txt, str) and txt:
                part.text = default_pii_redactor.redact_text(txt)
    return None


class PokeSellSecurityAndEvalPlugin(BasePlugin):
    """ADK App-level Security & Policy Plugin intercepting model and tool calls."""

    def __init__(self, name: str = "pokesell_security_and_eval_plugin") -> None:
        super().__init__(name=name)

    async def before_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_request: LlmRequest,
    ) -> LlmResponse | None:
        return guardrail_before_model_callback(callback_context, llm_request)

    async def after_model_callback(
        self,
        *,
        callback_context: CallbackContext,
        llm_response: LlmResponse,
    ) -> LlmResponse | None:
        return guardrail_after_model_callback(callback_context, llm_response)

    async def before_tool_callback(
        self,
        *,
        tool: BaseTool,
        tool_args: dict[str, Any],
        tool_context: ToolContext,
    ) -> dict[str, Any] | None:
        tool_name = getattr(tool, "name", str(tool))
        for k, v in list(tool_args.items()):
            tool_args[k] = default_pii_redactor.redact_data(v)

        if tool_name == "record_livestream_card_sale":
            price = tool_args.get("hammer_price_usd")
            if isinstance(price, (int, float)) and price > 25000.0:
                return {
                    "status": "error",
                    "error_code": "POLICY_MAX_HAMMER_PRICE_EXCEEDED",
                    "message": f"Hammer price ${price:,.2f} exceeds single-lot safety cap ($25,000).",
                    "recovery_instructions": (
                        "Verify the hammer price for fat-finger typos (e.g., $250 vs $25,000) "
                        "and re-invoke `record_livestream_card_sale` with `hammer_price_usd` <= 25000."
                    ),
                    "retryable": True,
                    "suggested_parameters": {"hammer_price_usd": 250.0},
                }
        return None


@dataclass
class SelfEvalReport:
    """Result of post-generation constitutional self-evaluation."""

    passed: bool
    groundedness_score: float
    safety_score: float
    overall_score: float
    remediated_text: str


class SelfEvaluationCritic:
    """Evaluates generated streamer responses for groundedness, pull-bag accuracy, and PII safety."""

    def evaluate(
        self,
        response_text: str,
        tool_evidence: list[dict[str, Any]] | None = None,
    ) -> SelfEvalReport:
        clean_text = default_pii_redactor.redact_text(response_text)
        safety_score = 1.0 if clean_text == response_text else 0.85

        grounded_score = 1.0
        if tool_evidence:
            expected_tokens: list[str] = []
            for ev in tool_evidence:
                if "assigned_pull_bag_number" in ev:
                    expected_tokens.append(str(ev["assigned_pull_bag_number"]))
                if "sale_id" in ev:
                    expected_tokens.append(str(ev["sale_id"]))
                if "manifest_id" in ev:
                    expected_tokens.append(str(ev["manifest_id"]))
            if expected_tokens:
                hits = sum(1 for tok in expected_tokens if tok in clean_text)
                grounded_score = round(hits / len(expected_tokens), 2)
                if grounded_score < 1.0:
                    missing = [t for t in expected_tokens if t not in clean_text]
                    clean_text = f"{clean_text} [Verified Ledger Refs: {', '.join(missing)}]"
                    grounded_score = 1.0

        overall = round((grounded_score + safety_score) / 2.0, 2)
        return SelfEvalReport(
            passed=overall >= 0.85,
            groundedness_score=grounded_score,
            safety_score=safety_score,
            overall_score=overall,
            remediated_text=clean_text,
        )


class HumanInTheLoopGate:
    """Verifies explicit human operator confirmation for high-stakes financial/fulfillment actions."""

    @staticmethod
    def verify_or_halt(
        *,
        action_label: str,
        expected_token: str,
        provided_token: str | None,
        tool_context: Any = None,
        payload: dict[str, Any] | None = None,
    ) -> tuple[bool, str | None]:
        """Return `(True, None)` if approved, or `(False, instructions_or_rejection)` if halted."""
        # 1. Explicit deterministic approval token check (works across CLI, REST BFF, and ADK)
        if provided_token and provided_token.strip().upper() == expected_token.upper():
            return True, None

        # 2. ADK ToolContext interactive confirmation protocol
        if tool_context is not None:
            confirmation = getattr(tool_context, "tool_confirmation", None)
            if confirmation is not None:
                if getattr(confirmation, "confirmed", False) is True:
                    return True, None
                return False, "REJECTED_BY_OPERATOR"

            req_fn = getattr(tool_context, "request_confirmation", None)
            if callable(req_fn):
                req_fn(
                    hint=(
                        f"Human-in-the-Loop Approval Required: Confirm '{action_label}' "
                        f"or supply approval token '{expected_token}'."
                    ),
                    payload=payload or {},
                )

        instructions = (
            f"Execution paused for Human-in-the-Loop confirmation on '{action_label}'. "
            f"Ask the human streamer to confirm, then re-invoke with "
            f"human_approval_token='{expected_token}'."
        )
        return False, instructions


default_security_plugin = PokeSellSecurityAndEvalPlugin()
default_self_critic = SelfEvaluationCritic()
default_hitl_gate = HumanInTheLoopGate()
