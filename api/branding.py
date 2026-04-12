"""Per-tenant branding configuration store (SQLite-backed)."""

import os
import re
import sqlite3
from dataclasses import asdict, dataclass
from typing import Optional
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Default branding values
# ---------------------------------------------------------------------------

DEFAULTS = {
    "display_name": "CivicLens",
    "logo_url": "",
    "primary_color": "#1a56db",
    "secondary_color": "#1e293b",
    "accent_color": "#f59e0b",
    "favicon_url": "",
    "custom_css": "",
    "welcome_message": "Search and explore your local government meetings.",
    "footer_text": "",
    "support_email": "",
}

_HEX_COLOR_RE = re.compile(r"^#(?:[0-9a-fA-F]{3}){1,2}$")


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def validate_hex_color(value: str, field_name: str) -> str:
    """Validate that a string is a valid hex color (#RGB or #RRGGBB)."""
    value = value.strip()
    if not value:
        return value
    if not _HEX_COLOR_RE.match(value):
        raise ValueError(f"{field_name} must be a valid hex color (e.g. #1a56db), got '{value}'")
    return value


def validate_url(value: str, field_name: str) -> str:
    """Validate that a URL uses HTTPS (or is empty)."""
    value = value.strip()
    if not value:
        return value
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise ValueError(f"{field_name} must use HTTPS, got '{value}'")
    if not parsed.netloc:
        raise ValueError(f"{field_name} is not a valid URL: '{value}'")
    return value


# ---------------------------------------------------------------------------
# Branding config dataclass
# ---------------------------------------------------------------------------

@dataclass
class BrandingConfig:
    tenant_id: str
    display_name: str = DEFAULTS["display_name"]
    logo_url: str = DEFAULTS["logo_url"]
    primary_color: str = DEFAULTS["primary_color"]
    secondary_color: str = DEFAULTS["secondary_color"]
    accent_color: str = DEFAULTS["accent_color"]
    favicon_url: str = DEFAULTS["favicon_url"]
    custom_css: str = DEFAULTS["custom_css"]
    welcome_message: str = DEFAULTS["welcome_message"]
    footer_text: str = DEFAULTS["footer_text"]
    support_email: str = DEFAULTS["support_email"]

    def to_public_dict(self) -> dict:
        """Return branding config without tenant_id (safe for frontend)."""
        d = asdict(self)
        d.pop("tenant_id", None)
        return d


# ---------------------------------------------------------------------------
# Validation for incoming updates
# ---------------------------------------------------------------------------

_COLOR_FIELDS = {"primary_color", "secondary_color", "accent_color"}
_URL_FIELDS = {"logo_url", "favicon_url"}
_ALLOWED_FIELDS = set(DEFAULTS.keys())


def validate_branding_update(data: dict) -> dict:
    """Validate and sanitize a branding update dict. Returns cleaned data.

    Raises ValueError on invalid input.
    """
    cleaned = {}
    for key, value in data.items():
        if key not in _ALLOWED_FIELDS:
            raise ValueError(f"Unknown branding field: '{key}'")
        if not isinstance(value, str):
            raise ValueError(f"Field '{key}' must be a string")

        if key in _COLOR_FIELDS:
            value = validate_hex_color(value, key)
        elif key in _URL_FIELDS:
            value = validate_url(value, key)
        elif key == "support_email" and value:
            # Basic email sanity check
            if "@" not in value or "." not in value.split("@")[-1]:
                raise ValueError(f"Invalid support_email: '{value}'")
        elif key == "custom_css":
            # Cap length to prevent abuse
            if len(value) > 10_000:
                raise ValueError("custom_css must be under 10,000 characters")

        cleaned[key] = value
    return cleaned


# ---------------------------------------------------------------------------
# SQLite branding store
# ---------------------------------------------------------------------------

_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS branding (
    tenant_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL DEFAULT '',
    logo_url TEXT NOT NULL DEFAULT '',
    primary_color TEXT NOT NULL DEFAULT '#1a56db',
    secondary_color TEXT NOT NULL DEFAULT '#1e293b',
    accent_color TEXT NOT NULL DEFAULT '#f59e0b',
    favicon_url TEXT NOT NULL DEFAULT '',
    custom_css TEXT NOT NULL DEFAULT '',
    welcome_message TEXT NOT NULL DEFAULT '',
    footer_text TEXT NOT NULL DEFAULT '',
    support_email TEXT NOT NULL DEFAULT ''
);
"""


class BrandingStore:
    """SQLite-backed store for per-tenant branding configuration."""

    def __init__(self, db_path: str):
        self._db_path = db_path
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(_CREATE_TABLE)
        self._conn.commit()

    def _row_to_config(self, row: sqlite3.Row) -> BrandingConfig:
        return BrandingConfig(**dict(row))

    def get(self, tenant_id: str) -> BrandingConfig:
        """Get branding config for a tenant, returning defaults if none stored."""
        row = self._conn.execute(
            "SELECT * FROM branding WHERE tenant_id = ?", (tenant_id,)
        ).fetchone()
        if row:
            return self._row_to_config(row)
        return BrandingConfig(tenant_id=tenant_id)

    def upsert(self, tenant_id: str, data: dict) -> BrandingConfig:
        """Create or update branding config. ``data`` must already be validated."""
        existing = self.get(tenant_id)
        merged = asdict(existing)
        merged.update(data)
        merged["tenant_id"] = tenant_id

        self._conn.execute(
            """INSERT INTO branding
                (tenant_id, display_name, logo_url, primary_color, secondary_color,
                 accent_color, favicon_url, custom_css, welcome_message, footer_text, support_email)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tenant_id) DO UPDATE SET
                display_name=excluded.display_name,
                logo_url=excluded.logo_url,
                primary_color=excluded.primary_color,
                secondary_color=excluded.secondary_color,
                accent_color=excluded.accent_color,
                favicon_url=excluded.favicon_url,
                custom_css=excluded.custom_css,
                welcome_message=excluded.welcome_message,
                footer_text=excluded.footer_text,
                support_email=excluded.support_email
            """,
            (
                merged["tenant_id"],
                merged["display_name"],
                merged["logo_url"],
                merged["primary_color"],
                merged["secondary_color"],
                merged["accent_color"],
                merged["favicon_url"],
                merged["custom_css"],
                merged["welcome_message"],
                merged["footer_text"],
                merged["support_email"],
            ),
        )
        self._conn.commit()
        return self.get(tenant_id)

    def reset(self, tenant_id: str) -> BrandingConfig:
        """Reset branding to defaults by deleting the row."""
        self._conn.execute("DELETE FROM branding WHERE tenant_id = ?", (tenant_id,))
        self._conn.commit()
        return BrandingConfig(tenant_id=tenant_id)

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_branding_store: Optional[BrandingStore] = None


def init_branding(output_dir: str) -> BrandingStore:
    """Initialize the branding store. Call once at startup."""
    global _branding_store
    db_path = os.path.join(output_dir, "branding.db")
    _branding_store = BrandingStore(db_path)
    return _branding_store


def get_branding_store() -> BrandingStore:
    if _branding_store is None:
        output_dir = os.environ.get("MEETINGS_OUTPUT_DIR", "./meetings_output")
        return init_branding(output_dir)
    return _branding_store
