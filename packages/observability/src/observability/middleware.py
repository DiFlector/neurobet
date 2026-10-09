import time
import uuid
from typing import Callable
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from .logging import ObservabilityContext, get_logger, ctx_request_id
from .metrics import REGISTRY


class ObservabilityMiddleware(BaseHTTPMiddleware):
    """
    Middleware that:
    1. Extracts or generates X-Request-ID.
    2. Sets ObservabilityContext for the request lifespan.
    3. Logs request completion with latency, status, and result.
    4. Attaches X-Request-ID to response headers.
    """

    def __init__(self, app, service_name: str = "backend"):
        super().__init__(app)
        self.service_name = service_name
        self.logger = get_logger(service_name)

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        req_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        start_time = time.perf_counter()

        with ObservabilityContext(request_id=req_id):
            try:
                response = await call_next(request)
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                response.headers["x-request-id"] = req_id

                # Avoid logging /metrics or /health polling loops as errors
                if request.url.path not in ("/health", "/metrics", "/ready"):
                    self.logger.info(
                        f"{request.method} {request.url.path} -> {response.status_code}",
                        extra={
                            "request_id": req_id,
                            "operation": f"{request.method} {request.url.path}",
                            "latency_ms": latency_ms,
                            "result": "SUCCESS" if response.status_code < 400 else "ERROR",
                        },
                    )

                return response
            except Exception as exc:
                latency_ms = (time.perf_counter() - start_time) * 1000.0
                self.logger.error(
                    f"Unhandled exception during {request.method} {request.url.path}: {str(exc)}",
                    exc_info=True,
                    extra={
                        "request_id": req_id,
                        "operation": f"{request.method} {request.url.path}",
                        "latency_ms": latency_ms,
                        "result": "CRITICAL_ERROR",
                        "error": str(exc),
                    },
                )
                raise exc


def setup_observability(app, service_name: str = "neurobet"):
    """Configures structured logging middleware and mounts the /metrics endpoint."""
    app.add_middleware(ObservabilityMiddleware, service_name=service_name)

    @app.get("/metrics", include_in_schema=False)
    def metrics_endpoint():
        content = REGISTRY.generate_prometheus_text()
        return Response(content=content, media_type="text/plain; version=0.0.4; charset=utf-8")
