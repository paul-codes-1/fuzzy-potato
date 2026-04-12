"""Customer health scoring and success system for CivicLens SaaS.

Calculates a 0-100 health score per tenant based on weighted signals,
detects trends, identifies expansion opportunities, and generates
actionable recommendations. Daily snapshots stored in SQLite.
"""

import logging
import os
import sqlite3
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

HEALTH_WEIGHTS = {
    "query_frequency": 0.25,
    "feature_breadth": 0.20,
    "user_engagement": 0.15,
    "data_freshness": 0.15,
    "alert_activity": 0.10,
    "support_signals": 0.10,
    "billing_health": 0.05,
}

ALL_FEATURES = {"chat", "votes", "alerts", "export", "webhook", "slack", "teams"}

CATEGORY_THRESHOLDS = {
    "healthy": 80,
    "at_risk": 50,
    # below 50 -> churning
}

TREND_THRESHOLD = 5  # points difference to count as improving/declining

# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------


@dataclass
class ScoreBreakdown:
    query_frequency: float = 0.0
    feature_breadth: float = 0.0
    user_engagement: float = 0.0
    data_freshness: float = 0.0
    alert_activity: float = 0.0
    support_signals: float = 0.0
    billing_health: float = 0.0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class HealthScore:
    tenant_id: str
    score: float
    category: str  # healthy | at_risk | churning
    trend: str  # improving | stable | declining
    breakdown: ScoreBreakdown
    recommendations: list[str] = field(default_factory=list)
    expansion_signals: list[str] = field(default_factory=list)
    computed_at: str = ""

    def to_dict(self) -> dict:
        return {
            "tenant_id": self.tenant_id,
            "score": round(self.score, 1),
            "category": self.category,
            "trend": self.trend,
            "breakdown": self.breakdown.to_dict(),
            "recommendations": self.recommendations,
            "expansion_signals": self.expansion_signals,
            "computed_at": self.computed_at,
        }


# ---------------------------------------------------------------------------
# SQLite schema for snapshots
# ---------------------------------------------------------------------------

_CREATE_TABLES = """
CREATE TABLE IF NOT EXISTS health_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tenant_id TEXT NOT NULL,
    score REAL NOT NULL,
    category TEXT NOT NULL,
    trend TEXT NOT NULL,
    query_frequency REAL NOT NULL DEFAULT 0,
    feature_breadth REAL NOT NULL DEFAULT 0,
    user_engagement REAL NOT NULL DEFAULT 0,
    data_freshness REAL NOT NULL DEFAULT 0,
    alert_activity REAL NOT NULL DEFAULT 0,
    support_signals REAL NOT NULL DEFAULT 0,
    billing_health REAL NOT NULL DEFAULT 0,
    recommendations TEXT NOT NULL DEFAULT '[]',
    expansion_signals TEXT NOT NULL DEFAULT '[]',
    computed_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_health_tenant ON health_snapshots(tenant_id, computed_at);
CREATE INDEX IF NOT EXISTS idx_health_date ON health_snapshots(computed_at);
CREATE INDEX IF NOT EXISTS idx_health_category ON health_snapshots(category);
"""


# ---------------------------------------------------------------------------
# CustomerHealthScorer
# ---------------------------------------------------------------------------


class CustomerHealthScorer:
    """Computes and persists per-tenant health scores.

    Pulls real data from the analytics, auth, webhook, tracker, export,
    SSO, and Slack/Teams stores that are already initialized in server.py.
    """

    def __init__(self, output_dir: str):
        self._output_dir = output_dir
        db_path = os.path.join(output_dir, "health.db")
        os.makedirs(os.path.dirname(db_path) or ".", exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_CREATE_TABLES)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def score_tenant(self, tenant_id: str) -> HealthScore:
        """Compute the current health score for a single tenant."""
        breakdown = ScoreBreakdown(
            query_frequency=self._score_query_frequency(tenant_id),
            feature_breadth=self._score_feature_breadth(tenant_id),
            user_engagement=self._score_user_engagement(tenant_id),
            data_freshness=self._score_data_freshness(tenant_id),
            alert_activity=self._score_alert_activity(tenant_id),
            support_signals=self._score_support_signals(tenant_id),
            billing_health=self._score_billing_health(tenant_id),
        )

        raw = (
            breakdown.query_frequency * HEALTH_WEIGHTS["query_frequency"]
            + breakdown.feature_breadth * HEALTH_WEIGHTS["feature_breadth"]
            + breakdown.user_engagement * HEALTH_WEIGHTS["user_engagement"]
            + breakdown.data_freshness * HEALTH_WEIGHTS["data_freshness"]
            + breakdown.alert_activity * HEALTH_WEIGHTS["alert_activity"]
            + breakdown.support_signals * HEALTH_WEIGHTS["support_signals"]
            + breakdown.billing_health * HEALTH_WEIGHTS["billing_health"]
        )
        score = max(0.0, min(100.0, raw))
        category = self._categorize(score)
        trend = self._detect_trend(tenant_id, score)
        recommendations = self._generate_recommendations(tenant_id, breakdown)
        expansion = self._detect_expansion(tenant_id, breakdown)

        now = datetime.now(timezone.utc).isoformat()

        return HealthScore(
            tenant_id=tenant_id,
            score=score,
            category=category,
            trend=trend,
            breakdown=breakdown,
            recommendations=recommendations,
            expansion_signals=expansion,
            computed_at=now,
        )

    def score_all_tenants(self) -> list[HealthScore]:
        """Score every registered tenant and persist snapshots."""
        from api.auth import get_tenant_store

        store = get_tenant_store()
        tenants = store.list_all()
        results = []
        for t in tenants:
            try:
                hs = self.score_tenant(t.id)
                self._save_snapshot(hs)
                results.append(hs)
            except Exception:
                logger.exception("Failed to score tenant %s", t.id)
        return results

    def get_at_risk_tenants(self, threshold: int = 50) -> list[HealthScore]:
        """Return all tenants below the given health threshold."""
        all_scores = self.score_all_tenants()
        return [s for s in all_scores if s.score < threshold]

    def get_expansion_opportunities(self) -> list[HealthScore]:
        """Return tenants showing expansion signals."""
        all_scores = self.score_all_tenants()
        return [s for s in all_scores if s.expansion_signals]

    def get_trends(self, tenant_id: Optional[str] = None, days: int = 30) -> list[dict]:
        """Return daily health snapshots for trend visualization."""
        cutoff = datetime.now(timezone.utc).isoformat()[:10]  # today
        # Go back `days` days — SQLite date math
        if tenant_id:
            rows = self._conn.execute(
                "SELECT tenant_id, score, category, trend, computed_at "
                "FROM health_snapshots WHERE tenant_id = ? "
                "ORDER BY computed_at DESC LIMIT ?",
                (tenant_id, days),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT tenant_id, score, category, trend, computed_at "
                "FROM health_snapshots ORDER BY computed_at DESC LIMIT ?",
                (days * 50,),  # rough limit for all tenants
            ).fetchall()
        return [
            {
                "tenant_id": r["tenant_id"],
                "score": r["score"],
                "category": r["category"],
                "trend": r["trend"],
                "computed_at": r["computed_at"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------
    # Signal scoring (each returns 0-100)
    # ------------------------------------------------------------------

    def _score_query_frequency(self, tenant_id: str) -> float:
        """25% weight: compare last 7d query count vs 30d daily average."""
        try:
            from api.analytics import get_analytics_store
            analytics = get_analytics_store()
        except Exception:
            return 50.0  # neutral if analytics unavailable

        now = time.time()
        seven_days_ago = now - 7 * 86400
        thirty_days_ago = now - 30 * 86400

        conn = analytics._conn

        recent = conn.execute(
            "SELECT COUNT(*) as c FROM query_events WHERE tenant_id = ? AND timestamp >= ?",
            (tenant_id, seven_days_ago),
        ).fetchone()["c"]

        historical = conn.execute(
            "SELECT COUNT(*) as c FROM query_events WHERE tenant_id = ? AND timestamp >= ?",
            (tenant_id, thirty_days_ago),
        ).fetchone()["c"]

        if historical == 0:
            # No queries at all — this is a bad sign
            return 10.0

        daily_avg_30d = historical / 30.0
        daily_avg_7d = recent / 7.0

        if daily_avg_30d == 0:
            return 10.0

        ratio = daily_avg_7d / daily_avg_30d

        # ratio 1.0 = stable (80 points), >1.5 = growing (100), <0.3 = declining (20)
        if ratio >= 1.5:
            return 100.0
        elif ratio >= 1.0:
            return 80.0 + (ratio - 1.0) * 40.0  # 80-100
        elif ratio >= 0.5:
            return 40.0 + (ratio - 0.5) * 80.0  # 40-80
        elif ratio >= 0.1:
            return 10.0 + (ratio - 0.1) * 75.0  # 10-40
        else:
            return 10.0

    def _score_feature_breadth(self, tenant_id: str) -> float:
        """20% weight: how many features the tenant is using."""
        features_used = set()

        # Check chat usage (queries via chat endpoint)
        try:
            from api.analytics import get_analytics_store
            analytics = get_analytics_store()
            conn = analytics._conn
            cutoff = time.time() - 30 * 86400

            chat_count = conn.execute(
                "SELECT COUNT(*) as c FROM query_events WHERE tenant_id = ? AND timestamp >= ? AND model IS NOT NULL",
                (tenant_id, cutoff),
            ).fetchone()["c"]
            if chat_count > 0:
                features_used.add("chat")

            # Check for vote-related queries (votes feature)
            vote_queries = conn.execute(
                "SELECT COUNT(*) as c FROM query_events WHERE tenant_id = ? AND timestamp >= ? "
                "AND (question LIKE '%vote%' OR question LIKE '%motion%' OR question LIKE '%ordinance%')",
                (tenant_id, cutoff),
            ).fetchone()["c"]
            if vote_queries > 0:
                features_used.add("votes")
        except Exception:
            pass

        # Check alerts (tracker)
        try:
            from api.tracker import get_tracker_store
            tracker = get_tracker_store()
            alerts = tracker.list_alerts(tenant_id)
            if alerts:
                features_used.add("alerts")
        except Exception:
            pass

        # Check export usage
        try:
            from api.export import get_export_store
            export_store = get_export_store()
            exports = export_store.list_requests(tenant_id, limit=1)
            if exports:
                features_used.add("export")
        except Exception:
            pass

        # Check webhook usage
        try:
            from api.webhooks import get_webhook_store
            wh_store = get_webhook_store()
            webhooks = wh_store.list_webhooks(tenant_id)
            if webhooks:
                features_used.add("webhook")
        except Exception:
            pass

        # Check Slack integration
        try:
            from api.integrations.slack import get_slack_store
            slack_store = get_slack_store()
            configs = slack_store.list_configs(tenant_id)
            if configs:
                features_used.add("slack")
        except Exception:
            pass

        # Check Teams integration
        try:
            from api.integrations.teams import get_teams_store
            teams_store = get_teams_store()
            configs = teams_store.list_configs(tenant_id)
            if configs:
                features_used.add("teams")
        except Exception:
            pass

        count = len(features_used)
        total = len(ALL_FEATURES)

        if count == 0:
            return 15.0
        return min(100.0, (count / total) * 100.0 + 10.0)

    def _score_user_engagement(self, tenant_id: str) -> float:
        """15% weight: unique API keys active + SSO logins."""
        active_keys = 0
        sso_users = 0

        # Count distinct API activity
        try:
            from api.analytics import get_analytics_store
            analytics = get_analytics_store()
            cutoff = time.time() - 30 * 86400

            # Unique query days as a proxy for engagement
            days_active = analytics._conn.execute(
                "SELECT COUNT(DISTINCT date(timestamp, 'unixepoch')) as c "
                "FROM query_events WHERE tenant_id = ? AND timestamp >= ?",
                (tenant_id, cutoff),
            ).fetchone()["c"]
            active_keys = days_active
        except Exception:
            pass

        # Count SSO users
        try:
            from api.sso import get_user_store
            user_store = get_user_store()
            users = user_store.list_users(tenant_id)
            sso_users = len(users) if users else 0
        except Exception:
            pass

        # Score: active on 20+ days = 100, 10 days = 70, SSO users boost
        day_score = min(100.0, (active_keys / 20.0) * 80.0) if active_keys > 0 else 10.0
        user_boost = min(20.0, sso_users * 5.0)
        return min(100.0, day_score + user_boost)

    def _score_data_freshness(self, tenant_id: str) -> float:
        """15% weight: how recently meetings were processed."""
        try:
            from api.analytics import get_analytics_store
            analytics = get_analytics_store()

            row = analytics._conn.execute(
                "SELECT MAX(timestamp) as last_ts FROM meeting_processed_events WHERE tenant_id = ?",
                (tenant_id,),
            ).fetchone()

            if not row or not row["last_ts"]:
                return 20.0

            days_since = (time.time() - row["last_ts"]) / 86400.0

            if days_since <= 1:
                return 100.0
            elif days_since <= 3:
                return 90.0
            elif days_since <= 7:
                return 75.0
            elif days_since <= 14:
                return 55.0
            elif days_since <= 30:
                return 35.0
            else:
                return 15.0
        except Exception:
            return 50.0

    def _score_alert_activity(self, tenant_id: str) -> float:
        """10% weight: alerts created and matches viewed."""
        try:
            from api.tracker import get_tracker_store
            tracker = get_tracker_store()
            alerts = tracker.list_alerts(tenant_id)

            if not alerts:
                return 30.0  # Not using alerts = mildly concerning, not critical

            enabled = sum(1 for a in alerts if a.enabled)
            total = len(alerts)

            # Having active alerts is good
            if enabled == 0:
                return 40.0

            # More alerts = more engaged
            score = min(100.0, 50.0 + enabled * 10.0)

            # Check for recent matches
            for alert in alerts[:5]:  # Check top 5
                try:
                    matches = tracker.list_matches(alert.id, limit=1)
                    if matches:
                        score = min(100.0, score + 5.0)
                except Exception:
                    pass

            return score
        except Exception:
            return 50.0

    def _score_support_signals(self, tenant_id: str) -> float:
        """10% weight: error rate, failed queries (inverse: fewer errors = higher score)."""
        try:
            from api.analytics import get_analytics_store
            analytics = get_analytics_store()
            cutoff = time.time() - 7 * 86400

            total_calls = analytics._conn.execute(
                "SELECT COUNT(*) as c FROM api_call_events WHERE tenant_id = ? AND timestamp >= ?",
                (tenant_id, cutoff),
            ).fetchone()["c"]

            error_calls = analytics._conn.execute(
                "SELECT COUNT(*) as c FROM api_call_events WHERE tenant_id = ? AND timestamp >= ? AND status_code >= 400",
                (tenant_id, cutoff),
            ).fetchone()["c"]

            if total_calls == 0:
                return 70.0  # No calls = neutral

            error_rate = error_calls / total_calls

            # Low error rate = healthy
            if error_rate <= 0.01:
                return 100.0
            elif error_rate <= 0.05:
                return 85.0
            elif error_rate <= 0.10:
                return 65.0
            elif error_rate <= 0.25:
                return 40.0
            else:
                return 15.0
        except Exception:
            return 70.0

    def _score_billing_health(self, tenant_id: str) -> float:
        """5% weight: payment status and plan utilization."""
        try:
            from api.auth import get_tenant_store
            store = get_tenant_store()
            tenant = store.get_by_id(tenant_id)

            if not tenant:
                return 0.0

            score = 60.0  # base

            # Having a paid plan is positive
            if tenant.plan == "enterprise":
                score = 100.0
            elif tenant.plan == "pro":
                score = 85.0
            elif tenant.plan == "starter":
                score = 60.0

            # Stripe subscription present = payment active
            if tenant.stripe_subscription_id:
                score = min(100.0, score + 10.0)

            return score
        except Exception:
            return 50.0

    # ------------------------------------------------------------------
    # Categorization and trend detection
    # ------------------------------------------------------------------

    def _categorize(self, score: float) -> str:
        if score >= CATEGORY_THRESHOLDS["healthy"]:
            return "healthy"
        elif score >= CATEGORY_THRESHOLDS["at_risk"]:
            return "at_risk"
        else:
            return "churning"

    def _detect_trend(self, tenant_id: str, current_score: float) -> str:
        """Compare current score against the 30-day rolling average."""
        rows = self._conn.execute(
            "SELECT AVG(score) as avg_score FROM health_snapshots "
            "WHERE tenant_id = ? ORDER BY computed_at DESC LIMIT 30",
            (tenant_id,),
        ).fetchone()

        if not rows or rows["avg_score"] is None:
            return "stable"

        avg = rows["avg_score"]
        diff = current_score - avg

        if diff >= TREND_THRESHOLD:
            return "improving"
        elif diff <= -TREND_THRESHOLD:
            return "declining"
        return "stable"

    # ------------------------------------------------------------------
    # Recommendations engine
    # ------------------------------------------------------------------

    def _generate_recommendations(self, tenant_id: str, breakdown: ScoreBreakdown) -> list[str]:
        recs = []

        if breakdown.query_frequency < 40:
            recs.append("Query volume is declining. Schedule a check-in call to understand usage barriers.")

        if breakdown.feature_breadth < 40:
            recs.append("Tenant is only using basic features. Offer a guided tour of alerts, webhooks, and integrations.")

        if breakdown.user_engagement < 40:
            recs.append("Low user engagement. Consider hosting a training session or sending onboarding tips.")

        if breakdown.data_freshness < 40:
            recs.append("Meeting data is stale. Verify the ingestion pipeline is running and configured correctly.")

        if breakdown.alert_activity < 35:
            recs.append("No active alerts configured. Demonstrate how policy alerts can surface relevant meeting content automatically.")

        if breakdown.support_signals < 50:
            recs.append("Elevated error rate detected. Review API logs and reach out to troubleshoot integration issues.")

        if breakdown.billing_health < 50:
            recs.append("Tenant is on the starter plan. Discuss upgrade benefits if their usage warrants it.")

        if not recs:
            recs.append("Tenant is healthy. Consider reaching out to gather a testimonial or case study.")

        return recs

    # ------------------------------------------------------------------
    # Expansion signal detection
    # ------------------------------------------------------------------

    def _detect_expansion(self, tenant_id: str, breakdown: ScoreBreakdown) -> list[str]:
        signals = []

        # High query volume could mean they need a bigger plan
        if breakdown.query_frequency >= 90:
            signals.append("Very high query volume -- may be hitting or approaching rate limits.")

        # Many features in use = power user
        if breakdown.feature_breadth >= 80:
            signals.append("Using most available features -- good candidate for enterprise upsell.")

        # High engagement = growing team
        if breakdown.user_engagement >= 85:
            signals.append("High user engagement suggests growing internal adoption.")

        # Check if they're on a lower plan with high usage
        try:
            from api.auth import get_tenant_store
            store = get_tenant_store()
            tenant = store.get_by_id(tenant_id)
            if tenant and tenant.plan == "starter" and breakdown.query_frequency >= 70:
                signals.append("Starter plan tenant with high query volume -- upgrade candidate for Pro.")
            elif tenant and tenant.plan == "pro" and breakdown.query_frequency >= 85:
                signals.append("Pro plan tenant at high usage -- enterprise upgrade opportunity.")
        except Exception:
            pass

        return signals

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _save_snapshot(self, hs: HealthScore):
        import json

        self._conn.execute(
            "INSERT INTO health_snapshots "
            "(tenant_id, score, category, trend, query_frequency, feature_breadth, "
            "user_engagement, data_freshness, alert_activity, support_signals, "
            "billing_health, recommendations, expansion_signals, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                hs.tenant_id,
                hs.score,
                hs.category,
                hs.trend,
                hs.breakdown.query_frequency,
                hs.breakdown.feature_breadth,
                hs.breakdown.user_engagement,
                hs.breakdown.data_freshness,
                hs.breakdown.alert_activity,
                hs.breakdown.support_signals,
                hs.breakdown.billing_health,
                json.dumps(hs.recommendations),
                json.dumps(hs.expansion_signals),
                hs.computed_at,
            ),
        )
        self._conn.commit()

    def close(self):
        self._conn.close()


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_health_scorer: Optional[CustomerHealthScorer] = None


def init_health(output_dir: str) -> CustomerHealthScorer:
    """Initialize the global health scorer. Call once at startup."""
    global _health_scorer
    _health_scorer = CustomerHealthScorer(output_dir)
    return _health_scorer


def get_health_scorer() -> CustomerHealthScorer:
    """Get the global health scorer singleton."""
    if _health_scorer is None:
        raise RuntimeError("Health scorer not initialized -- call init_health() first")
    return _health_scorer
