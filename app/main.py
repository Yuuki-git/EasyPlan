import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Iterable

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from app.api.auth import router as auth_router
from app.api.routes_integrations import router as integrations_router
from app.api.routes_intents import router as intents_router
from app.api.routes_threads import router as threads_router


logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_STATIC_DIR = PROJECT_ROOT / "frontend" / "dist"
DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "http://localhost:3000")


@asynccontextmanager
async def lifespan(app: FastAPI):
    _log_missing_environment()
    yield


def create_app(
    *,
    static_dir: str | Path | None = None,
    enable_static: bool = True,
) -> FastAPI:
    app = FastAPI(
        title="EasyPlan Backend API",
        version="0.1.0",
        description="Intent-driven task planning API with HITL LangGraph checkpoints.",
        openapi_url="/openapi.json",
        docs_url="/docs",
        lifespan=lifespan,
    )
    _configure_cors(app, _load_cors_origins())

    @app.get("/health", tags=["health"])
    async def health() -> dict[str, str]:
        return {"status": "ok", "service": "easyplan-backend"}

    app.include_router(auth_router)
    app.include_router(intents_router)
    app.include_router(threads_router)
    app.include_router(integrations_router)

    if enable_static:
        resolved_static_dir = Path(static_dir) if static_dir is not None else DEFAULT_STATIC_DIR
        if resolved_static_dir.exists():
            app.mount(
                "/",
                StaticFiles(directory=str(resolved_static_dir), html=True),
                name="frontend",
            )
        else:
            logger.info("frontend_static_dir_missing", extra={"static_dir": str(resolved_static_dir)})

    return app


def _configure_cors(app: FastAPI, origins: list[str]) -> None:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Last-Event-ID", "X-User-Timezone"],
    )


def _load_cors_origins() -> list[str]:
    raw_origins = os.getenv("EASYPLAN_CORS_ORIGINS")
    if not raw_origins:
        return list(DEFAULT_CORS_ORIGINS)
    return _split_csv(raw_origins)


def _split_csv(value: str) -> list[str]:
    return [item for item in (part.strip() for part in value.split(",")) if item]


def _log_missing_environment() -> None:
    required_vars = ["DATABASE_URL"]
    if not (os.getenv("EASYPLAN_JWT_SECRET") or os.getenv("JWT_SECRET_KEY")):
        required_vars.append("EASYPLAN_JWT_SECRET or JWT_SECRET_KEY")
    missing = [var for var in required_vars if " or " in var or not os.getenv(var)]
    model_provider = os.getenv("EASYPLAN_LLM_PROVIDER", "openai").strip().lower()
    missing.extend(_missing_provider_keys(model_provider))
    if missing:
        logger.warning("missing_environment_variables", extra={"missing": missing})


def _missing_provider_keys(model_provider: str) -> list[str]:
    provider_keys: dict[str, Iterable[str]] = {
        "openai": ("OPENAI_API_KEY",),
        "deepseek": ("DEEPSEEK_API_KEY",),
        "xiaomi": ("XIAOMI_API_KEY",),
    }
    return [key for key in provider_keys.get(model_provider, ()) if not os.getenv(key)]


app = create_app()
