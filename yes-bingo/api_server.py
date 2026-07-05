"""
Async HTTP + REST API server for YES BINGO (internal, port 8082).
This version calls the async database wrappers and uses the centralized
claim handling in handlers.game when available.
Adapted for Replit: bind to 0.0.0.0 and use PORT env var when present.
"""
import asyncio
import json
import logging
import os
from aiohttp import web
import database as db
import config
from utils import generate_bingo_board, flatten_board, get_board_number, generate_game_id

logger = logging.getLogger(__name__)

_bot_application = None   # set by main.py after build


def set_bot_application(app) -> None:
    global _bot_application
    _bot_application = app


def _json(data: dict, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data),
        content_type="application/json",
        status=status,
        headers={"Access-Control-Allow-Origin": "*"},
    )


async def api_health(request: web.Request) -> web.Response:
    return _json({"status": "ok"})


async def api_lobby(request: web.Request) -> web.Response:
    # Use the async helper implemented in database.py
    snapshot = await db.get_lobby_snapshot()
    return _json(snapshot)


async def api_game_join(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)

    user_id_str = body.get("user_id")
    stake = body.get("stake")
    if user_id_str is None or stake is None:
        return _json({"error": "missing user_id or stake"}, 400)

    user_id = int(user_id_str)
    stake = float(stake)
    is_demo = stake == 0.0

    raw_board_num = body.get("board_number")
    try:
        chosen_board_number = int(raw_board_num) if raw_board_num is not None else None
        if chosen_board_number is not None and not (1 <= chosen_board_number <= 200):
            chosen_board_number = None
    except (ValueError, TypeError):
        chosen_board_number = None

    user = await db.get_user(user_id)
    if not user:
        return _json({"error": "user not found"}, 404)

    existing_game = await db.get_active_game_for_user(user_id)
    if existing_game:
        game = await db.get_game(existing_game)
        player = await db.get_player_in_game(existing_game, user_id)
        return _json({"game_id": existing_game, "game": game, "player": player, "rejoined": True})

    if is_demo:
        if user["coin_balance"] < config.DEMO_COST_COINS:
            return _json({"error": "insufficient_coins",
                          "message": f"Demo costs {config.DEMO_COST_COINS} coins."}, 400)
    else:
        if user["wallet_balance"] < stake:
            return _json({"error": "insufficient_balance",
                          "message": f"Need {stake} ETB, you have {user['wallet_balance']:.2f} ETB."}, 400)

    waiting = await db.get_waiting_game(stake)
    if waiting and waiting["player_count"] < config.MAX_PLAYERS:
        game_id = waiting["game_id"]
    else:
        game_id = generate_game_id()
        while await db.get_game(game_id):
            game_id = generate_game_id()
        await db.create_game(game_id, stake)

    board = generate_bingo_board()
    board_number = chosen_board_number if chosen_board_number is not None else get_board_number()
    flat_board = flatten_board(board)

    if is_demo:
        await db.update_coins(user_id, -config.DEMO_COST_COINS)
    else:
        await db.update_balance(user_id, -stake)

    await db.add_player_to_game(game_id, user_id, flat_board, board_number)
    await asyncio.to_thread(lambda: None)  # tiny yield point

    game = await db.get_game(game_id)
    player = await db.get_player_in_game(game_id, user_id)

    from broadcaster import broadcaster
    await broadcaster.broadcast(game_id, {
        "type": "player_joined",
        "player_count": game["player_count"],
        "total_pot": game["total_pot"],
    })

    # Start countdown if 2+ players
    if game["player_count"] >= 2 and _bot_application:
        from handlers.game import active_game_tasks, run_game_countdown
        if game_id not in active_game_tasks:
            task = asyncio.create_task(run_game_countdown(game_id, _bot_application))
            active_game_tasks[game_id] = task

    return _json({"game_id": game_id, "game": game, "player": player, "rejoined": False})


async def api_game_state(request: web.Request) -> web.Response:
    game_id = request.rel_url.query.get("game_id")
    user_id_str = request.rel_url.query.get("user_id")
    if not game_id:
        return _json({"error": "missing game_id"}, 400)

    game = await db.get_game(game_id)
    if not game:
        return _json({"error": "game not found"}, 404)

    player = await db.get_player_in_game(game_id, int(user_id_str)) if user_id_str else None
    extra: dict = {}
    if game.get("winner_id"):
        w = await db.get_user(game["winner_id"])
        if w:
            extra["winner_name"] = w["first_name"]
        if player and player.get("is_winner"):
            prize = game["total_pot"] * config.WINNER_PERCENTAGE * config.FIRST_WINNER_SHARE
            extra["winner_amount"] = round(prize, 2)
            extra["winner_board"] = player["board_numbers"][0] if player.get("board_numbers") else "-"
    return _json({"game": game, "player": player, **extra})


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

    # Delegate to centralized claim handler in handlers.game
    try:
        from handlers.game import handle_claim
    except Exception:
        return _json({"error": "server misconfiguration"}, 500)

    res = await handle_claim(game_id, user_id)
    return _json(res)


async def api_leaderboard(request: web.Request) -> web.Response:
    players = await db.get_leaderboard(10)
    return _json({"players": players})


async def api_history(request: web.Request) -> web.Response:
    user_id_str = request.rel_url.query.get("user_id")
    if not user_id_str:
        return _json({"transactions": []})
    txs = await db.get_user_transactions(int(user_id_str), 20)
    return _json({"transactions": txs})


async def api_profile(request: web.Request) -> web.Response:
    user_id_str = request.rel_url.query.get("user_id")
    if not user_id_str:
        return _json({"error": "missing user_id"}, 400)
    user = await db.get_user(int(user_id_str))
    if not user:
        return _json({"error": "user not found"}, 404)
    user_dict = dict(user)
    user_dict["is_admin"] = int(user_id_str) in config.ADMIN_IDS
    return _json({"user": user_dict})


# Admin endpoints — use small threaded helpers for queries that don't have wrappers
async def api_admin_pending(request: web.Request) -> web.Response:
    user_id_str = request.rel_url.query.get("user_id")
    if not user_id_str or int(user_id_str) not in config.ADMIN_IDS:
        return _json({"error": "unauthorized"}, 403)

    def _fetch_pending():
        conn = db._get_connection_sync()
        try:
            rows = conn.execute(
                "SELECT t.*, u.first_name, u.username FROM transactions t JOIN users u ON t.user_id=u.telegram_id WHERE t.status='pending' ORDER BY t.created_at DESC LIMIT 50"
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()

    pending = await asyncio.to_thread(_fetch_pending)
    return _json({"pending": pending})


async def api_admin_approve(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)
    admin_id = int(body.get("admin_id", 0))
    if admin_id not in config.ADMIN_IDS:
        return _json({"error": "unauthorized"}, 403)
    tx_id = body.get("tx_id")
    if not tx_id:
        return _json({"error": "missing tx_id"}, 400)

    # Approve transaction and credit user if deposit
    def _approve():
        conn = db._get_connection_sync()
        try:
            tx = conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
            if not tx:
                return {"error": "transaction not found"}
            if tx["status"] != "pending":
                return {"error": "already processed"}
            with conn:
                conn.execute("UPDATE transactions SET status='approved', approved_by=? WHERE id=?", (admin_id, tx_id))
                if tx["type"] == "deposit":
                    conn.execute(
                        "UPDATE users SET wallet_balance=wallet_balance+?,total_deposits=total_deposits+?,updated_at=datetime('now') WHERE telegram_id=?",
                        (tx["amount"], tx["amount"], tx["user_id"])
                    )
            return {"ok": True, "tx": dict(tx)}
        finally:
            conn.close()

    result = await asyncio.to_thread(_approve)
    if result.get("ok") and _bot_application:
        asyncio.create_task(_notify_user(result["tx"]["user_id"], f"✅ Your deposit of {result['tx']['amount']:.2f} ETB has been *approved*! Your balance has been updated."))
    if result.get("ok"):
        return _json({"ok": True})
    return _json(result, 400)


async def api_admin_reject(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:
        return _json({"error": "invalid json"}, 400)
    admin_id = int(body.get("admin_id", 0))
    if admin_id not in config.ADMIN_IDS:
        return _json({"error": "unauthorized"}, 403)
    tx_id = body.get("tx_id")
    reason = body.get("reason", "Rejected by admin")
    if not tx_id:
        return _json({"error": "missing tx_id"}, 400)

    def _reject():
        conn = db._get_connection_sync()
        try:
            tx = conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
            if not tx or tx["status"] != "pending":
                return {"error": "transaction not found or already processed"}
            with conn:
                conn.execute("UPDATE transactions SET status='rejected', approved_by=? WHERE id=?", (admin_id, tx_id))
            return {"ok": True, "tx": dict(tx)}
        finally:
            conn.close()

    result = await asyncio.to_thread(_reject)
    if result.get("ok") and _bot_application:
        asyncio.create_task(_notify_user(result["tx"]["user_id"], f"❌ Your deposit of {result['tx']['amount']:.2f} ETB was *rejected*.\nReason: {reason}"))
    if result.get("ok"):
        return _json({"ok": True})
    return _json(result, 400)


async def api_admin_stats(request: web.Request) -> web.Response:
    user_id_str = request.rel_url.query.get("user_id")
    if not user_id_str or int(user_id_str) not in config.ADMIN_IDS:
        return _json({"error": "unauthorized"}, 403)
    stats = await db.get_admin_stats()
    return _json(stats)


async def _notify_user(user_id: int, text: str) -> None:
    if not _bot_application:
        return
    try:
        await _bot_application.bot.send_message(user_id, text, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Could not notify user {user_id}: {e}")


def create_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/health", api_health)
    app.router.add_get("/api/lobby", api_lobby)
    app.router.add_post("/api/game/join", api_game_join)
    app.router.add_get("/api/game/state", api_game_state)
    app.router.add_post("/api/game/claim-bingo", api_claim_bingo)
    app.router.add_get("/api/leaderboard", api_leaderboard)
    app.router.add_get("/api/history", api_history)
    app.router.add_get("/api/profile", api_profile)
    app.router.add_get("/api/admin/pending", api_admin_pending)
    app.router.add_post("/api/admin/approve", api_admin_approve)
    app.router.add_post("/api/admin/reject", api_admin_reject)
    app.router.add_get("/api/admin/stats", api_admin_stats)
    return app


async def start_server(port: int) -> tuple:
    app = create_app()
    # Mount internal WS relay if present
    try:
        from ws_relay import add_routes as _add_ws_routes
        _add_ws_routes(app)
    except Exception:
        pass
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
-    site = web.TCPSite(runner, "127.0.0.1", port)
+    site = web.TCPSite(runner, "0.0.0.0", int(os.environ.get("PORT", port)))
     await site.start()
     logger.info(f"REST API server running on 127.0.0.1:{port}")
     return runner, site
