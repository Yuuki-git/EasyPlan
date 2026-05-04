from fastapi import FastAPI

from app.api.routes_integrations import router as integrations_router
from app.api.routes_intents import router as intents_router
from app.api.routes_threads import router as threads_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="EasyPlan Backend API",
        version="0.1.0",
        description="Intent-driven task planning API with HITL LangGraph checkpoints.",
        openapi_url="/openapi.json",
        docs_url="/docs",
    )
    app.include_router(intents_router)
    app.include_router(threads_router)
    app.include_router(integrations_router)
    return app


app = create_app()
