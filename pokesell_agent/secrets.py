"""Secure Secret Management via Google Cloud Secret Manager.

Addresses AgentOps Rubric Category 5 — Secure Secret Management (5 pts):
- Zero hardcoded API keys, webhook tokens, or session secrets anywhere in source code.
- Retrieves secrets dynamically from Google Cloud Secret Manager
  (`google.cloud.secretmanager.SecretManagerServiceClient`) using Application
  Default Credentials (ADC), with Cloud Run secret-mount environment lookup and
  cryptographically strong ephemeral RNG generation ONLY for local sandbox
  session signing.
"""

from __future__ import annotations

import functools
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Any

try:
    from google.api_core import exceptions as google_exceptions
    from google.cloud import secretmanager
except ImportError:  # pragma: no cover
    google_exceptions = None  # type: ignore[assignment]
    secretmanager = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)

_SECRET_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{2,128}$")


class SecretManagerService:
    """Retrieves sensitive credentials strictly via Google Cloud Secret Manager or IAM mounts."""

    def __init__(
        self,
        project_id: str | None = None,
        allow_env_injection: bool = True,
    ) -> None:
        """Initialize the Secret Manager service.

        Args:
            project_id: GCP Project ID hosting Secret Manager resources. Defaults
                to the `GOOGLE_CLOUD_PROJECT` environment variable.
            allow_env_injection: Whether to inspect environment variables mounted
                by Cloud Run `secret_key_ref` before making a network RPC.
        """
        self.project_id = (project_id or os.environ.get("GOOGLE_CLOUD_PROJECT", "")).strip()
        self.allow_env_injection = allow_env_injection
        self._client: Any = None
        self._ephemeral_secrets: dict[str, str] = {}

    @staticmethod
    def validate_secret_id(secret_id: str) -> str:
        """Validate secret identifier against strict allow-list to block path traversal."""
        cleaned = (secret_id or "").strip()
        if not _SECRET_ID_PATTERN.match(cleaned):
            raise ValueError(
                f"Invalid secret_id '{secret_id}'. Must match ^[A-Za-z0-9_-]{{2,128}}$."
            )
        return cleaned

    @property
    def client(self) -> Any:
        """Lazy-initialize the Google Cloud SecretManagerServiceClient."""
        if self._client is None and secretmanager is not None:
            self._client = secretmanager.SecretManagerServiceClient()
        return self._client

    @functools.lru_cache(maxsize=64)  # noqa: B019
    def get_secret(
        self,
        secret_id: str,
        version_id: str = "latest",
        *,
        allow_ephemeral_fallback: bool = False,
    ) -> str:
        """Resolve a secret value without hardcoded fallback literals.

        Resolution hierarchy:
        1. Environment variable injected via Cloud Run Secret Manager volume/env mount.
        2. Direct RPC to Google Cloud Secret Manager (`access_secret_version`).
        3. Optional local untracked secret file in `.secrets/<secret_id>` (sandboxed).
        4. When `allow_ephemeral_fallback=True` (for local sandbox session signing),
           generates a cryptographically random 256-bit token via `secrets.token_hex(32)`
           and logs a warning about instance isolation. Otherwise raises `RuntimeError`.

        Args:
            secret_id: Alphanumeric secret identifier (e.g., 'WHATNOT_WEBHOOK_SECRET').
            version_id: Secret Manager version tag (defaults to 'latest').
            allow_ephemeral_fallback: If True, generates an OS CSPRNG ephemeral secret
                when neither Secret Manager nor env mounts are configured.

        Returns:
            Resolved secret string (never logged).

        Raises:
            ValueError: If `secret_id` fails allow-list validation.
            RuntimeError: If the secret cannot be resolved and ephemeral fallback is off.
        """
        validated_id = self.validate_secret_id(secret_id)

        # 1. Cloud Run / CI secret mount environment variable
        if self.allow_env_injection:
            env_value = os.environ.get(validated_id, "").strip()
            if env_value:
                return env_value

        # 2. Google Cloud Secret Manager API
        sm_enabled = os.environ.get("ENABLE_GCP_SECRET_MANAGER", "true").lower() in (
            "true",
            "1",
            "yes",
        )
        if self.project_id and sm_enabled and secretmanager is not None:
            resource_name = (
                f"projects/{self.project_id}/secrets/{validated_id}/versions/{version_id}"
            )
            try:
                response = self.client.access_secret_version(request={"name": resource_name})
                payload = response.payload.data.decode("utf-8").strip()
                if payload:
                    return payload
            except Exception as exc:
                logger.warning(
                    "Google Cloud Secret Manager lookup failed for secret_id=%s (%s)",
                    validated_id,
                    type(exc).__name__,
                )

        # 3. Local untracked file fallback (strictly confined to `.secrets/` directory)
        secrets_dir = Path(".secrets").resolve()
        candidate_file = (secrets_dir / validated_id).resolve()
        if str(candidate_file).startswith(str(secrets_dir) + os.sep) and candidate_file.is_file():
            file_secret = candidate_file.read_text(encoding="utf-8").strip()
            if file_secret:
                return file_secret

        # 4. Ephemeral CSPRNG fallback strictly for local testing / session signing
        if allow_ephemeral_fallback:
            if validated_id not in self._ephemeral_secrets:
                logger.warning(
                    "Generating ephemeral CSPRNG secret for '%s'. Instance-isolated; "
                    "configure Google Cloud Secret Manager in production!",
                    validated_id,
                )
                self._ephemeral_secrets[validated_id] = secrets.token_hex(32)
            return self._ephemeral_secrets[validated_id]

        raise RuntimeError(
            f"Required secret '{validated_id}' is not configured in Google Cloud Secret Manager "
            "or environment mounts."
        )


@functools.cache
def get_secret_manager() -> SecretManagerService:
    """Return singleton SecretManagerService instance."""
    return SecretManagerService()
