from __future__ import annotations

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse

from app.api.routes import actors, admin, evaluation, events, runs, scenarios, ui
from app.services.config import get_settings
from app.services.db import init_db


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="PM Simulation", lifespan=lifespan)

    @app.get("/health")
    def health() -> JSONResponse:
        return JSONResponse({"status": "ok"})

    app.include_router(runs.router)
    app.include_router(scenarios.router)
    app.include_router(events.router)
    app.include_router(actors.router)
    app.include_router(evaluation.router)
    app.include_router(admin.router)
    app.include_router(ui.router)
    return app


app = create_app()


def run() -> None:
    uvicorn.run("app.api.main:app", host="0.0.0.0", port=8000)
