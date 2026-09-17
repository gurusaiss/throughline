"""Shared error taxonomy.

Provider adapters normalise every vendor failure into one of the `ProviderError` subclasses, so the router
and orchestrator never branch on a vendor-specific error shape (ARCHITECTURE §5).
"""

from __future__ import annotations


class ThroughlineError(Exception):
    """Base for all application errors."""

    status_code = 500
    code = "internal_error"

    def __init__(self, message: str, *, detail: str = "") -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail

    def to_dict(self) -> dict[str, str]:
        out = {"code": self.code, "message": self.message}
        if self.detail:
            out["detail"] = self.detail
        return out


# --- client errors -------------------------------------------------------------------
class ValidationError(ThroughlineError):
    status_code = 422
    code = "validation_error"


class AuthenticationError(ThroughlineError):
    status_code = 401
    code = "authentication_error"


class AuthorizationError(ThroughlineError):
    status_code = 403
    code = "authorization_error"


class NotFoundError(ThroughlineError):
    """Also used for cross-tenant access.

    Returning 404 rather than 403 for another org's resource avoids confirming that the resource exists
    (ARCHITECTURE §7).
    """

    status_code = 404
    code = "not_found"


class RateLimitedError(ThroughlineError):
    status_code = 429
    code = "rate_limited"


class BudgetExceededError(ThroughlineError):
    """Raised pre-flight, before any provider spend (ADR-011)."""

    status_code = 402
    code = "budget_exceeded"

    def __init__(self, message: str, *, estimated_usd: float = 0.0, remaining_usd: float = 0.0) -> None:
        super().__init__(message)
        self.estimated_usd = estimated_usd
        self.remaining_usd = remaining_usd

    def to_dict(self) -> dict[str, str]:
        out = super().to_dict()
        out["estimated_usd"] = f"{self.estimated_usd:.6f}"
        out["remaining_usd"] = f"{self.remaining_usd:.6f}"
        return out


# --- provider errors -----------------------------------------------------------------
class ProviderError(ThroughlineError):
    status_code = 502
    code = "provider_error"

    def __init__(self, message: str, *, provider: str = "", model_id: str = "", detail: str = "") -> None:
        super().__init__(message, detail=detail)
        self.provider = provider
        self.model_id = model_id

    @property
    def retryable(self) -> bool:
        return False


class ProviderTimeout(ProviderError):
    code = "provider_timeout"
    status_code = 504

    @property
    def retryable(self) -> bool:
        return True


class ProviderRateLimited(ProviderError):
    code = "provider_rate_limited"
    status_code = 429

    def __init__(self, message: str, *, retry_after: float | None = None, **kw: str) -> None:
        super().__init__(message, **kw)
        self.retry_after = retry_after

    @property
    def retryable(self) -> bool:
        return True


class ProviderUnavailable(ProviderError):
    code = "provider_unavailable"
    status_code = 503

    @property
    def retryable(self) -> bool:
        return True


class ProviderInvalidRequest(ProviderError):
    """Our fault, not theirs. Never retried — a retry would fail identically."""

    code = "provider_invalid_request"
    status_code = 400


class ProviderAuthError(ProviderError):
    code = "provider_auth_error"
    status_code = 502


class ContentFiltered(ProviderError):
    code = "content_filtered"
    status_code = 422


class ProviderNotConfigured(ProviderError):
    """No credential available for this provider."""

    code = "provider_not_configured"
    status_code = 503


class ModelNotFound(NotFoundError):
    code = "model_not_found"


class NoViableModelError(ThroughlineError):
    """The router could not satisfy the spec's hard requirements with any configured model."""

    status_code = 503
    code = "no_viable_model"
