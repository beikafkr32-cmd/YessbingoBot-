import asyncio
import logging
import random
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
import database as db
import config
from utils import (
    generate_bingo_board, flatten_board, check_bingo,
    generate_game_id, get_board_number, format_currency, get_win_message
)
from broadcaster import broadcaster

logger = logging.getLogger(__name__)

active_game_tasks: dict[str, asyncio.Task] = {}
game_locks: dict[str, asyncio.Lock] = {}


def get_game_lock(game_id: str) -> asyncio.Lock:
    if game_id not in game_locks:
        game_locks[game_id] = asyncio.Lock()
    return game_locks[game_id]


def _lobby_url(user_id: int, stake: float | None = None) -> str | None:
    """Build the Mini App lobby URL for a given user and optional stake."""
    base = config.WEB_APP_URL
    if not base:
        return None
    url = f"{base}?user_id={user_id}"
    if stake is not None and stake > 0:
        url += f"&stake={int(stake)}"
    return url


async def playbingo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    if not user:
        await update.message.reply_text("Please /start first.")
        return

    lobby_url = _lobby_url(user_id)
    keyboard = []

    if lobby_url:
        keyboard.append([
            InlineKeyboardButton("🎮 Open Game Lobby", web_app=WebAppInfo(url=lobby_url))
        ])
    else:
        # Fallback: show inline stake keyboard when Mini App URL not configured
        keyboard = [
            [InlineKeyboardButton("💰 10 ETB", callback_data="join_10"),
             InlineKeyboardButton("💰 20 ETB", callback_data="join_20")],
            [InlineKeyboardButton("💰 50 ETB", callback_data="join_50"),
             InlineKeyboardButton("💰 100 ETB", callback_data="join_100")],
            [InlineKeyboardButton("🎮 FREE DEMO", callback_data="join_demo")],
        ]
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data="main_menu")])

    text = (
        f"🎯 *YES BINGO — Game Lobby*\n\n"
        f"💰 Balance: {format_currency(user['wallet_balance'])}\n"
        f"🪙 Coins: {user['coin_balance']}\n\n"
        f"Tap *Open Game Lobby* to see all active games, pick your stake, and join!\n\n"
        f"Available stakes: 10 · 20 · 50 · 100 ETB · FREE DEMO"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.message.reply_text(
            text, parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


async def join_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Fallback handler when Mini App is not available — joins inline."""
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = db.get_user(user_id)
    if not user:
        await query.answer("Please /start first.", show_alert=True)
        return

    data = query.data
    is_demo = data == "join_demo"
    stake = 0.0 if is_demo else float(data.split("_")[1])

    # If Mini App is available, open lobby with focused stake
    lobby_url = _lobby_url(user_id, stake if not is_demo else None)
    if lobby_url and not is_demo:
        keyboard = [[
            InlineKeyboardButton(
                f"🎮 Open {int(stake)} ETB Lobby",
                web_app=WebAppInfo(url=lobby_url)
            )
        ], [InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]
        await query.edit_message_text(
            f"🎯 *{int(stake)} ETB Games*\n\nTap the button to see active games and join!",
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    # Inline join fallback (no Mini App)
    if is_demo:
        if user["coin_balance"] < config.DEMO_COST_COINS:
            await query.edit_message_text(
                f"❌ Need {config.DEMO_COST_COINS} coins for demo.\nYour coins: {user['coin_balance']}"
            )
            return
    else:
        if user["wallet_balance"] < stake:
            await query.edit_message_text(
                f"❌ Insufficient balance!\nNeed: {format_currency(stake)}\n"
                f"Have: {format_currency(user['wallet_balance'])}"
            )
            return

    # Check existing game
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT gp.game_id FROM game_players gp JOIN games g ON gp.game_id=g.game_id "
            "WHERE gp.user_id=? AND g.status IN ('waiting','active')",
            (user_id,)
        ).fetchone()
        existing_game = row["game_id"] if row else None
    finally:
        conn.close()

    if existing_game:
        await query.edit_message_text(
            f"⚠️ Already in game `{existing_game}`.", parse_mode="Markdown"
        )
        return

    waiting_game = db.get_waiting_game(stake)
    if waiting_game and waiting_game["player_count"] < config.MAX_PLAYERS:
        game_id = waiting_game["game_id"]
    else:
        game_id = generate_game_id()
        while db.get_game(game_id):
            game_id = generate_game_id()
        db.create_game(game_id, stake)

    board = generate_bingo_board()
    board_number = get_board_number()
    flat_board = flatten_board(board)

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
    player_count = game["player_count"]

    await broadcaster.broadcast(game_id, {
        "type": "player_joined",
        "player_count": player_count,
        "total_pot": game["total_pot"],
    })

    # Build message
    mini_url = _lobby_url(user_id)
    keyboard_rows = []
    if mini_url:
        game_url = f"{config.WEB_APP_URL}?game_id={game_id}&user_id={user_id}"
        keyboard_rows.append([
            InlineKeyboardButton("🎮 Open Game Board", web_app=WebAppInfo(url=game_url))
        ])
    keyboard_rows.append([
        InlineKeyboardButton("🚪 Leave", callback_data=f"leave_{game_id}"),
        InlineKeyboardButton("📋 Add Board", callback_data=f"addboard_{game_id}"),
    ])

    text = (
        f"🎯 *Game: {game_id}*\n\n"
        f"💰 Stake: {format_currency(stake) if not is_demo else 'FREE DEMO'}\n"
        f"👥 Players: {player_count}/{config.MAX_PLAYERS}\n"
        f"💰 Pot: {format_currency(game['total_pot'])}\n"
        f"🎯 Board #: {board_number}\n\n"
        f"⏳ Waiting for players..."
    )
    await query.edit_message_text(text, parse_mode="Markdown",
                                   reply_markup=InlineKeyboardMarkup(keyboard_rows))

    if player_count >= 2 and game_id not in active_game_tasks:
        task = asyncio.create_task(run_game_countdown(game_id, context))
        active_game_tasks[game_id] = task


async def run_game_countdown(game_id: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        for remaining in range(config.COUNTDOWN_SECONDS, 0, -1):
            game = db.get_game(game_id)
            if not game or game["status"] != "waiting":
                return
            await broadcaster.broadcast(game_id, {"type": "countdown", "seconds": remaining})
            await asyncio.sleep(1)

        game = db.get_game(game_id)
        if not game or game["status"] != "waiting":
            return

        players = db.get_game_players(game_id)
        if len(players) < 2:
            for p in players:
                if game["stake"] > 0:
                    db.update_balance(p["user_id"], game["stake"])
                    db.create_transaction(p["user_id"], game["stake"], "refund",
                                          description="Game cancelled – not enough players")
                try:
                    await context.bot.send_message(p["user_id"],
                        "⚠️ Game cancelled — not enough players. Refund issued.")
                except Exception:
                    pass
            db.update_game(game_id, status="finished")
            await broadcaster.broadcast(game_id, {"type": "game_cancelled"})
            return

        db.update_game(game_id, status="active", started_at="datetime('now')",
                       board_number=random.randint(1, 9999))
        game = db.get_game(game_id)

        await broadcaster.broadcast(game_id, {
            "type": "game_start",
            "player_count": game["player_count"],
            "total_pot": game["total_pot"],
        })

        for p in players:
            try:
                await context.bot.send_message(
                    p["user_id"],
                    f"🎮 *Game {game_id} Started!*\n\n"
                    f"👥 {len(players)} players\n"
                    f"💰 Pot: {format_currency(game['total_pot'])}\n"
                    f"🏆 Prize: {format_currency(game['total_pot'] * config.WINNER_PERCENTAGE)}\n"
                    f"Numbers called every {config.CALL_INTERVAL}s. Good luck! 🍀",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        await run_game(game_id, context)

    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Game countdown error {game_id}: {e}", exc_info=True)
    finally:
        active_game_tasks.pop(game_id, None)
        game_locks.pop(game_id, None)


async def run_game(game_id: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    numbers = list(range(1, 76))
    random.shuffle(numbers)
    called: list[int] = []

    for number in numbers:
        game = db.get_game(game_id)
        if not game or game["status"] != "active":
            return

        called.append(number)
        db.update_game(game_id, called_numbers=called)

        await broadcaster.broadcast(game_id, {
            "type": "number_called",
            "number": number,
            "called": called,
            "remaining": 75 - len(called),
        })

        await asyncio.sleep(config.CALL_INTERVAL)

    game = db.get_game(game_id)
    if game and game["status"] == "active":
        await end_game_no_winner(game_id, context)


async def process_winner(game_id: str, winner_id: int, winner_name: str,
                         context: ContextTypes.DEFAULT_TYPE) -> None:
    game = db.get_game(game_id)
    if not game:
        return

    prize_pool = game["total_pot"] * config.WINNER_PERCENTAGE
    first_prize = prize_pool * config.FIRST_WINNER_SHARE

    db.update_game(game_id, status="finished", winner_id=winner_id, ended_at="datetime('now')")
    db.update_player(game_id, winner_id, is_winner=1)
    db.update_balance(winner_id, first_prize)
    db.create_transaction(winner_id, first_prize, "win", description=f"Win in {game_id}")

    conn = db.get_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE users SET total_wins=total_wins+1,win_streak=win_streak+1,updated_at=datetime('now') WHERE telegram_id=?",
                (winner_id,)
            )
    finally:
        conn.close()

    player = db.get_player_in_game(game_id, winner_id)
    board_number = player["board_numbers"][0] if player and player["board_numbers"] else 0

    await broadcaster.broadcast(game_id, {
        "type": "game_end",
        "winner_id": winner_id,
        "winner_name": winner_name,
        "board_number": board_number,
        "amount": round(first_prize, 2),
    })

    players = db.get_game_players(game_id)
    win_msg = get_win_message(winner_name, board_number, first_prize)
    for p in players:
        try:
            await context.bot.send_message(p["user_id"], win_msg, parse_mode="Markdown")
        except Exception:
            pass

    if game_id in active_game_tasks:
        active_game_tasks[game_id].cancel()


async def end_game_no_winner(game_id: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    game = db.get_game(game_id)
    if not game:
        return
    db.update_game(game_id, status="finished", ended_at="datetime('now')")
    await broadcaster.broadcast(game_id, {"type": "game_no_winner"})

    players = db.get_game_players(game_id)
    stake = game["stake"]
    for p in players:
        if stake > 0:
            db.update_balance(p["user_id"], stake)
            db.create_transaction(p["user_id"], stake, "refund",
                                  description=f"Refund – no winner in {game_id}")
        try:
            await context.bot.send_message(
                p["user_id"],
                f"🎮 *Game {game_id}* — No winner.\n"
                f"{'💰 Refunded: ' + format_currency(stake) if stake > 0 else ''}",
                parse_mode="Markdown"
            )
        except Exception:
            pass


async def claim_bingo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    game_id = query.data.split("_")[1]

    async with get_game_lock(game_id):
        game = db.get_game(game_id)
        if not game or game["status"] != "active":
            await query.answer("Game not active.", show_alert=True)
            return

        player = db.get_player_in_game(game_id, user_id)
        if not player or player["is_eliminated"]:
            await query.answer("Not in game or eliminated.", show_alert=True)
            return

        called = game["called_numbers"]
        has_bingo = any(
            check_bingo([[b[r * 5 + c] for r in range(5)] for c in range(5)], called)
            for b in [player["main_board"]] + player["extra_boards"]
        )

        if not has_bingo:
            db.update_player(game_id, user_id, is_eliminated=1)
            await broadcaster.broadcast(game_id, {"type": "player_eliminated", "user_id": user_id})
            await query.edit_message_text(
                f"❌ *False BINGO!* — Eliminated from game {game_id}.", parse_mode="Markdown"
            )
            players = db.get_game_players(game_id)
            if not any(p for p in players if not p["is_eliminated"]):
                await end_game_no_winner(game_id, context)
            return

        await process_winner(game_id, user_id, query.from_user.first_name, context)
        await query.edit_message_text("🎉 *BINGO! You won!*", parse_mode="Markdown")


async def leave_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    game_id = query.data.split("_")[1]

    game = db.get_game(game_id)
    if not game:
        await query.edit_message_text("❌ Game not found.")
        return

    player = db.get_player_in_game(game_id, user_id)
    if not player:
        await query.edit_message_text("❌ Not in this game.")
        return

    if game["status"] == "waiting":
        stake = game["stake"]
        db.remove_player_from_game(game_id, user_id)
        if stake > 0:
            db.update_balance(user_id, stake)
        await broadcaster.broadcast(game_id, {
            "type": "player_left", "user_id": user_id,
            "player_count": max(0, game["player_count"] - 1),
        })
        await query.edit_message_text(
            f"✅ Left game {game_id}.\n"
            f"{'💰 Refunded: ' + format_currency(stake) if stake > 0 else ''}"
        )
    elif game["status"] == "active":
        await query.edit_message_text("❌ Can't leave — game already started.")
    else:
        await query.edit_message_text("❌ Game already finished.")


async def add_board_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    game_id = query.data.split("_")[1]

    game = db.get_game(game_id)
    if not game or game["status"] not in ("waiting", "active"):
        await query.answer("Game not available.", show_alert=True)
        return

    player = db.get_player_in_game(game_id, user_id)
    if not player:
        await query.answer("Not in this game.", show_alert=True)
        return
    if len(player["extra_boards"]) >= config.MAX_EXTRA_BOARDS:
        await query.answer(f"Max {config.MAX_EXTRA_BOARDS} extra boards.", show_alert=True)
        return

    user = db.get_user(user_id)
    if not user or user["wallet_balance"] < config.EXTRA_BOARD_COST:
        await query.answer(
            f"Insufficient balance. Extra board costs {format_currency(config.EXTRA_BOARD_COST)}.",
            show_alert=True
        )
        return

    board = generate_bingo_board()
    flat = flatten_board(board)
    board_number = get_board_number() + 100

    extra_boards  = player["extra_boards"] + [flat]
    board_numbers = player["board_numbers"] + [board_number]

    db.update_balance(user_id, -config.EXTRA_BOARD_COST)
    db.update_player(game_id, user_id, extra_boards=extra_boards, board_numbers=board_numbers)

    is_last      = len(extra_boards) >= config.MAX_EXTRA_BOARDS
    coin_reward  = config.EXTRA_BOARD_ALL_COIN_REWARD if is_last else config.EXTRA_BOARD_COIN_REWARD
    db.update_coins(user_id, coin_reward)
    db.create_transaction(user_id, config.EXTRA_BOARD_COST, "extra_board",
                          description=f"Extra board #{board_number} in {game_id}")

    await broadcaster.broadcast(game_id, {
        "type": "board_added", "flat": flat, "board_number": board_number,
        "_target_user": user_id,
    })

    await query.answer(f"✅ Extra board #{board_number} added! +{coin_reward} coin(s)",
                       show_alert=True)


def get_game_handlers() -> list:
    return [
        CommandHandler("playbingo", playbingo_command),
        CallbackQueryHandler(join_game_callback, pattern=r"^join_"),
        CallbackQueryHandler(claim_bingo_callback, pattern=r"^bingo_"),
        CallbackQueryHandler(leave_game_callback, pattern=r"^leave_"),
        CallbackQueryHandler(add_board_callback, pattern=r"^addboard_"),
    ]
