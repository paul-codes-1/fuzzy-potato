"""SSO (SAML 2.0) support for enterprise multi-tenant authentication.

Provides SSOManager for SAML flow orchestration, SSOConfigStore for per-tenant
IdP configuration in SQLite, UserStore for user lifecycle, and JWT session
token generation after successful SAML assertion.

Coexists with API key auth: API keys for programmatic access, JWT for
browser-based SSO sessions.
"""

import hashlib
import logging
import os
import secrets
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

import jwt
from onelogin.saml2.auth import OneLogin_Saml2_Auth

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JWT_ALGORITHM = "HS256"
JWT_EXPIRY_SECONDS = 8 * 3600  # 8 hours
JWT_REFRESH_WINDOW_SECONDS = 30 * 60  # refreshable within last 30 min


class UserRole(str, Enum):
    admin = "admin"
    analyst = "analyst"
    viewer = "viewer"


# Permissions per role
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"query", "export", "alerts", "settings", "manage_users", "sso_config"},
    "analyst": {"query", "export", "alerts"},
    "viewer": {"query"},
}


def role_has_permission(role: str, permission: str) -> bool:
    return permission in ROLE_PERMISSIONS.get(role, set())


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class SSOConfig:
    tenant_id: str
    idp_entity_id: str
    idp_sso_url: str
    idp_x509_cert: str
    sp_entity_id: str
    enabled: bool
    created_at: str
    updated_at: str
    idp_slo_url: str = ""
    default_role: str = "viewer"
    allowed_domains: str = ""  # comma-separated email domains


@dataclass
class SSOUser:
    id: str
    tenant_id: str
    email: str
    name: str
    role: str
    last_login: Optional[str]
    created_at: str


# ---------------------------------------------------------------------------
# SSOConfigStore -- per-tenant SAML IdP configuration
# ---------------------------------------------------------------------------

_SSO_CONFIG_TABLE = """
CREATE TABLE IF NOT EXISTS sso_config (
    tenant_id TEXT PRIMARY KEY,
    idp_entity_id TEXT NOT NULL,
    idp_sso_url TEXT NOT NULL,
    idp_x509_cert TEXT NOT NULL,
    sp_entity_id TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 0,
    idp_slo_url TEXT NOT NULL DEFAULT '',
    default_role TEXT NOT NULL DEFAULT 'viewer',
    allowed_domains TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class SSOConfigStore:
    """SQLite-backed storage for per-tenant SSO configuration."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_SSO_CONFIG_TABLE)
        self._conn.commit()

    def _row_to_config(self, row: sqlite3.Row) -> SSOConfig:
        d = dict(row)
        d["enabled"] = bool(d.get("enabled", 0))
        return SSOConfig(**d)

    def get(self, tenant_id: str) -> Optional[SSOConfig]:
        row = self._conn.execute(
            "SELECT * FROM sso_config WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        return self._row_to_config(row) if row else None

    def upsert(self, tenant_id: str, data: dict) -> SSOConfig:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get(tenant_id)

        if existing:
            sets = []
            vals = []
            for key in ("idp_entity_id", "idp_sso_url", "idp_x509_cert",
                        "sp_entity_id", "enabled", "idp_slo_url",
                        "default_role", "allowed_domains"):
                if key in data:
                    sets.append(f"{key} = ?")
                    val = data[key]
                    if key == "enabled":
                        val = 1 if val else 0
                    vals.append(val)
            sets.append("updated_at = ?")
            vals.append(now)
            vals.append(tenant_id)
            self._conn.execute(
                f"UPDATE sso_config SET {', '.join(sets)} WHERE tenant_id = ?",
                vals,
            )
        else:
            self._conn.execute(
                "INSERT INTO sso_config "
                "(tenant_id, idp_entity_id, idp_sso_url, idp_x509_cert, "
                " sp_entity_id, enabled, idp_slo_url, default_role, "
                " allowed_domains, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    tenant_id,
                    data.get("idp_entity_id", ""),
                    data.get("idp_sso_url", ""),
                    data.get("idp_x509_cert", ""),
                    data.get("sp_entity_id", ""),
                    1 if data.get("enabled") else 0,
                    data.get("idp_slo_url", ""),
                    data.get("default_role", "viewer"),
                    data.get("allowed_domains", ""),
                    now,
                    now,
                ),
            )
        self._conn.commit()
        return self.get(tenant_id)  # type: ignore[return-value]

    def delete(self, tenant_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM sso_config WHERE tenant_id = ?", (tenant_id,)
        )
        self._conn.commit()
        return cur.rowcount > 0

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# UserStore -- SSO users per tenant
# ---------------------------------------------------------------------------

_USERS_TABLE = """
CREATE TABLE IF NOT EXISTS sso_users (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    email TEXT NOT NULL,
    name TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'viewer',
    last_login TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(tenant_id, email)
);
"""


class UserStore:
    """SQLite-backed user store for SSO-authenticated users."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_USERS_TABLE)
        self._conn.commit()

    def _row_to_user(self, row: sqlite3.Row) -> SSOUser:
        return SSOUser(**dict(row))

    def get_by_id(self, user_id: str) -> Optional[SSOUser]:
        row = self._conn.execute(
            "SELECT * FROM sso_users WHERE id = ?", (user_id,)
        ).fetchone()
        return self._row_to_user(row) if row else None

    def get_by_email(self, tenant_id: str, email: str) -> Optional[SSOUser]:
        row = self._conn.execute(
            "SELECT * FROM sso_users WHERE tenant_id = ? AND email = ?",
            (tenant_id, email.lower()),
        ).fetchone()
        return self._row_to_user(row) if row else None

    def list_by_tenant(self, tenant_id: str) -> list[SSOUser]:
        rows = self._conn.execute(
            "SELECT * FROM sso_users WHERE tenant_id = ? ORDER BY created_at DESC",
            (tenant_id,),
        ).fetchall()
        return [self._row_to_user(r) for r in rows]

    def upsert_from_saml(
        self,
        tenant_id: str,
        email: str,
        name: str,
        default_role: str = "viewer",
    ) -> SSOUser:
        """Create or update a user from SAML assertion data.

        On first login, user is created with default_role. On subsequent
        logins, only last_login is updated (preserving admin-assigned role).
        """
        email = email.lower().strip()
        now = datetime.now(timezone.utc).isoformat()
        existing = self.get_by_email(tenant_id, email)

        if existing:
            self._conn.execute(
                "UPDATE sso_users SET name = ?, last_login = ? WHERE id = ?",
                (name, now, existing.id),
            )
            self._conn.commit()
            return self.get_by_id(existing.id)  # type: ignore[return-value]

        # Generate deterministic-ish user id from tenant + email
        user_id = hashlib.sha256(
            f"{tenant_id}:{email}".encode()
        ).hexdigest()[:16]
        self._conn.execute(
            "INSERT INTO sso_users (id, tenant_id, email, name, role, last_login, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, tenant_id, email, name, default_role, now, now),
        )
        self._conn.commit()
        return self.get_by_id(user_id)  # type: ignore[return-value]

    def update_role(self, user_id: str, role: str) -> Optional[SSOUser]:
        if role not in UserRole.__members__:
            raise ValueError(f"Invalid role '{role}'. Must be one of: {', '.join(UserRole.__members__)}")
        cur = self._conn.execute(
            "UPDATE sso_users SET role = ? WHERE id = ?", (role, user_id)
        )
        self._conn.commit()
        if cur.rowcount == 0:
            return None
        return self.get_by_id(user_id)

    def delete(self, user_id: str) -> bool:
        cur = self._conn.execute("DELETE FROM sso_users WHERE id = ?", (user_id,))
        self._conn.commit()
        return cur.rowcount > 0

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# JWT session management
# ---------------------------------------------------------------------------

def _get_jwt_secret() -> str:
    """Read JWT_SECRET from environment. Falls back to a generated secret
    (logged as a warning -- not suitable for multi-instance deployments).
    """
    secret = os.environ.get("JWT_SECRET")
    if not secret:
        logger.warning(
            "JWT_SECRET not set -- generating ephemeral secret. "
            "Sessions will not survive restarts. Set JWT_SECRET in .env for production."
        )
        # Generate and cache for process lifetime
        secret = secrets.token_urlsafe(64)
        os.environ["JWT_SECRET"] = secret
    return secret


def create_session_token(user: SSOUser, tenant_id: str) -> str:
    """Create a signed JWT for a browser session after successful SAML auth."""
    now = int(time.time())
    payload = {
        "sub": user.id,
        "email": user.email,
        "name": user.name,
        "role": user.role,
        "tenant_id": tenant_id,
        "iat": now,
        "exp": now + JWT_EXPIRY_SECONDS,
    }
    return jwt.encode(payload, _get_jwt_secret(), algorithm=JWT_ALGORITHM)


def verify_session_token(token: str) -> dict:
    """Verify and decode a JWT session token. Raises jwt.InvalidTokenError on failure."""
    return jwt.decode(token, _get_jwt_secret(), algorithms=[JWT_ALGORITHM])


# ---------------------------------------------------------------------------
# SSOManager -- orchestrates SAML flows
# ---------------------------------------------------------------------------

class SSOManager:
    """Orchestrates SAML 2.0 authentication flows.

    Uses python3-saml (onelogin/python3-saml) for SAML protocol handling.
    Each tenant has its own IdP configuration stored in SSOConfigStore.
    """

    def __init__(self, config_store: SSOConfigStore, user_store: UserStore):
        self._config_store = config_store
        self._user_store = user_store

    @property
    def config_store(self) -> SSOConfigStore:
        return self._config_store

    @property
    def user_store(self) -> UserStore:
        return self._user_store

    def get_config(self, tenant_id: str) -> Optional[SSOConfig]:
        return self._config_store.get(tenant_id)

    def _build_saml_settings(
        self, config: SSOConfig, request_data: dict
    ) -> dict:
        """Build python3-saml settings dict from tenant SSO config."""
        sp_base_url = os.environ.get("SP_BASE_URL", "https://app.civiclens.ai")

        settings: dict[str, Any] = {
            "strict": True,
            "debug": os.environ.get("SAML_DEBUG", "false").lower() == "true",
            "sp": {
                "entityId": config.sp_entity_id,
                "assertionConsumerService": {
                    "url": f"{sp_base_url}/api/v1/sso/acs",
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-POST",
                },
                "singleLogoutService": {
                    "url": f"{sp_base_url}/api/v1/sso/logout",
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                },
                "NameIDFormat": "urn:oasis:names:tc:SAML:1.1:nameid-format:emailAddress",
            },
            "idp": {
                "entityId": config.idp_entity_id,
                "singleSignOnService": {
                    "url": config.idp_sso_url,
                    "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
                },
                "x509cert": config.idp_x509_cert,
            },
            "security": {
                "authnRequestsSigned": False,
                "wantAssertionsSigned": True,
                "wantNameIdEncrypted": False,
                "signMetadata": False,
            },
        }

        if config.idp_slo_url:
            settings["idp"]["singleLogoutService"] = {
                "url": config.idp_slo_url,
                "binding": "urn:oasis:names:tc:SAML:2.0:bindings:HTTP-Redirect",
            }

        return settings

    def _prepare_request_data(self, request_info: dict) -> dict:
        """Convert FastAPI request info into the format python3-saml expects."""
        return {
            "https": "on" if request_info.get("https") else "off",
            "http_host": request_info.get("http_host", ""),
            "script_name": request_info.get("script_name", ""),
            "get_data": request_info.get("get_data", {}),
            "post_data": request_info.get("post_data", {}),
        }

    def initiate_login(self, tenant_id: str, request_info: dict) -> str:
        """Generate SAML AuthnRequest and return the IdP redirect URL.

        Raises ValueError if SSO is not configured or not enabled for tenant.
        """
        config = self._config_store.get(tenant_id)
        if not config:
            raise ValueError(f"SSO not configured for tenant '{tenant_id}'")
        if not config.enabled:
            raise ValueError(f"SSO is disabled for tenant '{tenant_id}'")

        req_data = self._prepare_request_data(request_info)
        saml_settings = self._build_saml_settings(config, req_data)
        auth = OneLogin_Saml2_Auth(req_data, saml_settings)

        return auth.login()

    def process_acs(
        self, tenant_id: str, request_info: dict
    ) -> tuple[SSOUser, str]:
        """Process SAML Assertion Consumer Service callback.

        Returns (user, jwt_token) on success.
        Raises ValueError on SAML validation failure.
        """
        config = self._config_store.get(tenant_id)
        if not config:
            raise ValueError(f"SSO not configured for tenant '{tenant_id}'")
        if not config.enabled:
            raise ValueError(f"SSO is disabled for tenant '{tenant_id}'")

        req_data = self._prepare_request_data(request_info)
        saml_settings = self._build_saml_settings(config, req_data)
        auth = OneLogin_Saml2_Auth(req_data, saml_settings)
        auth.process_response()

        errors = auth.get_errors()
        if errors:
            error_reason = auth.get_last_error_reason()
            logger.warning(
                "saml_acs_errors",
                extra={
                    "tenant_id": tenant_id,
                    "errors": errors,
                    "reason": error_reason,
                },
            )
            raise ValueError(f"SAML validation failed: {', '.join(errors)}. {error_reason or ''}")

        if not auth.is_authenticated():
            raise ValueError("SAML authentication failed: user not authenticated")

        # Extract user attributes from SAML assertion
        attrs = auth.get_attributes()
        name_id = auth.get_nameid()

        email = (
            _first_attr(attrs, "email")
            or _first_attr(attrs, "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/emailaddress")
            or name_id
        )
        name = (
            _first_attr(attrs, "displayName")
            or _first_attr(attrs, "http://schemas.xmlsoap.org/ws/2005/05/identity/claims/name")
            or _first_attr(attrs, "firstName", "")
            + " "
            + _first_attr(attrs, "lastName", "")
        ).strip() or email

        if not email:
            raise ValueError("SAML assertion missing email attribute")

        # Validate email domain if restrictions are configured
        if config.allowed_domains:
            allowed = {d.strip().lower() for d in config.allowed_domains.split(",") if d.strip()}
            domain = email.split("@")[-1].lower()
            if allowed and domain not in allowed:
                raise ValueError(
                    f"Email domain '{domain}' is not allowed for this tenant. "
                    f"Allowed: {', '.join(sorted(allowed))}"
                )

        # Upsert user (create on first login, update last_login on subsequent)
        user = self._user_store.upsert_from_saml(
            tenant_id=tenant_id,
            email=email,
            name=name,
            default_role=config.default_role,
        )

        token = create_session_token(user, tenant_id)

        logger.info(
            "sso_login_success",
            extra={
                "tenant_id": tenant_id,
                "user_id": user.id,
                "email": user.email,
            },
        )

        return user, token

    def get_sp_metadata(self, tenant_id: str, request_info: dict) -> str:
        """Generate SP metadata XML for IdP configuration.

        Raises ValueError if SSO is not configured for tenant.
        """
        config = self._config_store.get(tenant_id)
        if not config:
            raise ValueError(f"SSO not configured for tenant '{tenant_id}'")

        req_data = self._prepare_request_data(request_info)
        saml_settings = self._build_saml_settings(config, req_data)
        auth = OneLogin_Saml2_Auth(req_data, saml_settings)
        metadata = auth.get_settings().get_sp_metadata()

        errors = auth.get_settings().validate_metadata(metadata)
        if errors:
            raise ValueError(f"Invalid SP metadata: {', '.join(errors)}")

        return metadata

    def initiate_logout(self, tenant_id: str, request_info: dict, name_id: Optional[str] = None) -> str:
        """Generate SAML LogoutRequest and return the IdP redirect URL.

        Returns empty string if SLO is not configured.
        """
        config = self._config_store.get(tenant_id)
        if not config or not config.idp_slo_url:
            return ""

        req_data = self._prepare_request_data(request_info)
        saml_settings = self._build_saml_settings(config, req_data)
        auth = OneLogin_Saml2_Auth(req_data, saml_settings)

        return auth.logout(name_id=name_id)


# ---------------------------------------------------------------------------
# Module singletons
# ---------------------------------------------------------------------------

_sso_config_store: Optional[SSOConfigStore] = None
_user_store: Optional[UserStore] = None
_sso_manager: Optional[SSOManager] = None


def init_sso(output_dir: str) -> SSOManager:
    """Initialize SSO stores and manager. Call once at startup."""
    global _sso_config_store, _user_store, _sso_manager
    db_path = os.path.join(output_dir, "sso.db")
    _sso_config_store = SSOConfigStore(db_path)
    _user_store = UserStore(db_path)
    _sso_manager = SSOManager(_sso_config_store, _user_store)
    logger.info("sso_initialized", extra={"db_path": db_path})
    return _sso_manager


def get_sso_manager() -> SSOManager:
    if _sso_manager is None:
        raise RuntimeError("SSO not initialized -- call init_sso() first")
    return _sso_manager


def get_user_store() -> UserStore:
    if _user_store is None:
        raise RuntimeError("SSO not initialized -- call init_sso() first")
    return _user_store


def get_sso_config_store() -> SSOConfigStore:
    if _sso_config_store is None:
        raise RuntimeError("SSO not initialized -- call init_sso() first")
    return _sso_config_store


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _first_attr(attrs: dict, key: str, default: str = "") -> str:
    """Get first value from SAML attribute dict (values are always lists)."""
    val = attrs.get(key, [])
    if isinstance(val, list) and val:
        return str(val[0])
    return default
