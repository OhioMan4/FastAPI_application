import time
import random
import logging
import asyncio

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from prometheus_client import (
    Counter,
    Histogram,
    generate_latest,
    CONTENT_TYPE_LATEST,
)

# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------
REQUEST_COUNT = Counter(
    "http_requests_total",
    "Total number of HTTP requests",
    ["method", "endpoint", "status_code"],
)

REQUEST_LATENCY = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = FastAPI(
    title="DevOps Observability Demo",
    description="Simple FastAPI app for Prometheus, Grafana, Loki & K6 testing.",
    version="1.0.0",
)

app.mount("/static", StaticFiles(directory="app/static"), name="static")

# ---------------------------------------------------------------------------
# Stats tracker
# ---------------------------------------------------------------------------
_stats = {
    "total_requests": 0,
    "errors": 0,
    "slow_calls": 0,
    "compute_calls": 0,
}

# ---------------------------------------------------------------------------
# Middleware – request logging + metrics
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Excluded paths from stats tracking
# ---------------------------------------------------------------------------
_EXCLUDED = {"/metrics", "/stats", "/reset", "/", "/error"}

@app.middleware("http")
async def observe_requests(request: Request, call_next):
    start = time.perf_counter()
    response: Response = await call_next(request)
    duration = time.perf_counter() - start

    endpoint = request.url.path
    method   = request.method
    status   = response.status_code

    if not endpoint.startswith("/static") and endpoint not in _EXCLUDED:
        _stats["total_requests"] += 1
        if status >= 500:
            _stats["errors"] += 1

    logger.info(
        "method=%s endpoint=%s status=%s duration=%.4fs",
        method, endpoint, status, duration,
    )

    REQUEST_COUNT.labels(method=method, endpoint=endpoint, status_code=status).inc()
    REQUEST_LATENCY.labels(method=method, endpoint=endpoint).observe(duration)

    return response

# ---------------------------------------------------------------------------
# In-memory "database"
# ---------------------------------------------------------------------------
_items: list[dict] = [
    {"id": 1, "name": "Widget"},
    {"id": 2, "name": "Gadget"},
    {"id": 3, "name": "Doohickey"},
]

# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["Ops"])
async def health():
    """Liveness / readiness probe."""
    return {"status": "ok"}


@app.get("/items", tags=["Items"])
async def list_items():
    """Return the full list of items (fast response)."""
    logger.info("Listing %d items", len(_items))
    return {"items": _items}


@app.post("/items", tags=["Items"], status_code=201)
async def create_item(payload: dict):
    """
    Create a new item.

    Expected body: `{ "name": "<string>" }`
    """
    name = payload.get("name", "").strip()
    if not name:
        return JSONResponse(status_code=422, content={"detail": "Field 'name' is required."})

    new_item = {"id": len(_items) + 1, "name": name}
    _items.append(new_item)
    logger.info("Created item: %s", new_item)
    return {"message": "Item created", "item": new_item}


@app.get("/", include_in_schema=False)
async def serve_ui():
    """Serve the dashboard UI."""
    return FileResponse("app/static/index.html")


@app.get("/stats", tags=["Ops"])
async def get_stats():
    """Return live request statistics."""
    return {
        "total_requests": _stats["total_requests"],
        "errors": _stats["errors"],
        "slow_calls": _stats["slow_calls"],
        "compute_calls": _stats["compute_calls"],
        "error_rate": round(
            (_stats["errors"] / _stats["total_requests"] * 100)
            if _stats["total_requests"] > 0 else 0, 2
        ),
    }


@app.get("/reset", tags=["Ops"])
async def reset_stats():
    """Reset all stat counters."""
    for key in _stats:
        _stats[key] = 0
    logger.info("Stats reset")
    return {"message": "Stats reset successfully"}


# patch slow + compute to update stats
@app.get("/slow", tags=["Simulation"])
async def slow_endpoint():
    """Simulate a slow response (2–3 s) to test latency metrics."""
    _stats["slow_calls"] += 1
    delay = random.uniform(2.0, 3.0)
    logger.info("Slow endpoint sleeping for %.2fs", delay)
    await asyncio.sleep(delay)
    return {"message": "Finally done", "delay_seconds": round(delay, 3)}


@app.get("/error", tags=["Simulation"])
async def random_error():
    """Return HTTP 500 ~30 % of the time to simulate failure rates."""
    if random.random() < 0.30:
        logger.warning("Simulated 500 error triggered")
        _stats["errors"] += 1          # explicitly track here
        _stats["total_requests"] += 1  # middleware may miss JSONResponse status
        return JSONResponse(
            status_code=500,
            content={"detail": "Simulated internal server error"},
        )
    return {"message": "All good"}


@app.get("/compute", tags=["Simulation"])
async def cpu_compute():
    """Run a CPU-bound Fibonacci calculation to simulate CPU load."""
    _stats["compute_calls"] += 1
    n = 35  # large enough to be measurable, small enough not to block forever

    def fib(x: int) -> int:
        if x <= 1:
            return x
        return fib(x - 1) + fib(x - 2)

    logger.info("Starting CPU compute: fib(%d)", n)
    start   = time.perf_counter()
    result  = fib(n)
    elapsed = time.perf_counter() - start
    logger.info("fib(%d) = %d in %.4fs", n, result, elapsed)
    return {"n": n, "result": result, "compute_seconds": round(elapsed, 4)}


@app.get("/metrics", tags=["Ops"], include_in_schema=False)
async def metrics():
    """Expose Prometheus metrics for scraping."""
    data = generate_latest()
    return Response(content=data, media_type=CONTENT_TYPE_LATEST)
