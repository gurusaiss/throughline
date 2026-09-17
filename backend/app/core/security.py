"""Authentication, credential encryption and tenant scoping primitives.

Choices and why:

* **Argon2id** for passwords — memory-hard, the current password-hashing recommendation.
* **Fernet** for provider credentials — authenticated symmetric encryption, so a tampered ciphertext fails
  loudly rather than decrypting to garbage. Provider keys must never be recoverable from a database dump
  alone (rule 28).
* **API keys are stored hashed.** The plaintext is shown exactly once. A stolen database yields no usable
  key. We use SHA-256 rather than Argon2 here deliberately: an API key is 256 bits of our own randomness,
  not a human-chosen password, so it is not brute-forceable and the hash must stay fast enough to run on
  every request.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from cryptography.fernet import Fernet, InvalidToken

from app.core.config import Settings
from app.core.errors import AuthenticationError

_hasher = PasswordHasher()

API_KEY_PREFIX = "tl_sk_"
MIN_PASSWORD_LENGTH = 10


# --------------------------------------------------------------------------------------
# Passwords
# --------------------------------------------------------------------------------------
def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


# --------------------------------------------------------------------------------------
# JWT
# --------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TokenPayload:
    user_id: str
    org_id: str
    role: str
    token_type: str = "access"


def create_token(
    payload: TokenPayload, settings: Settings, *, expires_delta: timedelta | None = None
) -> str:
    now = datetime.now(UTC)
    ttl = expires_delta or (
        timedelta(minutes=settings.access_token_ttl_minutes)
        if payload.token_type == "access"
        else timedelta(days=settings.refresh_token_ttl_days)
    )
    claims: dict[str, Any] = {
        "sub": payload.user_id,
        "org": payload.org_id,
        "role": payload.role,
        "type": payload.token_type,
        "iat": int(now.timestamp()),
        "exp": int((now + ttl).timestamp()),
        "jti": secrets.token_urlsafe(16),
    }
    return jwt.encode(claims, settings.secret_key, algorithm="HS256")


def decode_token(token: str, settings: Settings, *, expected_type: str = "access") -> TokenPayload:
    try:
        claims = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationError("Session expired. Please sign in again.") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationError("Invalid authentication token.") from exc

    if claims.get("type") != expected_type:
        # A refresh token presented as an access token is an attack, not a mistake.
        raise AuthenticationError("Invalid token type.")
    return TokenPayload(
        user_id=claims["sub"],
        org_id=claims.get("org", ""),
        role=claims.get("role", "member"),
        token_type=claims.get("type", "access"),
    )


# --------------------------------------------------------------------------------------
# API keys
# --------------------------------------------------------------------------------------
def generate_api_key() -> tuple[str, str, str]:
    """Return (plaintext, prefix, hash). Plaintext is never persisted."""
    raw = secrets.token_urlsafe(32)
    plaintext = f"{API_KEY_PREFIX}{raw}"
    return plaintext, plaintext[: len(API_KEY_PREFIX) + 6], hash_api_key(plaintext)


def hash_api_key(plaintext: str) -> str:
    return hashlib.sha256(plaintext.encode()).hexdigest()


def verify_api_key(plaintext: str, stored_hash: str) -> bool:
    # Constant-time comparison: a timing oracle on key lookup is a real, cheap attack.
    return hmac.compare_digest(hash_api_key(plaintext), stored_hash)


# --------------------------------------------------------------------------------------
# Provider credential encryption
# --------------------------------------------------------------------------------------
class CredentialCipher:
    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError("ENCRYPTION_KEY is required to store provider credentials")
        self._fernet = Fernet(key.encode() if isinstance(key, str) else key)

    @staticmethod
    def generate_key() -> str:
        return Fernet.generate_key().decode()

    def encrypt(self, plaintext: str) -> str:
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except InvalidToken as exc:
            # Wrong key or tampered ciphertext. Surfaced explicitly rather than returning garbage that
            # would later fail as a confusing provider auth error.
            raise AuthenticationError("Stored provider credential could not be decrypted.") from exc


# --------------------------------------------------------------------------------------
# Tenant scope
# --------------------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class TenantScope:
    """Threaded through every request. The repository layer requires one (ARCHITECTURE §7)."""

    org_id: str
    user_id: str | None = None
    role: str = "member"
    via_api_key: bool = False

    @property
    def can_write(self) -> bool:
        return self.role in ("owner", "admin", "member")

    @property
    def can_administer(self) -> bool:
        return self.role in ("owner", "admin")

    def require_write(self) -> None:
        from app.core.errors import AuthorizationError

        if not self.can_write:
            raise AuthorizationError("Your role does not permit this action.")

    def require_admin(self) -> None:
        from app.core.errors import AuthorizationError

        if not self.can_administer:
            raise AuthorizationError("This action requires an admin or owner role.")


def redact(text: str, *, keep: int = 0) -> str:
    """Redact user content for logs when raw prompt logging is disabled (rule 28)."""
    if not text:
        return ""
    if keep <= 0:
        return f"[redacted {len(text)} chars]"
    return f"{text[:keep]}… [redacted {max(0, len(text) - keep)} chars]"
