import asyncio
import logging
import random
from typing import Optional
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CallbackQueryHandler, CommandHandler
import database as db
import config
from utils import (
    generate_bingo_board, flatten_board, check_bingo,
    generate_game_id, get_board_number, format_currency, get_win_message
)

logger = logging.getLogger(__name__)

active_game_tasks: dict[str, asyncio.Task] = {}
game_locks: dict[str, asyncio.Lock] = {}


def get_game_lock(game_id: str) -> asyncio.Lock:
    if game_id not in game_locks:
        game_locks[game_id] = asyncio.Lock()
    return game_locks[game_id]


async def playbingo_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    if not user:
        await update.message.reply_text("Please /start first.")
        return

    keyboard = [
        [InlineKeyboardButton("💰 10 ETB", callback_data="join_10"),
         InlineKeyboardButton("💰 20 ETB", callback_data="join_20")],
        [InlineKeyboardButton("💰 50 ETB", callback_data="join_50"),
         InlineKeyboardButton("💰 100 ETB", callback_data="join_100")],
        [InlineKeyboardButton("🎮 FREE DEMO (10 coins)", callback_data="join_demo")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
    ]
    text = (
        f"🎯 *Select Your Bet*\n\n"
        f"💰 Balance: {format_currency(user['wallet_balance'])}\n"
        f"🪙 Coins: {user['coin_balance']}"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def join_game_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    user = db.get_user(user_id)
    if not user:
        await query.answer("Please /start first.", show_alert=True)
        return

    data = query.data
    is_demo = data == "join_demo"

    if is_demo:
        stake = 0.0
        if user["coin_balance"] < config.DEMO_COST_COINS:
            await query.edit_message_text(
                f"❌ Not enough coins!\n\nDemo costs {config.DEMO_COST_COINS} coins.\n"
                f"Your coins: {user['coin_balance']}\n\nEarn coins by referring friends!"
            )
            return
    else:
        stake = float(data.split("_")[1])
        if user["wallet_balance"] < stake:
            await query.edit_message_text(
                f"❌ Insufficient balance!\n\n"
                f"Required: {format_currency(stake)}\n"
                f"Your balance: {format_currency(user['wallet_balance'])}\n\n"
                f"Use /deposit to add funds."
            )
            return

    existing_game = None
    conn = db.get_connection()
    try:
        row = conn.execute(
            "SELECT gp.game_id FROM game_players gp JOIN games g ON gp.game_id = g.game_id "
            "WHERE gp.user_id = ? AND g.status IN ('waiting', 'active')",
            (user_id,)
        ).fetchone()
        if row:
            existing_game = row["game_id"]
    finally:
        conn.close()

    if existing_game:
        await query.edit_message_text(
            f"⚠️ You are already in game `{existing_game}`.\n\nUse /leave to exit first.",
            parse_mode="Markdown"
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
                "UPDATE users SET total_games = total_games + 1, updated_at = datetime('now') WHERE telegram_id = ?",
                (user_id,)
            )
    finally:
        conn.close()

    game = db.get_game(game_id)
    player_count = game["player_count"]

    web_app_url = config.WEB_APP_URL
    keyboard_rows = []
    if web_app_url:
        from telegram import WebAppInfo
        keyboard_rows.append([InlineKeyboardButton("🎮 Open Game Board", web_app=WebAppInfo(url=f"{web_app_url}?game_id={game_id}&user_id={user_id}"))])
    keyboard_rows.append([
        InlineKeyboardButton("🚪 Leave Game", callback_data=f"leave_{game_id}"),
        InlineKeyboardButton("📋 Add Board", callback_data=f"addboard_{game_id}"),
    ])

    text = (
        f"🎯 *Game: {game_id}*\n\n"
        f"💰 Stake: {format_currency(stake) if not is_demo else 'FREE DEMO'}\n"
        f"👥 Players: {player_count}/{config.MAX_PLAYERS}\n"
        f"💰 Pot: {format_currency(game['total_pot'])}\n"
        f"🎯 Your Board #: {board_number}\n\n"
        f"⏳ Waiting for players... Game starts when {config.COUNTDOWN_SECONDS}s countdown begins (min 2 players)."
    )
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard_rows))

    if player_count >= 2 and game_id not in active_game_tasks:
        task = asyncio.create_task(run_game_countdown(game_id, context))
        active_game_tasks[game_id] = task


async def run_game_countdown(game_id: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await asyncio.sleep(config.COUNTDOWN_SECONDS)
        game = db.get_game(game_id)
        if not game or game["status"] != "waiting":
            return
        players = db.get_game_players(game_id)
        if len(players) < 2:
            for p in players:
                stake = game["stake"]
                if stake > 0:
                    db.update_balance(p["user_id"], stake)
                    db.create_transaction(p["user_id"], stake, "refund", description="Game cancelled - not enough players")
                try:
                    await context.bot.send_message(p["user_id"], "⚠️ Game cancelled - not enough players. Refund issued.")
                except Exception:
                    pass
            db.update_game(game_id, status="finished")
            return

        db.update_game(game_id, status="active", started_at="datetime('now')", board_number=random.randint(1, 9999))
        for p in players:
            try:
                await context.bot.send_message(
                    p["user_id"],
                    f"🎮 *Game {game_id} Started!*\n\n"
                    f"👥 {len(players)} players\n"
                    f"💰 Pot: {format_currency(game['total_pot'])}\n"
                    f"🏆 Prize: {format_currency(game['total_pot'] * config.WINNER_PERCENTAGE)}\n\n"
                    f"Numbers will be called every {config.CALL_INTERVAL} seconds. Good luck!",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        await run_game(game_id, context)
    except asyncio.CancelledError:
        pass
    except Exception as e:
        logger.error(f"Game countdown error for {game_id}: {e}")
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

        players = db.get_game_players(game_id)
        active_players = [p for p in players if not p["is_eliminated"]]
        for p in active_players:
            try:
                await context.bot.send_message(
                    p["user_id"],
                    f"📢 *{game_id}* — Called: *{number}*\n"
                    f"Called {len(called)}/75 numbers",
                    parse_mode="Markdown"
                )
            except Exception:
                pass

        await asyncio.sleep(config.CALL_INTERVAL)

    game = db.get_game(game_id)
    if game and game["status"] == "active":
        await end_game_no_winner(game_id, context)


async def claim_bingo_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    game_id = data.split("_")[1]

    async with get_game_lock(game_id):
        game = db.get_game(game_id)
        if not game or game["status"] != "active":
            await query.answer("Game is not active.", show_alert=True)
            return

        player = db.get_player_in_game(game_id, user_id)
        if not player:
            await query.answer("You are not in this game.", show_alert=True)
            return
        if player["is_eliminated"]:
            await query.answer("You have been eliminated.", show_alert=True)
            return

        called = game["called_numbers"]
        boards_to_check = [player["main_board"]] + player["extra_boards"]
        has_bingo = any(check_bingo(_reshape_board(b), called) for b in boards_to_check)

        if not has_bingo:
            db.update_player(game_id, user_id, is_eliminated=1)
            await query.edit_message_text(
                f"❌ *False BINGO Claim!*\n\nYou have been eliminated from game {game_id}.",
                parse_mode="Markdown"
            )
            players = db.get_game_players(game_id)
            active = [p for p in players if not p["is_eliminated"]]
            if len(active) == 0:
                await end_game_no_winner(game_id, context)
            return

        await process_winner(game_id, user_id, query.from_user.first_name, context)
        await query.edit_message_text(
            f"🎉 *BINGO! You won!*\n\nCongratulations! Check the prize details.",
            parse_mode="Markdown"
        )


def _reshape_board(flat: list) -> list[list]:
    board = []
    for col in range(5):
        board.append([flat[col * 5 + row] if col * 5 + row < len(flat) else None for row in range(5)])
    return board


async def process_winner(game_id: str, winner_id: int, winner_name: str, context: ContextTypes.DEFAULT_TYPE) -> None:
    game = db.get_game(game_id)
    if not game:
        return

    total_pot = game["total_pot"]
    prize_pool = total_pot * config.WINNER_PERCENTAGE
    first_prize = prize_pool * config.FIRST_WINNER_SHARE

    db.update_game(game_id, status="finished", winner_id=winner_id, ended_at="datetime('now')")
    db.update_player(game_id, winner_id, is_winner=1)
    db.update_balance(winner_id, first_prize)
    db.create_transaction(winner_id, first_prize, "win", description=f"Bingo win in game {game_id}")
    conn = db.get_connection()
    try:
        with conn:
            conn.execute(
                "UPDATE users SET total_wins = total_wins + 1, win_streak = win_streak + 1, updated_at = datetime('now') WHERE telegram_id = ?",
                (winner_id,)
            )
    finally:
        conn.close()

    player = db.get_player_in_game(game_id, winner_id)
    board_number = player["board_numbers"][0] if player and player["board_numbers"] else 0

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
    players = db.get_game_players(game_id)
    stake = game["stake"]
    for p in players:
        if stake > 0:
            db.update_balance(p["user_id"], stake)
            db.create_transaction(p["user_id"], stake, "refund", description=f"Refund - no winner in game {game_id}")
        try:
            await context.bot.send_message(
                p["user_id"],
                f"🎮 *Game {game_id} Over*\n\nAll 75 numbers called with no winner.\n"
                f"💰 Your stake of {format_currency(stake)} has been refunded.",
                parse_mode="Markdown"
            )
        except Exception:
            pass


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
        await query.edit_message_text("❌ You are not in this game.")
        return

    if game["status"] == "waiting":
        stake = game["stake"]
        db.remove_player_from_game(game_id, user_id)
        if stake > 0:
            db.update_balance(user_id, stake)
        await query.edit_message_text(
            f"✅ Left game {game_id}.\n"
            f"{'💰 Refunded: ' + format_currency(stake) if stake > 0 else ''}"
        )
    elif game["status"] == "active":
        await query.edit_message_text(
            f"❌ Game already started!\n\nYou cannot get a refund after the game begins.\n"
            f"Your stake stays in the pot."
        )
    else:
        await query.edit_message_text("❌ Game is already finished.")


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
        await query.answer("You are not in this game.", show_alert=True)
        return

    if len(player["extra_boards"]) >= config.MAX_EXTRA_BOARDS:
        await query.answer(f"Maximum {config.MAX_EXTRA_BOARDS} extra boards allowed.", show_alert=True)
        return

    user = db.get_user(user_id)
    if not user or user["wallet_balance"] < config.EXTRA_BOARD_COST:
        await query.answer(f"Insufficient balance. Extra board costs {format_currency(config.EXTRA_BOARD_COST)}.", show_alert=True)
        return

    board = generate_bingo_board()
    flat = flatten_board(board)
    board_number = get_board_number() + 100

    extra_boards = player["extra_boards"] + [flat]
    board_numbers = player["board_numbers"] + [board_number]

    db.update_balance(user_id, -config.EXTRA_BOARD_COST)
    db.update_player(game_id, user_id, extra_boards=extra_boards, board_numbers=board_numbers)

    is_last_board = len(extra_boards) >= config.MAX_EXTRA_BOARDS
    coin_reward = config.EXTRA_BOARD_ALL_COIN_REWARD if is_last_board else config.EXTRA_BOARD_COIN_REWARD
    db.update_coins(user_id, coin_reward)
    db.create_transaction(user_id, config.EXTRA_BOARD_COST, "extra_board", description=f"Extra board #{board_number} in game {game_id}")

    await query.answer(f"✅ Extra board #{board_number} added! +{coin_reward} coin(s)", show_alert=True)


def get_game_handlers() -> list:
    return [
        CommandHandler("playbingo", playbingo_command),
        CallbackQueryHandler(join_game_callback, pattern=r"^join_"),
        CallbackQueryHandler(claim_bingo_callback, pattern=r"^bingo_"),
        CallbackQueryHandler(leave_game_callback, pattern=r"^leave_"),
        CallbackQueryHandler(add_board_callback, pattern=r"^addboard_"),
    ]
