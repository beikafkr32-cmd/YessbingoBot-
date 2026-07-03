"""Broadcaster — posts game events to the Express server's internal broadcast
endpoint, which then fans them out to all Mini App WebSocket clients.

This implementation uses aiohttp so broadcasts are non-blocking and async.
If you prefer an internal aiohttp-based WS relay instead of an external Express
relay, we can add a small relay service that listens for websocket clients and
accepts /internal/broadcast POSTs.
"""
import json
import logging
from typing import Any
import asyncio

import aiohttp

logger = logging.getLogger(__name__)

# Express api-server handles WebSocket relay by default
EXPRESS_BROADCAST_URL = "http://localhost:8080/api/internal/broadcast"


class GameBroadcaster:
    def __init__(self) -> None:
        self._session: aiohttp.ClientSession | None = None

    async def _ensure_session(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession()

    async def broadcast(self, game_id: str, data: dict[str, Any]) -> None:
        """Send a game event to all Mini App clients via Express WebSocket relay."""
        await self._ensure_session()
        body = {"game_id": game_id, "event": data}
        try:
            assert self._session is not None
            async with self._session.post(EXPRESS_BROADCAST_URL, json=body, timeout=2) as resp:
                if resp.status >= 400:
                    logger.debug(f"WS broadcast returned status {resp.status}")
        except Exception as e:
            logger.debug(f"WS broadcast skipped (Express not ready?): {e}")

    async def send_to_user(self, game_id: str, user_id: int, data: dict[str, Any]) -> None:
        """Send to a specific user — include user_id in the event for client-side filtering."""
        await self.broadcast(game_id, {**data, "_target_user": user_id})

    async def close(self) -> None:
        if self._session:
            await self._session.close()
            self._session = None


# Singleton
broadcaster = GameBroadcaster()
