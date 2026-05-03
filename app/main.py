from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.database import init_db, db_mode

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting up ClaimsNexus API")
    try:
        await init_db()
        log.info("Database initialized", mode=db_mode())
    except Exception as exc:
        log.warning("Database initialization failed, app still starting", error=str(exc))
    yield
    log.info("Shutting down ClaimsNexus API")


app = FastAPI(
    title="ClaimsNexus API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://claimsnexus.vercel.app",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix="/api/v1")


@app.get("/")
def root():
    return {
        "status": "ok",
        "message": "ClaimsNexus backend is running",
        "docs": "/docs",
        "health": "/health",
    }


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "service": "claimsnexus",
        "db_mode": db_mode(),
    }