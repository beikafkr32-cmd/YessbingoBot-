from aiohttp import web
import asyncio
import logging
from typing import Dict, Set, List

logger = logging.getLogger(__name__)

# Simple internal WebSocket relay for Mini App clients.
# Clients connect to /ws and send a JSON message {"type":"subscribe","game_id":"G123"}
# The relay stores websockets per game_id and accepts POSTs to /internal/broadcast
# to fan-out events to connected clients.

WS_CLIENTS: Dict[str, Set[web.WebSocketResponse]] = {}


async def ws_handler(request: web.Request) -> web.StreamResponse:
    ws = web.WebSocketResponse()
    await ws.prepare(request)
    sub_games: List[str] = []
    try:
        async for msg in ws:
            if msg.type == web.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except Exception:
                    continue
                if data.get("type") == "subscribe":
                    gid = data.get("game_id")
                    if gid:
                        WS_CLIENTS.setdefault(gid, set()).add(ws)
                        sub_games.append(gid)
                # ignore other messages
            elif msg.type == web.WSMsgType.ERROR:
                logger.error('ws connection closed with exception %s' % ws.exception())
    finally:
        for gid in sub_games:
            if gid in WS_CLIENTS and ws in WS_CLIENTS[gid]:
                WS_CLIENTS[gid].remove(ws)
    return ws


async def internal_broadcast(request: web.Request) -> web.Response:
    try:
        payload = await request.json()
    except Exception:
        return web.Response(text='invalid json', status=400)
    gid = payload.get('game_id')
    event = payload.get('event')
    if not gid or not event:
        return web.Response(text='missing fields', status=400)
    clients = list(WS_CLIENTS.get(gid, []))
    for ws in clients:
        try:
            await ws.send_json(event)
        except Exception:
            pass
    return web.Response(text='ok')


def add_routes(app: web.Application) -> None:
    app.router.add_get('/ws', ws_handler)
    app.router.add_post('/api/internal/broadcast', internal_broadcast)
