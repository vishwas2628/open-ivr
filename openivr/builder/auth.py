"""Builder authentication: bcrypt passwords + signed session cookies.

Credentials live in ``config.yaml`` (mode 0600, never in the repository):

    builder:
      username: openivr
      password_hash: $2b$12$...
      session_ttl: 43200      # seconds a login stays valid (12h)
      session_secret: <random>

On first start without credentials the builder generates a password, prints it
once to the console and stores only the hash. ``OPENIVR_BUILDER_TOKEN`` still
works as a header/query token for scripts and tests.
"""

from __future__ import annotations

import logging
import os
import re
import secrets
import socket
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import bcrypt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from ..config import Config, set_config_value

log = logging.getLogger("openivr.builder")

SESSION_COOKIE = "openivr_session"
LOGIN_PATH = "/login"
UNPROTECTED_PATHS = frozenset({LOGIN_PATH, "/static", "/favicon.ico", "/healthz"})

DEFAULT_TTL = 7200  # plan: 120 minutes, configurable via builder.session_ttl
MIN_PASSWORD_LENGTH = 10
BCRYPT_ROUNDS = 12


class AuthError(Exception):
    """Raised for bad credentials or unusable auth configuration."""


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(BCRYPT_ROUNDS)).decode()


def verify_password(password: str, password_hash: str) -> bool:
    if not password or not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def generate_password(length: int = 20) -> str:
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))


@dataclass
class BuilderAuth:
    """Auth state for one builder process."""

    root: Path
    company_name: str = ""
    username: str = "admin"
    password_hash: str = ""
    session_ttl: int = DEFAULT_TTL
    session_secret: str = ""
    generated_password: str = ""
    enforced: bool = False

    @property
    def configured(self) -> bool:
        return bool(self.password_hash and self.username)

    @property
    def local_only(self) -> bool:
        """No credentials at all: fall back to the localhost-only check."""
        return not self.configured and not self.token

    @property
    def token(self) -> str:
        return os.environ.get("OPENIVR_BUILDER_TOKEN", "")

    def _serializer(self) -> URLSafeTimedSerializer:
        return URLSafeTimedSerializer(self.session_secret, salt="openivr-builder")

    def check(self, username: str, password: str) -> bool:
        if not self.configured:
            return False
        if not secrets.compare_digest(str(username or ""), self.username):
            return False
        return verify_password(str(password or ""), self.password_hash)

    def issue(self, username: str | None = None) -> str:
        return self._serializer().dumps({"u": username or self.username})

    def read(self, cookie: str | None) -> str | None:
        """Return the username behind a valid, unexpired cookie (or None)."""
        if not cookie or not self.session_secret:
            return None
        try:
            data = self._serializer().loads(cookie, max_age=self.session_ttl)
        except (BadSignature, SignatureExpired):
            return None
        if not isinstance(data, dict):
            return None
        user = data.get("u")
        if self.configured and not secrets.compare_digest(str(user or ""), self.username):
            return None
        return str(user or self.username)


def _ensure(cfg_value: Any, default: str) -> str:
    text = str(cfg_value or "").strip()
    return text or default


def slugify_company(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")
    return slug[:24]


def load_auth(
    root: Path,
    cfg: Config,
    *,
    provision: bool = True,
    company_name: str | None = None,
) -> BuilderAuth:
    """Build the auth state from config.yaml, provisioning it when empty."""
    builder = getattr(cfg, "builder", None)
    company = (company_name or str(getattr(builder, "company_name", "") or "")).strip()
    if not company:
        company = os.environ.get("OPENIVR_COMPANY", "").strip() or socket.gethostname()
    username = _ensure(getattr(builder, "username", ""), "")
    if not username:
        # plan: auto-generate the username from the company name
        username = slugify_company(company) or "admin"
    password_hash = str(getattr(builder, "password_hash", "") or "").strip()
    try:
        ttl = int(getattr(builder, "session_ttl", 0) or 0)
    except (TypeError, ValueError):
        ttl = 0
    secret = _ensure(getattr(builder, "session_secret", ""), "")

    auth = BuilderAuth(
        root=root,
        company_name=company,
        username=username,
        password_hash=password_hash,
        session_ttl=ttl if ttl > 0 else DEFAULT_TTL,
        session_secret=secret or os.environ.get("OPENIVR_SESSION_SECRET", ""),
    )

    if not auth.session_secret:
        auth.session_secret = secrets.token_urlsafe(32)
    if provision and not auth.configured:
        _provision(root, auth)
    return auth


def _provision(root: Path, auth: BuilderAuth) -> None:
    """First run: random password + hash + session secret, all into config.yaml."""
    auth.generated_password = generate_password(16)
    try:
        auth.password_hash = hash_password(auth.generated_password)
    except AuthError as exc:  # pragma: no cover - generate_password always fits
        log.error("could not hash the generated password: %s", exc)
        return
    _persist(root, auth)
    log.warning(
        "builder credentials created for %r (user %r); one-time password: %s - "
        "save this now, only its bcrypt hash is stored",
        auth.company_name,
        auth.username,
        auth.generated_password,
    )


def _persist(root: Path, auth: BuilderAuth) -> None:
    writes = {
        "builder.company_name": auth.company_name,
        "builder.username": auth.username,
        "builder.password_hash": auth.password_hash,
        "builder.session_secret": auth.session_secret,
        "builder.session_ttl": str(auth.session_ttl),
    }
    for key, value in writes.items():
        try:
            set_config_value(root, key, value)
        except Exception as exc:  # noqa: BLE001 - never break startup on this
            log.error("could not store %s in config.yaml: %s", key, exc)


def save_credentials(
    root: Path,
    auth: BuilderAuth,
    *,
    username: str,
    password: str | None = None,
    password_hash: str | None = None,
) -> None:
    """Update the stored credentials (Settings page)."""
    user = (username or "").strip()
    if not user:
        raise AuthError("username must not be empty")
    auth.username = user
    if password_hash:
        auth.password_hash = password_hash
    elif password is not None:
        auth.password_hash = hash_password(password)
    if not auth.configured:
        raise AuthError("a password hash is required")
    _persist(root, auth)