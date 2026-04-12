"""Application-level observability and metrics for production monitoring.

Provides in-memory counters, histograms, and gauges with optional Prometheus
exposition format export. All external dependencies (psutil, prometheus_client)
are optional -- the module degrades gracefully without them.
"""

import logging
import math
import os
import threading
import time
from collections import defaultdict
from typing import Optional

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional imports
# ---------------------------------------------------------------------------

try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    psutil = None
    _HAS_PSUTIL = False

try:
    import prometheus_client
    from prometheus_client import (
        CollectorRegistry,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
    )
    _HAS_PROMETHEUS = True
except ImportError:
    prometheus_client = None
    _HAS_PROMETHEUS = False


# ---------------------------------------------------------------------------
# Histogram helper (in-memory, lock-free reads via snapshot)
# ---------------------------------------------------------------------------

class _InMemoryHistogram:
    """Thread-safe histogram that tracks count, sum, and percentiles."""

    __slots__ = ("_lock", "_values", "_max_size")

    def __init__(self, max_size: int = 10_000):
        self._lock = threading.Lock()
        self._values: list[float] = []
        self._max_size = max_size

    def observe(self, value: float) -> None:
        with self._lock:
            self._values.append(value)
            # Evict oldest half when buffer is full
            if len(self._values) > self._max_size:
                self._values = self._values[self._max_size // 2:]

    def snapshot(self) -> dict:
        with self._lock:
            values = sorted(self._values)
        if not values:
            return {"count": 0, "sum": 0.0, "p50": 0.0, "p95": 0.0, "p99": 0.0, "min": 0.0, "max": 0.0}
        n = len(values)
        return {
            "count": n,
            "sum": round(math.fsum(values), 3),
            "p50": values[int(n * 0.50)],
            "p95": values[min(int(n * 0.95), n - 1)],
            "p99": values[min(int(n * 0.99), n - 1)],
            "min": values[0],
            "max": values[-1],
        }

    def reset(self) -> None:
        with self._lock:
            self._values.clear()


# ---------------------------------------------------------------------------
# MetricsCollector
# ---------------------------------------------------------------------------

class MetricsCollector:
    """Central collector for application metrics.

    All metrics are stored in-memory with lightweight data structures.
    Optionally mirrors values to prometheus_client objects when available.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._start_time = time.time()

        # Counters: key -> int
        self._counters: dict[str, int] = defaultdict(int)

        # Histograms: key -> _InMemoryHistogram
        self._histograms: dict[str, _InMemoryHistogram] = {}

        # Gauges: key -> float
        self._gauges: dict[str, float] = defaultdict(float)

        # Per-tenant metrics
        self._tenant_queries: dict[str, int] = defaultdict(int)
        self._tenant_errors: dict[str, int] = defaultdict(int)
        self._tenant_latency: dict[str, _InMemoryHistogram] = {}

        # Error tracking: error_type -> count
        self._errors_by_type: dict[str, int] = defaultdict(int)
        self._errors_by_endpoint: dict[str, int] = defaultdict(int)

        # Active connections gauge
        self._active_connections = 0

        # Prometheus objects (created once if lib available)
        self._prom_registry: Optional[object] = None
        self._prom_request_count = None
        self._prom_request_latency = None
        self._prom_active_connections = None
        self._prom_rag_latency = None
        self._prom_embedding_latency = None
        self._prom_errors = None

        if _HAS_PROMETHEUS:
            self._init_prometheus()

    # ------------------------------------------------------------------
    # Prometheus setup
    # ------------------------------------------------------------------

    def _init_prometheus(self) -> None:
        reg = CollectorRegistry()
        self._prom_registry = reg

        self._prom_request_count = Counter(
            "civiclens_http_requests_total",
            "Total HTTP requests",
            ["method", "endpoint", "status"],
            registry=reg,
        )
        self._prom_request_latency = Histogram(
            "civiclens_http_request_duration_ms",
            "HTTP request latency in milliseconds",
            ["method", "endpoint"],
            buckets=[5, 10, 25, 50, 100, 250, 500, 1000, 2500, 5000, 10000],
            registry=reg,
        )
        self._prom_active_connections = Gauge(
            "civiclens_active_connections",
            "Currently active HTTP connections",
            registry=reg,
        )
        self._prom_rag_latency = Histogram(
            "civiclens_rag_query_duration_ms",
            "RAG query latency in milliseconds",
            ["tenant_id"],
            buckets=[50, 100, 250, 500, 1000, 2500, 5000, 10000, 30000],
            registry=reg,
        )
        self._prom_embedding_latency = Histogram(
            "civiclens_embedding_duration_ms",
            "Embedding latency in milliseconds",
            ["tenant_id"],
            buckets=[10, 25, 50, 100, 250, 500, 1000, 2500],
            registry=reg,
        )
        self._prom_errors = Counter(
            "civiclens_errors_total",
            "Total errors by type",
            ["error_type", "endpoint"],
            registry=reg,
        )

    # ------------------------------------------------------------------
    # Request metrics
    # ------------------------------------------------------------------

    def record_request(self, method: str, endpoint: str, status_code: int, latency_ms: float) -> None:
        """Record an HTTP request completion."""
        key = f"{method}:{endpoint}:{status_code}"
        with self._lock:
            self._counters[key] += 1
            self._counters["requests_total"] += 1

        # Latency histogram (keyed by endpoint)
        hist_key = f"request_latency:{method}:{endpoint}"
        hist = self._histograms.get(hist_key)
        if hist is None:
            hist = _InMemoryHistogram()
            self._histograms[hist_key] = hist
        hist.observe(latency_ms)

        # Global latency histogram
        global_hist = self._histograms.get("request_latency_global")
        if global_hist is None:
            global_hist = _InMemoryHistogram()
            self._histograms["request_latency_global"] = global_hist
        global_hist.observe(latency_ms)

        if _HAS_PROMETHEUS and self._prom_request_count is not None:
            self._prom_request_count.labels(
                method=method, endpoint=endpoint, status=str(status_code)
            ).inc()
            self._prom_request_latency.labels(
                method=method, endpoint=endpoint
            ).observe(latency_ms)

    def inc_active_connections(self) -> None:
        with self._lock:
            self._active_connections += 1
        if _HAS_PROMETHEUS and self._prom_active_connections is not None:
            self._prom_active_connections.inc()

    def dec_active_connections(self) -> None:
        with self._lock:
            self._active_connections = max(0, self._active_connections - 1)
        if _HAS_PROMETHEUS and self._prom_active_connections is not None:
            self._prom_active_connections.dec()

    # ------------------------------------------------------------------
    # RAG query metrics
    # ------------------------------------------------------------------

    def record_rag_query(self, tenant_id: str, latency_ms: float, chunks_retrieved: int = 0) -> None:
        """Record a RAG query execution."""
        with self._lock:
            self._counters["rag_queries_total"] += 1
            self._tenant_queries[tenant_id] += 1

        hist_key = "rag_query_latency"
        hist = self._histograms.get(hist_key)
        if hist is None:
            hist = _InMemoryHistogram()
            self._histograms[hist_key] = hist
        hist.observe(latency_ms)

        # Per-tenant latency
        tenant_hist = self._tenant_latency.get(tenant_id)
        if tenant_hist is None:
            tenant_hist = _InMemoryHistogram()
            self._tenant_latency[tenant_id] = tenant_hist
        tenant_hist.observe(latency_ms)

        with self._lock:
            self._counters["rag_chunks_retrieved_total"] += chunks_retrieved

        if _HAS_PROMETHEUS and self._prom_rag_latency is not None:
            self._prom_rag_latency.labels(tenant_id=tenant_id).observe(latency_ms)

    # ------------------------------------------------------------------
    # Embedding metrics
    # ------------------------------------------------------------------

    def record_embedding(self, tenant_id: str, chunk_count: int, latency_ms: float) -> None:
        """Record an embedding operation."""
        with self._lock:
            self._counters["embeddings_total"] += 1
            self._counters["embedded_chunks_total"] += chunk_count

        hist_key = "embedding_latency"
        hist = self._histograms.get(hist_key)
        if hist is None:
            hist = _InMemoryHistogram()
            self._histograms[hist_key] = hist
        hist.observe(latency_ms)

        if _HAS_PROMETHEUS and self._prom_embedding_latency is not None:
            self._prom_embedding_latency.labels(tenant_id=tenant_id).observe(latency_ms)

    # ------------------------------------------------------------------
    # Error tracking
    # ------------------------------------------------------------------

    def record_error(self, error_type: str, endpoint: str = "") -> None:
        """Record an application error."""
        with self._lock:
            self._counters["errors_total"] += 1
            self._errors_by_type[error_type] += 1
            if endpoint:
                self._errors_by_endpoint[endpoint] += 1

        if _HAS_PROMETHEUS and self._prom_errors is not None:
            self._prom_errors.labels(error_type=error_type, endpoint=endpoint).inc()

    def record_tenant_error(self, tenant_id: str) -> None:
        """Increment per-tenant error counter."""
        with self._lock:
            self._tenant_errors[tenant_id] += 1

    # ------------------------------------------------------------------
    # Gauge setters
    # ------------------------------------------------------------------

    def set_gauge(self, name: str, value: float) -> None:
        with self._lock:
            self._gauges[name] = value

    # ------------------------------------------------------------------
    # Snapshot / export
    # ------------------------------------------------------------------

    def snapshot(self) -> dict:
        """Return a full JSON-serialisable snapshot of all metrics."""
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            active = self._active_connections
            tenant_queries = dict(self._tenant_queries)
            tenant_errors = dict(self._tenant_errors)
            errors_by_type = dict(self._errors_by_type)
            errors_by_endpoint = dict(self._errors_by_endpoint)

        histograms = {k: v.snapshot() for k, v in self._histograms.items()}
        tenant_latency = {k: v.snapshot() for k, v in self._tenant_latency.items()}

        system = self._system_metrics()

        return {
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "counters": counters,
            "gauges": gauges,
            "histograms": histograms,
            "active_connections": active,
            "per_tenant": {
                "queries": tenant_queries,
                "errors": tenant_errors,
                "latency": tenant_latency,
            },
            "errors": {
                "by_type": errors_by_type,
                "by_endpoint": errors_by_endpoint,
            },
            "system": system,
        }

    def _system_metrics(self) -> dict:
        """Collect system-level metrics."""
        info: dict = {
            "uptime_seconds": round(time.time() - self._start_time, 1),
            "pid": os.getpid(),
        }

        if _HAS_PSUTIL:
            try:
                process = psutil.Process()
                mem = process.memory_info()
                info["memory_rss_mb"] = round(mem.rss / (1024 * 1024), 1)
                info["memory_vms_mb"] = round(mem.vms / (1024 * 1024), 1)
                info["cpu_percent"] = process.cpu_percent(interval=0)
                info["threads"] = process.num_threads()
                info["open_files"] = len(process.open_files())

                disk = psutil.disk_usage("/")
                info["disk_total_gb"] = round(disk.total / (1024**3), 1)
                info["disk_used_gb"] = round(disk.used / (1024**3), 1)
                info["disk_free_gb"] = round(disk.free / (1024**3), 1)
                info["disk_percent"] = disk.percent

                vm = psutil.virtual_memory()
                info["system_memory_total_mb"] = round(vm.total / (1024 * 1024), 1)
                info["system_memory_available_mb"] = round(vm.available / (1024 * 1024), 1)
                info["system_memory_percent"] = vm.percent
            except Exception:
                logger.debug("Failed to collect psutil metrics", exc_info=True)

        return info

    def prometheus_export(self) -> Optional[bytes]:
        """Return Prometheus exposition format bytes, or None if not available."""
        if not _HAS_PROMETHEUS or self._prom_registry is None:
            return None
        return generate_latest(self._prom_registry)

    # ------------------------------------------------------------------
    # Health check
    # ------------------------------------------------------------------

    def detailed_health(self, output_dir: str) -> dict:
        """Detailed health check covering DB, ChromaDB, memory, disk."""
        health: dict = {
            "status": "ok",
            "checks": {},
            "timestamp": time.time(),
        }

        # ChromaDB check
        try:
            from api.ingest import get_chroma_collection
            collection = get_chroma_collection(output_dir)
            count = collection.count()
            health["checks"]["chromadb"] = {
                "status": "ok",
                "chunks": count,
            }
        except Exception as e:
            health["checks"]["chromadb"] = {"status": "error", "detail": str(e)}
            health["status"] = "degraded"

        # Tenant DB check
        try:
            from api.auth import get_tenant_store
            store = get_tenant_store()
            tenant_count = len(store.list_all())
            health["checks"]["tenant_db"] = {
                "status": "ok",
                "tenants": tenant_count,
            }
        except Exception as e:
            health["checks"]["tenant_db"] = {"status": "error", "detail": str(e)}
            health["status"] = "degraded"

        # Analytics DB check
        try:
            from api.analytics import get_analytics_store
            analytics = get_analytics_store()
            # Simple connectivity check
            analytics.admin_overview("1d")
            health["checks"]["analytics_db"] = {"status": "ok"}
        except Exception as e:
            health["checks"]["analytics_db"] = {"status": "error", "detail": str(e)}
            health["status"] = "degraded"

        # System resources
        system = self._system_metrics()
        health["checks"]["system"] = {"status": "ok", **system}

        if _HAS_PSUTIL:
            # Flag warnings for resource pressure
            mem_pct = system.get("system_memory_percent", 0)
            disk_pct = system.get("disk_percent", 0)
            if mem_pct > 90:
                health["checks"]["system"]["status"] = "warning"
                health["checks"]["system"]["warning"] = "Memory usage above 90%"
            if disk_pct > 90:
                health["checks"]["system"]["status"] = "warning"
                health["checks"]["system"]["warning"] = "Disk usage above 90%"

        # Output directory check
        try:
            if os.path.isdir(output_dir):
                health["checks"]["output_dir"] = {"status": "ok", "path": output_dir}
            else:
                health["checks"]["output_dir"] = {"status": "error", "detail": "Directory not found"}
                health["status"] = "degraded"
        except Exception as e:
            health["checks"]["output_dir"] = {"status": "error", "detail": str(e)}

        return health


# ---------------------------------------------------------------------------
# MetricsMiddleware
# ---------------------------------------------------------------------------

class MetricsMiddleware(BaseHTTPMiddleware):
    """Starlette middleware that records request latency and status codes.

    Designed to be lightweight (<1ms overhead).  The collector can be
    passed explicitly or resolved lazily from the module singleton, which
    allows the middleware to be registered at import time before
    ``init_metrics()`` runs during the lifespan event.
    """

    # Skip metrics collection for these paths to avoid noise
    _SKIP_PATHS = frozenset({"/health", "/api/health", "/docs", "/openapi.json", "/redoc"})

    def __init__(self, app, collector: "MetricsCollector | None" = None):
        super().__init__(app)
        self._collector = collector

    def _get_collector(self) -> "MetricsCollector | None":
        if self._collector is not None:
            return self._collector
        # Lazy resolution from module singleton
        try:
            self._collector = get_metrics_collector()
        except RuntimeError:
            return None
        return self._collector

    async def dispatch(self, request: Request, call_next):
        collector = self._get_collector()
        if collector is None or request.url.path in self._SKIP_PATHS:
            return await call_next(request)

        collector.inc_active_connections()
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            collector.dec_active_connections()
            collector.record_error("unhandled_exception", request.url.path)
            raise

        latency_ms = round((time.perf_counter() - start) * 1000, 2)
        collector.dec_active_connections()

        # Normalise path to avoid cardinality explosion from path params
        endpoint = self._normalise_path(request.url.path)
        collector.record_request(
            method=request.method,
            endpoint=endpoint,
            status_code=response.status_code,
            latency_ms=latency_ms,
        )

        if response.status_code >= 500:
            collector.record_error(f"http_{response.status_code}", endpoint)

        return response

    @staticmethod
    def _normalise_path(path: str) -> str:
        """Collapse path parameters to reduce metric cardinality.

        e.g. /api/v1/admin/tenants/elk-grove-ca -> /api/v1/admin/tenants/:id
        """
        parts = path.strip("/").split("/")
        normalised = []
        # Known patterns where the segment after a keyword is an ID
        _id_parents = {"tenants", "clips", "meetings", "webhooks", "schedules", "exports"}
        skip_next = False
        for i, part in enumerate(parts):
            if skip_next:
                normalised.append(":id")
                skip_next = False
                continue
            normalised.append(part)
            if part in _id_parents and i < len(parts) - 1:
                skip_next = True
        return "/" + "/".join(normalised) if normalised else path


# ---------------------------------------------------------------------------
# Module-level singleton
# ---------------------------------------------------------------------------

_collector: Optional[MetricsCollector] = None
_output_dir: Optional[str] = None


def init_metrics(output_dir: str) -> MetricsCollector:
    """Initialize the global metrics collector. Call once at startup."""
    global _collector, _output_dir
    _output_dir = output_dir
    _collector = MetricsCollector()
    logger.info(
        "Metrics collector initialized (prometheus=%s, psutil=%s)",
        _HAS_PROMETHEUS,
        _HAS_PSUTIL,
    )
    return _collector


def get_metrics_collector() -> MetricsCollector:
    """Get the global metrics collector singleton."""
    if _collector is None:
        raise RuntimeError("Metrics not initialized -- call init_metrics() first")
    return _collector


def get_output_dir() -> str:
    """Get the output directory used during init."""
    return _output_dir or os.environ.get("MEETINGS_OUTPUT_DIR", "./meetings_output")


# ---------------------------------------------------------------------------
# Convenience helpers (module-level functions)
# ---------------------------------------------------------------------------


def record_rag_query(tenant_id: str, latency_ms: float, chunks_retrieved: int = 0) -> None:
    """Record a RAG query. Safe to call even if metrics not initialized."""
    try:
        collector = get_metrics_collector()
        collector.record_rag_query(tenant_id, latency_ms, chunks_retrieved)
    except RuntimeError:
        pass


def record_embedding(tenant_id: str, chunk_count: int, latency_ms: float) -> None:
    """Record an embedding operation. Safe to call even if metrics not initialized."""
    try:
        collector = get_metrics_collector()
        collector.record_embedding(tenant_id, chunk_count, latency_ms)
    except RuntimeError:
        pass


def record_error(error_type: str, endpoint: str = "") -> None:
    """Record an error. Safe to call even if metrics not initialized."""
    try:
        collector = get_metrics_collector()
        collector.record_error(error_type, endpoint)
    except RuntimeError:
        pass
