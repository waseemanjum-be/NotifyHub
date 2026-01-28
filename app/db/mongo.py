# app/db/mongo.py

from __future__ import annotations

from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import settings

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


async def connect_to_mongo() -> None:
    global _client, _db
    if _client is not None and _db is not None:
        return

    _client = AsyncIOMotorClient(
        settings.MONGODB_URI,
        appname=settings.MONGODB_APP_NAME,
        connectTimeoutMS=settings.MONGODB_CONNECT_TIMEOUT_MS,
        serverSelectionTimeoutMS=settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS,
    )
    _db = _client[settings.MONGODB_DB]


async def close_mongo_connection() -> None:
    global _client, _db
    if _client is not None:
        _client.close()
    _client = None
    _db = None


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("MongoDB is not initialized. Call connect_to_mongo() first.")
    return _db
