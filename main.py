import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import app.db.base
from app.api.router import api_router
from app.core.logging_config import setup_logging

setup_logging()
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from slowapi import _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.api.system import system_router
from app.core.config import APP_ENVIRONMENT
from app.core.rate_limit import limiter
from app.core.scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    logger.info("*** Application starting — environment: %s ***", APP_ENVIRONMENT)
    start_scheduler()
    yield
    stop_scheduler()
    logger.info("Application shutdown complete.")


from app.api.router import OPENAPI_TAGS

app = FastAPI(
    title="vb-intern API",
    description=(
        "Internal management system for **vb-intern**.\n\n"
        "## Modules\n\n"
        "| Module | Description |\n"
        "|--------|-------------|\n"
        "| **Authentication** | Login, token refresh, password reset, Google OAuth |\n"
        "| **Members** | Current user profile and preferences |\n"
        "| **StandesDB** | Member & contact registry with images, changelog, export |\n"
        "| **Archive** | Hierarchical document archive with S3 storage |\n"
        "| **P4x** | Financial accounting — transactions, categories, fees, reports |\n"
        "| **Tracking** | Activity logs, sent emails, email template management |\n"
        "| **System** | Permission rules, scheduled jobs, database browser |\n"
        "| **Information** | Public payment account details |\n\n"
        "## Authentication\n\n"
        "Most endpoints require a **Bearer token** obtained via "
        "`POST /api/auth/login`.\n\n"
        "Pass it in the `Authorization` header:\n"
        "```\nAuthorization: Bearer <access_token>\n```\n\n"
        "Tokens expire after 60 minutes. Use `POST /api/auth/refresh` "
        "to obtain a new token pair.\n\n"
        "## Rate Limiting\n\n"
        "Authentication endpoints are rate-limited (see individual endpoint "
        "descriptions). Exceeding the limit returns `429 Too Many Requests`."
    ),
    version="0.1.0",
    openapi_tags=OPENAPI_TAGS,
    lifespan=lifespan,
)


# --- Security Headers Middleware ---
class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


from app.core.activity_logger import ActivityLoggingMiddleware

app.add_middleware(ActivityLoggingMiddleware)
app.add_middleware(SecurityHeadersMiddleware)

# --- Register SlowAPI Rate Limiter ---
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

# CORS Configuration: Explicitly whitelist our frontend domain.
# This prevents the browser from blocking requests from the Vue app.
origins = [
    "https://app.vb-intern.dev.schimpl.cc",
    "http://localhost:20001",
    "http://127.0.0.1:20001",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept"],
)

# 1. Mount the system routes (like the / health check) without a prefix
app.include_router(system_router)

# 2. Mount all business logic routes under the /api prefix
app.include_router(api_router, prefix="/api")
