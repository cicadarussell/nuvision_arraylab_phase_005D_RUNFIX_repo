from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.db.session import init_db


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialise local/dev tables on startup.

    Production/staging should still use Alembic migrations. This lifespan hook replaces the
    deprecated router startup event, because even warnings deserve to be hunted before they breed.
    """

    init_db()
    yield


app = FastAPI(
    title="NuVision ArrayLab API",
    version="0.5.3-phase005D-RUNFIX",
    description="NuVision ArrayLab backend spine with project/site/roof geometry, panel packing governance, and evidence-first validation.",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "nuvision-arraylab", "phase": "NVA_005D_RUNFIX"}
