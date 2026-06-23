"""
Async HTTP + WebSocket server for the YES BINGO Mini App.
Runs inside the same asyncio loop as the Telegram bot (started in post_init).
"""
import json
import logging
import os
from pathlib import Path
from aiohttp import web, WSMsgType
import database as db
import config
from broadcaster import broadcaster

logger = logging.getLogger(__name__)

WEBAPP_DIR = Path(__file__).parent / "web_app"


# ── helpers ─────────────────────────────────────────────────────────────────

def _json(data: dict, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data),
        content_type="application/json",
        status=status,
        headers={"Access-Control-Allow-Origin": "*"},
    )


def _cors(request: web.Request) -> web.Response:
    return web.Response(
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
            "Access-Control-Allow-Headers": "Content-Type, X-User-Id",
        }
    )


# ── static Mini App files ────────────────────────────────────────────────────

async def serve_index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEBAPP_DIR / "index.html")


async def serve_static(request: web.Request) -> web.FileResponse:
    filename = request.match_info["filename"]
    filepath = WEBAPP_DIR / filename
    if not filepath.exists() or not filepath.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(filepath)


# ── REST endpoints ───────────────────────────────────────────────────────────

async def api_health(request: web.Request) -> web.Response:
    return _json({"status": "ok"})


async def api_game_state(request: web.Request) -> web.Response:
    game_id = request.rel_url.query.get("game_id")
    user_id_str = request.rel_url.query.get("user_id")
    if not game_id:
        return _json({"error": "missing game_id"}, 400)

    game = db.get_game(game_id)
    if not game:
        return _json({"error": "game not found"}, 404)

    player = db.get_player_in_game(game_id, int(user_id_str)) if user_id_str else None
    extra: dict = {}
    if game.get("winner_id"):
        w = db.get_user(game["winner_id"])
        if w:
            extra["winner_name"] = w["first_name"]
        if player and player.get("is_winner"):
            prize = game["total_pot"] * config.WINNER_PERCENTAGE * config.FIRST_WINNER_SHARE
            extra["winner_amount"] = round(prize, 2)
            extra["winner_board"] = player["board_numbers"][0] if player.get("board_numbers") else "-"
    return _json({"game": game, "player": player, **extra})


async def api_leaderboard(request: web.Request) -> web.Response:
    players = db.get_leaderboard(10)
    return _json({"players": players})


async def api_history(request: web.Request) -> web.Response:
    user_id_str = request.rel_url.query.get("user_id")
    if not user_id_str:
        return _json({"transactions": []})
    txs = db.get_user_transactions(int(user_id_str), 20)
    return _json({"transactions": txs})


async def api_profile(request: web.Request) -> web.Response:
    user_id_str = request.rel_url.query.get("user_id")
    if not user_id_str:
        return _json({"error": "missing user_id"}, 400)
    user = db.get_user(int(user_id_str))
    if not user:
        return _json({"error": "user not found"}, 404)
    return _json({"user": user})


async def api_claim_bingo(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)

    game_id = body.get("game_id")
    user_id_str = body.get("user_id")
    if not game_id or not user_id_str:
        return _json({"error": "missing fields"}, 400)

    user_id = int(user_id_str)
    game = db.get_game(game_id)
    if not game or game["status"] != "active":
        return _json({"success": False, "message": "Game not active"})

    player = db.get_player_in_game(game_id, user_id)
    if not player:
        return _json({"success": False, "message": "Not in game"})
    if player["is_eliminated"]:
        return _json({"success": False, "eliminated": True})

    from utils import check_bingo
    called = game["called_numbers"]
    boards = [player["main_board"]] + player["extra_boards"]
    has_bingo = False
    for flat in boards:
        board_2d = [[flat[c * 5 + r] for r in range(5)] for c in range(5)]
        if check_bingo(board_2d, called):
            has_bingo = True
            break

    if not has_bingo:
        db.update_player(game_id, user_id, is_eliminated=1)
        await broadcaster.broadcast(game_id, {
            "type": "player_eliminated",
            "user_id": user_id,
        })
        return _json({"success": False, "eliminated": True})

    prize = game["total_pot"] * config.WINNER_PERCENTAGE * config.FIRST_WINNER_SHARE
    db.update_game(game_id, status="finished", winner_id=user_id)
    db.update_player(game_id, user_id, is_winner=1)
    db.update_balance(user_id, prize)
    db.create_transaction(user_id, prize, "win", description=f"Bingo win in {game_id}")

    conn = db.get_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE users SET total_wins = total_wins + 1, win_streak = win_streak + 1 "
                "WHERE telegram_id = ?", (user_id,)
            )
    finally:
        conn.close()

    user = db.get_user(user_id)
    board_number = player["board_numbers"][0] if player.get("board_numbers") else "-"

    await broadcaster.broadcast(game_id, {
        "type": "game_end",
        "winner_id": user_id,
        "winner_name": user["first_name"] if user else "Player",
        "board_number": board_number,
        "amount": round(prize, 2),
    })

    return _json({
        "success": True,
        "winner_name": user["first_name"] if user else "Player",
        "board_number": board_number,
        "amount": round(prize, 2),
    })


# ── WebSocket handler ────────────────────────────────────────────────────────

async def ws_handler(request: web.Request) -> web.WebSocketResponse:
    game_id = request.rel_url.query.get("game_id", "")
    user_id_str = request.rel_url.query.get("user_id", "")
    user_id = int(user_id_str) if user_id_str.isdigit() else 0

    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    ws["user_id"] = user_id

    await broadcaster.join(game_id, ws)
    logger.info(f"WS connected game={game_id} user={user_id}")

    # Send current game state immediately on connect
    game = db.get_game(game_id)
    if game:
        player = db.get_player_in_game(game_id, user_id) if user_id else None
        await ws.send_str(json.dumps({
            "type": "init",
            "game": game,
            "player": player,
        }))

    try:
        async for msg in ws:
            if msg.type == WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                    msg_type = data.get("type")
                    if msg_type == "ping":
                        await ws.send_str(json.dumps({"type": "pong"}))
                except Exception:
                    pass
            elif msg.type in (WSMsgType.ERROR, WSMsgType.CLOSE):
                break
    finally:
        await broadcaster.leave(game_id, ws)
        logger.info(f"WS disconnected game={game_id} user={user_id}")

    return ws


# ── app factory ──────────────────────────────────────────────────────────────

def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", serve_index)
    app.router.add_get("/web_app/", serve_index)
    app.router.add_get("/web_app/index.html", serve_index)
    app.router.add_get("/web_app/{filename}", serve_static)
    app.router.add_get("/api/health", api_health)
    app.router.add_get("/api/game/state", api_game_state)
    app.router.add_get("/api/leaderboard", api_leaderboard)
    app.router.add_get("/api/history", api_history)
    app.router.add_get("/api/profile", api_profile)
    app.router.add_post("/api/game/claim-bingo", api_claim_bingo)
    app.router.add_get("/ws", ws_handler)
    app.router.add_route("OPTIONS", "/{path_info:.*}", lambda r: _cors(r))
    return app


async def start_server(port: int) -> tuple:
    """Start aiohttp server. Returns (runner, site) for cleanup."""
    app = create_app()
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logger.info(f"Mini App server running on port {port}")
    return runner, site
