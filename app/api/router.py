from fastapi import APIRouter
from app.api.v1 import claims, decisions, simulate, stream, audit, analytics

api_router = APIRouter()
api_router.include_router(claims.router)
api_router.include_router(decisions.router)
api_router.include_router(simulate.router)
api_router.include_router(stream.router)
api_router.include_router(audit.router)
api_router.include_router(analytics.router)
