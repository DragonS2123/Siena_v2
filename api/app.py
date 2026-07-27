"""FastAPI application factory with a single startup composition root."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import (
    attachments,
    chat,
    conversations,
    diagnostics,
    health,
    insights,
    logs,
    memory,
    models,
    ocr,
    settings,
    trace,
    vision,
    voice,
)
from core.runtime import close_runtime, create_runtime


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.runtime = create_runtime()
    try:
        yield
    finally:
        close_runtime(app.state.runtime)


def create_app() -> FastAPI:
    app = FastAPI(title="Siena", version="2.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://127.0.0.1:5173", "http://localhost:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    for router in (
        health.router,
        chat.router,
        models.router,
        conversations.router,
        memory.router,
        insights.router,
        attachments.router,
        ocr.router,
        vision.router,
        voice.router,
        settings.router,
        logs.router,
        diagnostics.router,
        trace.router,
    ):
        app.include_router(router)
    return app


app = create_app()
