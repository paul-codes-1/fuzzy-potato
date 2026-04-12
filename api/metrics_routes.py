"""FastAPI routes for application metrics and observability."""


from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, PlainTextResponse

from api.auth import require_admin
from api.metrics import get_metrics_collector, get_output_dir

router = APIRouter()


@router.get("/api/v1/metrics")
def metrics_endpoint(_: bool = Depends(require_admin)):
    """Prometheus exposition format if prometheus_client is available, else JSON.

    Returns text/plain with Prometheus metrics when the library is installed,
    otherwise falls back to the same structured JSON as /api/v1/metrics/json.
    """
    collector = get_metrics_collector()
    prom_bytes = collector.prometheus_export()
    if prom_bytes is not None:
        return PlainTextResponse(
            content=prom_bytes.decode("utf-8"),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
    # Fallback to JSON
    return JSONResponse(content=collector.snapshot())


@router.get("/api/v1/metrics/json")
def metrics_json_endpoint(_: bool = Depends(require_admin)):
    """Always returns structured JSON metrics regardless of available libraries."""
    collector = get_metrics_collector()
    return JSONResponse(content=collector.snapshot())


@router.get("/api/v1/metrics/health")
def metrics_health_endpoint(_: bool = Depends(require_admin)):
    """Detailed health check: DB connectivity, ChromaDB status, memory, disk."""
    collector = get_metrics_collector()
    output_dir = get_output_dir()
    health = collector.detailed_health(output_dir)
    status_code = 200 if health["status"] == "ok" else 503
    return JSONResponse(content=health, status_code=status_code)
