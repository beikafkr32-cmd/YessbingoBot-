"""
Global WebSocket broadcaster — connects the game engine to all Mini App clients.
"""
import json
import logging
from typing import Any
from aiohttp import web

logger = logging.getLogger(__name__)


class GameBroadcaster:
    def __init__(self) -> None:
        self._rooms: dict[str, set[web.WebSocketResponse]] = {}

    async def join(self, game_id: str, ws: web.WebSocketResponse) -> None:
        self._rooms.setdefault(game_id, set()).add(ws)
        logger.debug(f"WS join game={game_id} total={len(self._rooms[game_id])}")

    async def leave(self, game_id: str, ws: web.WebSocketResponse) -> None:
        if game_id in self._rooms:
            self._rooms[game_id].discard(ws)

    async def broadcast(self, game_id: str, data: dict[str, Any]) -> None:
        room = self._rooms.get(game_id)
        if not room:
            return
        msg = json.dumps(data)
        dead: set[web.WebSocketResponse] = set()
        for ws in list(room):
            try:
                if not ws.closed:
                    await ws.send_str(msg)
            except Exception:
                dead.add(ws)
        room -= dead

    async def send_to_user(self, game_id: str, user_id: int, data: dict[str, Any]) -> None:
        """Send a message only to a specific user in a game room."""
        room = self._rooms.get(game_id)
        if not room:
            return
        msg = json.dumps(data)
        for ws in list(room):
            if ws.get("user_id") == user_id:
                try:
                    if not ws.closed:
                        await ws.send_str(msg)
                except Exception:
                    pass

    def room_size(self, game_id: str) -> int:
        return len(self._rooms.get(game_id, set()))


# Singleton — imported by both api_server.py and handlers/game.py
broadcaster = GameBroadcaster()
