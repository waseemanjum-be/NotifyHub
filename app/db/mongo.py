# app/db/mongo.py

from __future__ import annotations

from typing import Optional

from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.core.config import settings

_client: Optional[AsyncIOMotorClient] = None
_db: Optional[AsyncIOMotorDatabase] = None


async def connect_to_mongo() -> None:
    """
    Initialize a single Motor client per process.
    Motor/PyMongo manages internal connection pooling for concurrency.
    """
    global _client, _db

    _client = AsyncIOMotorClient(
        settings.MONGODB_URI,
        appname=settings.MONGODB_APP_NAME,
        connectTimeoutMS=settings.MONGODB_CONNECT_TIMEOUT_MS,
        serverSelectionTimeoutMS=settings.MONGODB_SERVER_SELECTION_TIMEOUT_MS,
    )
    _db = _client[settings.MONGODB_DB]


async def close_mongo_connection() -> None:
    global _client
    if _client is not None:
        _client.close()


def get_db() -> AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("MongoDB is not initialized. Startup event may not have run.")
    return _db
