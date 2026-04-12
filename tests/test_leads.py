"""Tests for api/leads.py and api/leads_routes.py.

Covers the LeadStore persistence layer (CRUD, dedupe, hashing, stats) and
the FastAPI routes (public POST, honeypot, rate limit, admin auth).
"""

import hashlib
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import api.leads as leads_module
from api.leads import LeadStore, DEDUPE_WINDOW_SECONDS
import api.leads_routes as leads_routes_module
from api.leads_routes import router as leads_router, _reset_rate_limiter_for_tests


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def store(tmp_path):
    db_path = str(tmp_path / "leads.db")
    s = LeadStore(db_path)
    yield s
    s.close()


@pytest.fixture
def app_client(tmp_path, monkeypatch):
    """FastAPI app with lead routes mounted and a temp lead store."""
    db_path = str(tmp_path / "leads.db")
    store = LeadStore(db_path)
    monkeypatch.setattr(leads_module, "_lead_store", store)
    # Ensure admin key is set for admin endpoint tests.
    monkeypatch.setenv("ADMIN_API_KEY", "admin-test-key")
    # Prevent SMTP attempts during tests.
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setenv("ADMIN_LEAD_INBOX", "admin@example.com")

    _reset_rate_limiter_for_tests()

    app = FastAPI()
    app.include_router(leads_router)
    client = TestClient(app)
    yield client, store
    store.close()


# ---------------------------------------------------------------------------
# LeadStore: create / read
# ---------------------------------------------------------------------------

def test_create_lead_persists_all_fields(store):
    lead, created = store.create(
        name="Jane Smith",
        email="jane@cityof.gov",
        message="We need a pilot.",
        organization="City of Springfield",
        role="Clerk",
        phone="555-1234",
        intent="demo",
        source_page="/contact.html",
        utm_source="google",
        utm_medium="cpc",
        utm_campaign="launch",
        ip_address="10.0.0.5",
        user_agent="Mozilla/5.0",
    )
    assert created is True
    assert lead.id > 0
    assert lead.public_id and len(lead.public_id) == 32
    assert lead.name == "Jane Smith"
    assert lead.email == "jane@cityof.gov"
    assert lead.organization == "City of Springfield"
    assert lead.intent == "demo"
    assert lead.utm_source == "google"
    assert lead.status == "new"


def test_create_lead_hashes_ip_and_user_agent(store):
    lead, _ = store.create(
        name="Alice",
        email="alice@example.com",
        message="hi",
        ip_address="192.168.1.1",
        user_agent="curl/8.0",
    )
    expected_ip = hashlib.sha256(b"192.168.1.1").hexdigest()
    expected_ua = hashlib.sha256(b"curl/8.0").hexdigest()
    assert lead.ip_address_hash == expected_ip
    assert lead.user_agent_hash == expected_ua
    # Verify raw values are not stored anywhere on the row.
    raw = store._conn.execute(
        "SELECT ip_address_hash, user_agent_hash FROM leads WHERE id = ?", (lead.id,)
    ).fetchone()
    assert "192.168.1.1" not in (raw["ip_address_hash"] or "")
    assert "curl" not in (raw["user_agent_hash"] or "")


def test_create_lead_requires_name_email_message(store):
    with pytest.raises(ValueError):
        store.create(name="", email="a@b.co", message="hi")
    with pytest.raises(ValueError):
        store.create(name="A", email="notanemail", message="hi")
    with pytest.raises(ValueError):
        store.create(name="A", email="a@b.co", message="   ")


def test_intent_normalization(store):
    lead, _ = store.create(
        name="A", email="a@b.co", message="m", intent="nonprofit-pilot"
    )
    assert lead.intent == "pilot"
    lead2, _ = store.create(
        name="B", email="b@b.co", message="m", intent="partner-strategic"
    )
    assert lead2.intent == "partnership"
    lead3, _ = store.create(
        name="C", email="c@b.co", message="m", intent="unknown-thing"
    )
    assert lead3.intent == "other"


# ---------------------------------------------------------------------------
# LeadStore: dedupe
# ---------------------------------------------------------------------------

def test_email_dedupe_within_24h_appends_message(store):
    t0 = 1_000_000_000.0
    lead1, created1 = store.create(
        name="Jane", email="jane@x.co", message="First inquiry", now=t0
    )
    assert created1 is True

    # Submit again 5 minutes later
    lead2, created2 = store.create(
        name="Jane", email="jane@x.co", message="Forgot to mention timelines", now=t0 + 300
    )
    assert created2 is False
    assert lead2.id == lead1.id
    assert "First inquiry" in lead2.message
    assert "Forgot to mention timelines" in lead2.message
    assert "follow-up" in lead2.message


def test_email_dedupe_after_window_creates_new_record(store):
    t0 = 1_000_000_000.0
    lead1, _ = store.create(name="Jane", email="j@x.co", message="First", now=t0)
    lead2, created2 = store.create(
        name="Jane",
        email="j@x.co",
        message="Second",
        now=t0 + DEDUPE_WINDOW_SECONDS + 1,
    )
    assert created2 is True
    assert lead2.id != lead1.id


def test_duplicates_by_email_returns_all(store):
    t0 = 1_000_000_000.0
    store.create(name="A", email="dup@x.co", message="one", now=t0)
    store.create(
        name="A",
        email="dup@x.co",
        message="two",
        now=t0 + DEDUPE_WINDOW_SECONDS + 10,
    )
    dupes = store.duplicates_by_email("dup@x.co")
    assert len(dupes) == 2


# ---------------------------------------------------------------------------
# LeadStore: list / filter / stats
# ---------------------------------------------------------------------------

def test_list_with_filters(store):
    t0 = 1_000_000_000.0
    # Use distinct timestamps so dedupe never kicks in.
    store.create(name="Jane", email="jane@a.co", message="m", intent="demo", now=t0)
    store.create(
        name="Bob",
        email="bob@b.co",
        message="m",
        intent="pricing",
        organization="Acme Corp",
        now=t0 + DEDUPE_WINDOW_SECONDS * 2,
    )
    store.create(
        name="Carol",
        email="carol@c.co",
        message="m",
        intent="demo",
        now=t0 + DEDUPE_WINDOW_SECONDS * 4,
    )

    assert len(store.list()) == 3
    assert len(store.list(intent="demo")) == 2
    assert len(store.list(intent="pricing")) == 1
    assert len(store.list(search="Acme")) == 1
    assert len(store.list(search="jane")) == 1


def test_count_by_status_and_intent(store):
    store.create(name="A", email="a1@x.co", message="m", intent="demo")
    store.create(name="B", email="a2@x.co", message="m", intent="demo")
    store.create(name="C", email="a3@x.co", message="m", intent="pricing")
    by_status = store.count_by_status()
    by_intent = store.count_by_intent()
    assert by_status["new"] == 3
    assert by_intent["demo"] == 2
    assert by_intent["pricing"] == 1


def test_stats_today_week_month(store):
    now = time.time()
    store.create(name="recent", email="r@x.co", message="m", now=now - 60)
    # 10 days ago (outside day, within month)
    store.create(name="older", email="o@x.co", message="m", now=now - 10 * 86400)
    stats = store.stats(now=now)
    assert stats["total"] == 2
    assert stats["today"] == 1
    assert stats["month"] == 2


# ---------------------------------------------------------------------------
# LeadStore: status + notes
# ---------------------------------------------------------------------------

def test_status_transitions(store):
    lead, _ = store.create(name="A", email="a@x.co", message="m")
    updated = store.update_status(lead.public_id, "contacted")
    assert updated is not None
    assert updated.status == "contacted"

    updated = store.update_status(lead.public_id, "qualified", assigned_to="pat")
    assert updated.status == "qualified"
    assert updated.assigned_to == "pat"

    with pytest.raises(ValueError):
        store.update_status(lead.public_id, "not-a-real-status")


def test_add_note_appends(store):
    lead, _ = store.create(name="A", email="a@x.co", message="m")
    store.add_note(lead.public_id, "called them back", author="pat")
    store.add_note(lead.public_id, "scheduled demo")
    final = store.get(lead.public_id)
    assert "called them back" in final.notes
    assert "scheduled demo" in final.notes
    assert "pat" in final.notes


# ---------------------------------------------------------------------------
# Routes: public POST
# ---------------------------------------------------------------------------

def test_public_post_accepts_without_auth(app_client):
    client, store = app_client
    resp = client.post(
        "/api/v1/leads",
        json={
            "name": "Jane Smith",
            "email": "jane@cityof.gov",
            "message": "Please demo us.",
            "organization": "City of X",
            "intent": "demo",
            "utm_source": "google",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "lead_id" in body
    assert len(body["lead_id"]) == 32  # UUID hex, not sequential int

    # Verify it hit the store with UTM preserved.
    leads = store.list()
    assert len(leads) == 1
    assert leads[0].utm_source == "google"


def test_public_post_validates_required(app_client):
    client, _ = app_client
    resp = client.post(
        "/api/v1/leads",
        json={"name": "", "email": "not-an-email", "message": ""},
    )
    assert resp.status_code == 422


def test_honeypot_drops_submission_silently(app_client):
    client, store = app_client
    resp = client.post(
        "/api/v1/leads",
        json={
            "name": "Bot",
            "email": "bot@spam.co",
            "message": "buy cheap widgets",
            "website": "http://spam.example.com",
        },
    )
    assert resp.status_code == 200
    assert resp.json()["success"] is True
    # Nothing should have been persisted.
    assert store.list() == []


def test_rate_limit_after_5_submissions_per_ip(app_client, monkeypatch):
    client, _ = app_client
    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    headers = {"X-Forwarded-For": "203.0.113.7"}
    ok = 0
    for i in range(6):
        resp = client.post(
            "/api/v1/leads",
            json={
                "name": f"user{i}",
                "email": f"u{i}@x.co",
                "message": "hi",
            },
            headers=headers,
        )
        if resp.status_code == 200:
            ok += 1
        else:
            assert resp.status_code == 429
    assert ok == 5


def test_rate_limit_ignores_spoofed_forwarded_for_by_default(app_client):
    client, _ = app_client
    ok = 0
    for i in range(6):
        resp = client.post(
            "/api/v1/leads",
            json={
                "name": f"user{i}",
                "email": f"u{i}@x.co",
                "message": "hi",
            },
            headers={"X-Forwarded-For": f"203.0.113.{i}"},
        )
        if resp.status_code == 200:
            ok += 1
        else:
            assert resp.status_code == 429
    assert ok == 5


def test_public_post_uses_forwarded_for_only_when_proxy_headers_trusted(app_client, monkeypatch):
    client, store = app_client

    resp = client.post(
        "/api/v1/leads",
        json={"name": "Jane", "email": "jane@city.gov", "message": "Please call"},
        headers={"X-Forwarded-For": "203.0.113.44"},
    )
    assert resp.status_code == 200
    lead = store.list()[0]
    direct_ip_hash = hashlib.sha256(b"testclient").hexdigest()
    assert lead.ip_address_hash == direct_ip_hash

    monkeypatch.setenv("TRUST_PROXY_HEADERS", "true")
    resp = client.post(
        "/api/v1/leads",
        json={"name": "Pat", "email": "pat@city.gov", "message": "Need pricing"},
        headers={"X-Forwarded-For": "198.51.100.7"},
    )
    assert resp.status_code == 200
    lead = store.list()[0]
    forwarded_ip_hash = hashlib.sha256(b"198.51.100.7").hexdigest()
    assert lead.ip_address_hash == forwarded_ip_hash


def test_render_admin_email_escapes_user_content(store):
    lead, _ = store.create(
        name="<b>Jane</b>",
        email="jane@city.gov",
        message="<script>alert(1)</script>\nNeed <strong>pricing</strong>",
        organization="City <Admin>",
    )
    rendered = leads_routes_module._render_admin_email(lead)

    assert "<script>alert(1)</script>" not in rendered
    assert "<b>Jane</b>" not in rendered
    assert "City <Admin>" not in rendered
    assert "&lt;script&gt;alert(1)&lt;/script&gt;<br>Need &lt;strong&gt;pricing&lt;/strong&gt;" in rendered
    assert "&lt;b&gt;Jane&lt;/b&gt;" in rendered
    assert "City &lt;Admin&gt;" in rendered


# ---------------------------------------------------------------------------
# Routes: admin
# ---------------------------------------------------------------------------

ADMIN_HEADERS = {"X-API-Key": "admin-test-key"}


def test_admin_list_requires_auth(app_client):
    client, _ = app_client
    # No admin key
    resp = client.get("/api/v1/admin/leads")
    assert resp.status_code == 403

    # Wrong admin key
    resp = client.get("/api/v1/admin/leads", headers={"X-API-Key": "wrong"})
    assert resp.status_code == 403


def test_admin_list_returns_leads(app_client):
    client, store = app_client
    store.create(name="A", email="a@x.co", message="m", intent="demo")
    store.create(name="B", email="b@x.co", message="m", intent="pricing")
    resp = client.get("/api/v1/admin/leads", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["leads"]) == 2

    # Filter by intent
    resp2 = client.get("/api/v1/admin/leads?intent=demo", headers=ADMIN_HEADERS)
    assert len(resp2.json()["leads"]) == 1


def test_admin_get_update_status_and_note(app_client):
    client, store = app_client
    lead, _ = store.create(name="A", email="a@x.co", message="m")

    # Get
    resp = client.get(f"/api/v1/admin/leads/{lead.public_id}", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    assert resp.json()["name"] == "A"
    assert "ip_address_hash" in resp.json()

    # Update status
    resp = client.patch(
        f"/api/v1/admin/leads/{lead.public_id}/status",
        headers=ADMIN_HEADERS,
        json={"status": "contacted", "assigned_to": "pat"},
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "contacted"
    assert resp.json()["assigned_to"] == "pat"

    # Invalid status
    resp = client.patch(
        f"/api/v1/admin/leads/{lead.public_id}/status",
        headers=ADMIN_HEADERS,
        json={"status": "bogus"},
    )
    assert resp.status_code == 422

    # Add note
    resp = client.post(
        f"/api/v1/admin/leads/{lead.public_id}/notes",
        headers=ADMIN_HEADERS,
        json={"note": "followed up", "author": "pat"},
    )
    assert resp.status_code == 200
    assert "followed up" in resp.json()["notes"]


def test_admin_stats_endpoint(app_client):
    client, store = app_client
    store.create(name="A", email="a@x.co", message="m", intent="demo")
    resp = client.get("/api/v1/admin/leads/stats", headers=ADMIN_HEADERS)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["by_intent"]["demo"] == 1
    assert data["by_status"]["new"] == 1
