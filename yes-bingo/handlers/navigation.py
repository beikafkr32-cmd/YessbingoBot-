import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
import database as db
import config
from utils import (
    format_currency, build_main_menu, build_profile_text, build_history_text
)

logger = logging.getLogger(__name__)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user = update.effective_user
    args = context.args or []
    referral_code = args[0] if args else None

    is_new = db.register_user(user.id, user.username, user.first_name, referral_code)

    db_user = db.get_user(user.id)
    balance = db_user["wallet_balance"] if db_user else 0.0
    coins = db_user["coin_balance"] if db_user else 0

    conn = db.get_connection()
    try:
        active_games = conn.execute(
            "SELECT COUNT(*) as c FROM game_players gp JOIN games g ON gp.game_id = g.game_id "
            "WHERE gp.user_id = ? AND g.status IN ('waiting', 'active')",
            (user.id,)
        ).fetchone()["c"]
    finally:
        conn.close()

    text = build_main_menu(balance, coins, active_games)
    if is_new:
        text = f"👋 Welcome, {user.first_name}! You've been registered.\n\n" + text

    keyboard = [
        [InlineKeyboardButton("🎲 Play Bingo", callback_data="btn_playbingo"),
         InlineKeyboardButton("💰 Deposit", callback_data="btn_deposit")],
        [InlineKeyboardButton("💳 Balance", callback_data="btn_balance"),
         InlineKeyboardButton("💸 Withdraw", callback_data="btn_withdraw")],
        [InlineKeyboardButton("🏆 Scores", callback_data="btn_scores"),
         InlineKeyboardButton("📊 History", callback_data="btn_history")],
        [InlineKeyboardButton("👤 Profile", callback_data="btn_profile"),
         InlineKeyboardButton("📣 Invite", callback_data="btn_invite")],
        [InlineKeyboardButton("📝 Help", callback_data="btn_help")],
    ]
    await update.message.reply_text(
        text, parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    user = query.from_user
    db_user = db.get_user(user.id)
    balance = db_user["wallet_balance"] if db_user else 0.0
    coins = db_user["coin_balance"] if db_user else 0

    conn = db.get_connection()
    try:
        active_games = conn.execute(
            "SELECT COUNT(*) as c FROM game_players gp JOIN games g ON gp.game_id = g.game_id "
            "WHERE gp.user_id = ? AND g.status IN ('waiting', 'active')",
            (user.id,)
        ).fetchone()["c"]
    finally:
        conn.close()

    text = build_main_menu(balance, coins, active_games)
    keyboard = [
        [InlineKeyboardButton("🎲 Play Bingo", callback_data="btn_playbingo"),
         InlineKeyboardButton("💰 Deposit", callback_data="btn_deposit")],
        [InlineKeyboardButton("💳 Balance", callback_data="btn_balance"),
         InlineKeyboardButton("💸 Withdraw", callback_data="btn_withdraw")],
        [InlineKeyboardButton("🏆 Scores", callback_data="btn_scores"),
         InlineKeyboardButton("📊 History", callback_data="btn_history")],
        [InlineKeyboardButton("👤 Profile", callback_data="btn_profile"),
         InlineKeyboardButton("📣 Invite", callback_data="btn_invite")],
        [InlineKeyboardButton("📝 Help", callback_data="btn_help")],
    ]
    await query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def balance_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    db_user = db.get_user(user_id)
    if not db_user:
        target = update.message or update.callback_query.message
        await target.reply_text("Please /start first.")
        return
    text = (
        f"💳 *Your Balance*\n\n"
        f"💰 Wallet: {format_currency(db_user['wallet_balance'])}\n"
        f"🪙 Coins: {db_user['coin_balance']}\n\n"
        f"💵 Total Deposited: {format_currency(db_user['total_deposits'])}\n"
        f"💸 Total Withdrawn: {format_currency(db_user['total_withdrawals'])}"
    )
    keyboard = [
        [InlineKeyboardButton("💰 Deposit", callback_data="btn_deposit"),
         InlineKeyboardButton("💸 Withdraw", callback_data="btn_withdraw")],
        [InlineKeyboardButton("🔙 Back", callback_data="main_menu")],
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def scores_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    leaderboard = db.get_leaderboard(10)
    lines = ["🏆 *Top Players*\n"]
    medals = ["🥇", "🥈", "🥉"] + ["🏅"] * 7
    for i, u in enumerate(leaderboard):
        name = u["first_name"]
        lines.append(f"{medals[i]} {name} — 🪙 {u['coin_balance']} coins | 🏆 {u['total_wins']} wins")
    text = "\n".join(lines) if leaderboard else "🏆 *Leaderboard*\n\nNo players yet."
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    txs = db.get_user_transactions(user_id, 10)
    text = build_history_text(txs)
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def profile_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    db_user = db.get_user(user_id)
    if not db_user:
        target = update.message or update.callback_query.message
        await target.reply_text("Please /start first.")
        return
    text = build_profile_text(db_user)
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def invite_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    db_user = db.get_user(user_id)
    if not db_user:
        target = update.message or update.callback_query.message
        await target.reply_text("Please /start first.")
        return

    code = db_user.get("referral_code", "")
    bot_info = await context.bot.get_me()
    bot_username = bot_info.username
    link = f"https://t.me/{bot_username}?start={code}"

    conn = db.get_connection()
    try:
        ref_count = conn.execute("SELECT COUNT(*) as c FROM referrals WHERE referrer_id = ?", (user_id,)).fetchone()["c"]
    finally:
        conn.close()

    text = (
        f"📣 *Invite Friends & Earn Coins!*\n\n"
        f"🔗 Your Referral Link:\n`{link}`\n\n"
        f"🪙 Earn 1 coin per referral\n"
        f"💡 10 coins = 1 ETB (manual conversion)\n\n"
        f"👥 Total Referrals: {ref_count}\n"
        f"🪙 Total Coins: {db_user['coin_balance']}"
    )
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "📝 *Available Commands*\n\n"
        "/start \\- Start the bot\n"
        "/playbingo \\- Start playing Bingo\n"
        "/playspin \\- Start playing Spin \\(Coming Soon\\)\n"
        "/balance \\- Check account balance\n"
        "/deposit \\- Deposit funds\n"
        "/withdraw \\- Withdraw funds\n"
        "/history \\- View transaction history\n"
        "/invite \\- Get referral link\n"
        "/help \\- Show this help menu\n\n"
        "💡 Quick Tip: Use the buttons below for easy navigation\\!"
    )
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode="MarkdownV2", reply_markup=InlineKeyboardMarkup(keyboard))


async def playspin_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = "🎰 *Play Spin*\n\n⏳ Coming Soon! Stay tuned."
    keyboard = [[InlineKeyboardButton("🔙 Back", callback_data="main_menu")]]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))


async def transfer_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text("🔒 *Transfer*\n\n⏳ This feature is currently disabled.", parse_mode="Markdown")


async def convert_coins_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    db_user = db.get_user(user_id)
    if not db_user:
        await update.message.reply_text("Please /start first.")
        return
    coins = db_user["coin_balance"]
    convertible = coins // config.COINS_TO_ETB_RATE
    if convertible == 0:
        await update.message.reply_text(
            f"🪙 *Convert Coins*\n\n"
            f"You need at least {config.COINS_TO_ETB_RATE} coins to convert.\n"
            f"Your coins: {coins}",
            parse_mode="Markdown"
        )
        return
    etb_amount = convertible
    coins_used = convertible * config.COINS_TO_ETB_RATE
    db.update_coins(user_id, -coins_used)
    db.update_balance(user_id, etb_amount)
    db.create_transaction(user_id, etb_amount, "credit_conversion",
                          description=f"Converted {coins_used} coins to {etb_amount} ETB", status="approved")
    await update.message.reply_text(
        f"✅ *Coins Converted!*\n\n"
        f"🪙 Used: {coins_used} coins\n"
        f"💰 Added: {format_currency(etb_amount)}\n"
        f"🪙 Remaining: {coins - coins_used} coins",
        parse_mode="Markdown"
    )


async def button_router(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    data = query.data
    if data == "btn_playbingo":
        from handlers.game import playbingo_command
        await playbingo_command(update, context)
    elif data == "btn_balance":
        await balance_command(update, context)
    elif data == "btn_scores":
        await scores_command(update, context)
    elif data == "btn_history":
        await history_command(update, context)
    elif data == "btn_profile":
        await profile_command(update, context)
    elif data == "btn_invite":
        await invite_command(update, context)
    elif data == "btn_help":
        await help_command(update, context)
    elif data == "main_menu":
        await main_menu_callback(update, context)


def get_navigation_handlers() -> list:
    return [
        CommandHandler("start", start_command),
        CommandHandler("balance", balance_command),
        CommandHandler("history", history_command),
        CommandHandler("profile", profile_command),
        CommandHandler("invite", invite_command),
        CommandHandler("help", help_command),
        CommandHandler("playspin", playspin_command),
        CommandHandler("transfer", transfer_command),
        CommandHandler("convert", convert_coins_command),
        CallbackQueryHandler(button_router, pattern=r"^btn_|^main_menu$"),
    ]
