"""
Broadcaster — posts game events to the Express server's internal broadcast
endpoint, which then fans them out to all Mini App WebSocket clients.
"""
import json
import logging
from typing import Any
import asyncio
from urllib.request import Request, urlopen
from urllib.error import URLError

logger = logging.getLogger(__name__)

# Express api-server handles WebSocket relay
EXPRESS_BROADCAST_URL = "http://localhost:8080/api/internal/broadcast"


class GameBroadcaster:
    async def broadcast(self, game_id: str, data: dict[str, Any]) -> None:
        """Send a game event to all Mini App clients via Express WebSocket relay."""
        body = json.dumps({"game_id": game_id, "event": data}).encode()
        try:
            req = Request(
                EXPRESS_BROADCAST_URL,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            # Run blocking urlopen in a thread so we don't block the event loop
            loop = asyncio.get_event_loop()
            await loop.run_in_executor(None, lambda: urlopen(req, timeout=2))
        except (URLError, OSError) as e:
            logger.debug(f"WS broadcast skipped (Express not ready?): {e}")

    async def send_to_user(self, game_id: str, user_id: int, data: dict[str, Any]) -> None:
        """Send to a specific user — include user_id in the event for client-side filtering."""
        await self.broadcast(game_id, {**data, "_target_user": user_id})


# Singleton
broadcaster = GameBroadcaster()
