"""
Async HTTP + REST API server for YES BINGO (internal, port 8082).
Handles all game logic; Express on 8080 proxies public traffic here.
"""
import asyncio
import json
import logging
from aiohttp import web
import database as db
import config
from utils import generate_bingo_board, flatten_board, get_board_number, generate_game_id

logger = logging.getLogger(__name__)

_bot_application = None   # set by main.py after build


def set_bot_application(app) -> None:
    global _bot_application
    _bot_application = app


# ── helpers ──────────────────────────────────────────────────────────────────

def _json(data: dict, status: int = 200) -> web.Response:
    return web.Response(
        text=json.dumps(data),
        content_type="application/json",
        status=status,
        headers={"Access-Control-Allow-Origin": "*"},
    )


# ── /api/health ───────────────────────────────────────────────────────────────

async def api_health(request: web.Request) -> web.Response:
    return _json({"status": "ok"})


# ── /api/lobby ────────────────────────────────────────────────────────────────

async def api_lobby(request: web.Request) -> web.Response:
    conn = db.get_connection()
    try:
        stake_levels = [10, 20, 50, 100]
        stakes_out = []
        for stake in stake_levels:
            # Find the best joinable game (most players, waiting status)
            best = conn.execute(
                "SELECT * FROM games WHERE stake=? AND status='waiting' ORDER BY player_count DESC LIMIT 1",
                (stake,)
            ).fetchone()
            # Count active games at this stake
            active_rows = conn.execute(
                "SELECT COUNT(*) as c FROM games WHERE stake=? AND status='active'",
                (stake,)
            ).fetchone()
            active_count = active_rows["c"] if active_rows else 0

            # Total players across all open (waiting+active) games
            pcount = conn.execute(
                "SELECT COALESCE(SUM(player_count),0) as t FROM games WHERE stake=? AND status IN ('waiting','active')",
                (stake,)
            ).fetchone()
            total_players = int(pcount["t"]) if pcount else 0

            # Pot across open games
            potrow = conn.execute(
                "SELECT COALESCE(SUM(total_pot),0) as t FROM games WHERE stake=? AND status IN ('waiting','active')",
                (stake,)
            ).fetchone()
            total_pot = float(potrow["t"]) if potrow else 0.0
            prize = round(total_pot * config.WINNER_PERCENTAGE, 2)

            # Jackpot = 10% of lifetime bets at this stake, capped at stake*100
            jp_row = conn.execute(
                "SELECT COALESCE(SUM(total_pot),0) as t FROM games WHERE stake=?",
                (stake,)
            ).fetchone()
            jackpot = round(float(jp_row["t"]) * 0.1, 2) if jp_row else 0.0
            jackpot_max = stake * 100

            stakes_out.append({
                "stake": stake,
                "label": f"{int(stake)} ETB",
                "best_game": dict(best) if best else None,
                "active_count": active_count,
                "total_players": total_players,
                "prize": prize,
                "jackpot": min(jackpot, jackpot_max),
                "jackpot_max": jackpot_max,
                "joinable": bool(best) or (active_count == 0),
            })

        # Demo info
        demo_waiting = conn.execute(
            "SELECT COUNT(*) as c FROM games WHERE stake=0 AND status='waiting'"
        ).fetchone()
        demo_active = conn.execute(
            "SELECT COUNT(*) as c FROM games WHERE stake=0 AND status='active'"
        ).fetchone()

        return _json({
            "stakes": stakes_out,
            "demo": {
                "active_count": (demo_active["c"] if demo_active else 0),
                "waiting_count": (demo_waiting["c"] if demo_waiting else 0),
            }
        })
    finally:
        conn.close()


# ── /api/game/join ────────────────────────────────────────────────────────────

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

    user = db.get_user(user_id)
    if not user:
        return _json({"error": "user not found"}, 404)

    # Check already in active game
    conn = db.get_connection()
    try:
        existing = conn.execute(
            "SELECT gp.game_id FROM game_players gp JOIN games g ON gp.game_id=g.game_id "
            "WHERE gp.user_id=? AND g.status IN ('waiting','active')",
            (user_id,)
        ).fetchone()
    finally:
        conn.close()

    if existing:
        game_id = existing["game_id"]
        game = db.get_game(game_id)
        player = db.get_player_in_game(game_id, user_id)
        return _json({"game_id": game_id, "game": game, "player": player, "rejoined": True})

    # Balance check
    if is_demo:
        if user["coin_balance"] < config.DEMO_COST_COINS:
            return _json({"error": "insufficient_coins",
                          "message": f"Demo costs {config.DEMO_COST_COINS} coins."}, 400)
    else:
        if user["wallet_balance"] < stake:
            return _json({"error": "insufficient_balance",
                          "message": f"Need {stake} ETB, you have {user['wallet_balance']:.2f} ETB."}, 400)

    # Find or create game
    waiting = db.get_waiting_game(stake)
    if waiting and waiting["player_count"] < config.MAX_PLAYERS:
        game_id = waiting["game_id"]
    else:
        game_id = generate_game_id()
        while db.get_game(game_id):
            game_id = generate_game_id()
        db.create_game(game_id, stake)

    # Generate board
    board = generate_bingo_board()
    board_number = get_board_number()
    flat_board = flatten_board(board)

    # Deduct stake
    if is_demo:
        db.update_coins(user_id, -config.DEMO_COST_COINS)
    else:
        db.update_balance(user_id, -stake)

    db.add_player_to_game(game_id, user_id, flat_board, board_number)
    conn = db.get_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE users SET total_games=total_games+1,updated_at=datetime('now') WHERE telegram_id=?",
                (user_id,)
            )
    finally:
        conn.close()

    game = db.get_game(game_id)
    player = db.get_player_in_game(game_id, user_id)

    # Broadcast to existing Mini App clients
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


# ── /api/game/state ───────────────────────────────────────────────────────────

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


# ── /api/game/claim-bingo ─────────────────────────────────────────────────────

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
        from broadcaster import broadcaster
        await broadcaster.broadcast(game_id, {"type": "player_eliminated", "user_id": user_id})
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
                "UPDATE users SET total_wins=total_wins+1,win_streak=win_streak+1 WHERE telegram_id=?",
                (user_id,)
            )
    finally:
        conn.close()

    user = db.get_user(user_id)
    board_number = player["board_numbers"][0] if player.get("board_numbers") else "-"

    from broadcaster import broadcaster
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


# ── /api/leaderboard ──────────────────────────────────────────────────────────

async def api_leaderboard(request: web.Request) -> web.Response:
    players = db.get_leaderboard(10)
    return _json({"players": players})


# ── /api/history ──────────────────────────────────────────────────────────────

async def api_history(request: web.Request) -> web.Response:
    user_id_str = request.rel_url.query.get("user_id")
    if not user_id_str:
        return _json({"transactions": []})
    txs = db.get_user_transactions(int(user_id_str), 20)
    return _json({"transactions": txs})


# ── /api/profile ──────────────────────────────────────────────────────────────

async def api_profile(request: web.Request) -> web.Response:
    user_id_str = request.rel_url.query.get("user_id")
    if not user_id_str:
        return _json({"error": "missing user_id"}, 400)
    user = db.get_user(int(user_id_str))
    if not user:
        return _json({"error": "user not found"}, 404)
    user_dict = dict(user)
    user_dict["is_admin"] = int(user_id_str) in config.ADMIN_IDS
    return _json({"user": user_dict})


# ── /api/admin/pending ────────────────────────────────────────────────────────

async def api_admin_pending(request: web.Request) -> web.Response:
    user_id_str = request.rel_url.query.get("user_id")
    if not user_id_str or int(user_id_str) not in config.ADMIN_IDS:
        return _json({"error": "unauthorized"}, 403)
    conn = db.get_connection()
    try:
        rows = conn.execute(
            "SELECT t.*, u.first_name, u.username FROM transactions t "
            "JOIN users u ON t.user_id=u.telegram_id "
            "WHERE t.status='pending' ORDER BY t.created_at DESC LIMIT 50"
        ).fetchall()
        return _json({"pending": [dict(r) for r in rows]})
    finally:
        conn.close()


# ── /api/admin/approve ────────────────────────────────────────────────────────

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

    conn = db.get_connection()
    try:
        tx = conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
        if not tx:
            return _json({"error": "transaction not found"}, 404)
        if tx["status"] != "pending":
            return _json({"error": "already processed"}, 400)
        with conn:
            conn.execute(
                "UPDATE transactions SET status='approved', approved_by=? WHERE id=?",
                (admin_id, tx_id)
            )
            if tx["type"] == "deposit":
                conn.execute(
                    "UPDATE users SET wallet_balance=wallet_balance+?,total_deposits=total_deposits+?,updated_at=datetime('now') WHERE telegram_id=?",
                    (tx["amount"], tx["amount"], tx["user_id"])
                )
        # Notify user via bot
        if _bot_application:
            asyncio.create_task(_notify_user(
                tx["user_id"],
                f"✅ Your deposit of {tx['amount']:.2f} ETB has been *approved*! Your balance has been updated.",
            ))
        return _json({"ok": True})
    finally:
        conn.close()


# ── /api/admin/reject ─────────────────────────────────────────────────────────

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

    conn = db.get_connection()
    try:
        tx = conn.execute("SELECT * FROM transactions WHERE id=?", (tx_id,)).fetchone()
        if not tx or tx["status"] != "pending":
            return _json({"error": "transaction not found or already processed"}, 400)
        with conn:
            conn.execute(
                "UPDATE transactions SET status='rejected', approved_by=? WHERE id=?",
                (admin_id, tx_id)
            )
        if _bot_application:
            asyncio.create_task(_notify_user(
                tx["user_id"],
                f"❌ Your deposit of {tx['amount']:.2f} ETB was *rejected*.\nReason: {reason}",
            ))
        return _json({"ok": True})
    finally:
        conn.close()


# ── /api/admin/stats ──────────────────────────────────────────────────────────

async def api_admin_stats(request: web.Request) -> web.Response:
    user_id_str = request.rel_url.query.get("user_id")
    if not user_id_str or int(user_id_str) not in config.ADMIN_IDS:
        return _json({"error": "unauthorized"}, 403)
    conn = db.get_connection()
    try:
        total_users = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
        active_games = conn.execute("SELECT COUNT(*) as c FROM games WHERE status='active'").fetchone()["c"]
        pending_deps = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE type='deposit' AND status='pending'").fetchone()["c"]
        pending_wds = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE type='withdraw' AND status='pending'").fetchone()["c"]
        total_deposits = conn.execute("SELECT COALESCE(SUM(amount),0) as t FROM transactions WHERE type='deposit' AND status='approved'").fetchone()["t"]
        total_withdrawals = conn.execute("SELECT COALESCE(SUM(amount),0) as t FROM transactions WHERE type='withdraw' AND status='approved'").fetchone()["t"]
        return _json({
            "total_users": total_users,
            "active_games": active_games,
            "pending_deposits": pending_deps,
            "pending_withdrawals": pending_wds,
            "total_deposits": round(float(total_deposits), 2),
            "total_withdrawals": round(float(total_withdrawals), 2),
            "house_balance": round(float(total_deposits) - float(total_withdrawals), 2),
        })
    finally:
        conn.close()


async def _notify_user(user_id: int, text: str) -> None:
    if not _bot_application:
        return
    try:
        await _bot_application.bot.send_message(user_id, text, parse_mode="Markdown")
    except Exception as e:
        logger.warning(f"Could not notify user {user_id}: {e}")


# ── aiohttp app factory ───────────────────────────────────────────────────────

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
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", port)
    await site.start()
    logger.info(f"REST API server running on 127.0.0.1:{port}")
    return runner, site
