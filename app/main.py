# app/main.py

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1.routes import router as v1_router
from app.core.config import settings
from app.core.logging import setup_logging
from app.db.mongo import close_mongo_connection, connect_to_mongo, get_db
from app.repositories.notification_repository import NotificationRepository
from app.utils import get_cache

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    await connect_to_mongo()
    try:
        db = get_db()
        await NotificationRepository(db).create_indexes()
        _ = get_cache()
        logger.info("Startup complete")
        yield
    finally:
        await close_mongo_connection()
        logger.info("Shutdown complete")


def create_app() -> FastAPI:
    setup_logging()

    _app = FastAPI(
        title=settings.APP_NAME,
        version="0.1.0",
        docs_url="/docs",
        redoc_url="/redoc",
        lifespan=lifespan,
    )

    allow_origins = settings.CORS_ORIGINS or []
    _app.add_middleware(
        CORSMiddleware,
        allow_origins=allow_origins,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @_app.exception_handler(RequestValidationError)
    async def validation_exception_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "type": "validation_error",
                    "message": "Request validation failed",
                    "details": exc.errors(),
                }
            },
        )

    @_app.exception_handler(HTTPException)
    async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": {
                    "type": "http_error",
                    "message": exc.detail if exc.detail else "Request failed",
                }
            },
        )

    @_app.exception_handler(Exception)
    async def unhandled_exception_handler(
        request: Request, exc: Exception
    ) -> JSONResponse:
        logger.exception("Unhandled exception")
        return JSONResponse(
            status_code=500,
            content={
                "error": {
                    "type": "internal_server_error",
                    "message": "Internal server error",
                }
            },
        )

    @_app.get("/health", tags=["health"])
    async def health() -> dict:
        return {"status": "ok"}

    # Task path alignment: /api/notifications...
    _app.include_router(v1_router, prefix="/api")
    return _app


app = create_app()
