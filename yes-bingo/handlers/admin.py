import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler
import database as db
import config
from utils import format_currency
from config import DAILY_BONUS_RANKS

logger = logging.getLogger(__name__)


def is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


async def admin_stats(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Unauthorized.")
        return
    stats = db.get_admin_stats()
    text = (
        f"📊 *Admin Stats*\n\n"
        f"👥 Total Users: {stats['users_count']}\n"
        f"💰 Total Deposits: {format_currency(stats['total_deposits'])}\n"
        f"💸 Total Withdrawals: {format_currency(stats['total_withdrawals'])}\n"
        f"🎮 Active Games: {stats['active_games']}\n"
        f"⏳ Pending Deposits: {stats['pending_deposits']}\n"
        f"⏳ Pending Withdrawals: {stats['pending_withdrawals']}\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def admin_deposit_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("❌ Unauthorized", show_alert=True)
        return

    parts = query.data.split("_")
    action = parts[2]
    tx_db_id = int(parts[3])

    tx = db.get_transaction(tx_db_id)
    if not tx:
        await query.edit_message_text("❌ Transaction not found.")
        return
    if tx["status"] != "pending":
        await query.edit_message_text(f"⚠️ Transaction already {tx['status']}.")
        return

    if action == "approve":
        db.approve_transaction(tx_db_id, query.from_user.id)
        db.mark_transaction_id_used(tx["transaction_id"])
        db.update_balance(tx["user_id"], tx["amount"])
        db.create_transaction(
            user_id=tx["user_id"],
            amount=tx["amount"],
            tx_type="deposit",
            transaction_id=tx["transaction_id"],
            description="Deposit approved",
            status="approved"
        )
        conn = db.get_connection()
        try:
            with conn:
                conn.execute(
                    "UPDATE users SET total_deposits = total_deposits + ?, updated_at = datetime('now') WHERE telegram_id = ?",
                    (tx["amount"], tx["user_id"])
                )
        finally:
            conn.close()
        try:
            await context.bot.send_message(
                tx["user_id"],
                f"✅ *Deposit Approved!*\n\n💰 {format_currency(tx['amount'])} added to your balance.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Could not notify user {tx['user_id']}: {e}")
        await query.edit_message_text(
            query.message.text + f"\n\n✅ *Approved by admin {query.from_user.first_name}*",
            parse_mode="Markdown"
        )
    else:
        db.reject_transaction(tx_db_id, query.from_user.id)
        try:
            await context.bot.send_message(
                tx["user_id"],
                f"❌ *Deposit Rejected*\n\n💵 Amount: {format_currency(tx['amount'])}\n\nContact support if you believe this is an error.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Could not notify user {tx['user_id']}: {e}")
        await query.edit_message_text(
            query.message.text + f"\n\n❌ *Rejected by admin {query.from_user.first_name}*",
            parse_mode="Markdown"
        )


async def admin_withdraw_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    if not is_admin(query.from_user.id):
        await query.answer("❌ Unauthorized", show_alert=True)
        return

    parts = query.data.split("_")
    action = parts[2]
    tx_db_id = int(parts[3])

    tx = db.get_transaction(tx_db_id)
    if not tx:
        await query.edit_message_text("❌ Transaction not found.")
        return
    if tx["status"] != "pending":
        await query.edit_message_text(f"⚠️ Transaction already {tx['status']}.")
        return

    if action == "approve":
        user = db.get_user(tx["user_id"])
        if not user or user["wallet_balance"] < tx["amount"]:
            await query.edit_message_text("❌ User has insufficient balance.")
            return
        db.approve_transaction(tx_db_id, query.from_user.id)
        db.update_balance(tx["user_id"], -tx["amount"])
        conn = db.get_connection()
        try:
            with conn:
                conn.execute(
                    "UPDATE users SET total_withdrawals = total_withdrawals + ?, updated_at = datetime('now') WHERE telegram_id = ?",
                    (tx["amount"], tx["user_id"])
                )
        finally:
            conn.close()
        try:
            await context.bot.send_message(
                tx["user_id"],
                f"✅ *Withdrawal Processed!*\n\n💸 {format_currency(tx['amount'])} sent to `{tx['telebirr_number']}`.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Could not notify user {tx['user_id']}: {e}")
        await query.edit_message_text(
            query.message.text + f"\n\n✅ *Processed by admin {query.from_user.first_name}*",
            parse_mode="Markdown"
        )
    else:
        db.reject_transaction(tx_db_id, query.from_user.id)
        try:
            await context.bot.send_message(
                tx["user_id"],
                f"❌ *Withdrawal Rejected*\n\n💵 Amount: {format_currency(tx['amount'])}\n\nYour balance was NOT deducted. Contact support if needed.",
                parse_mode="Markdown"
            )
        except Exception as e:
            logger.error(f"Could not notify user {tx['user_id']}: {e}")
        await query.edit_message_text(
            query.message.text + f"\n\n❌ *Rejected by admin {query.from_user.first_name}*",
            parse_mode="Markdown"
        )


async def admin_run_bonus(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Unauthorized.")
        return
    await run_daily_bonus(context)
    await update.message.reply_text("✅ Daily bonus distributed.")


async def run_daily_bonus(context: ContextTypes.DEFAULT_TYPE) -> None:
    top_users = db.get_top_users_for_bonus(10)
    for i, user in enumerate(top_users):
        rank = i + 1
        multiplier = DAILY_BONUS_RANKS.get(rank, 1.0)
        bonus_coins = int(user["coin_balance"] * multiplier)
        if bonus_coins > 0:
            db.update_coins(user["telegram_id"], bonus_coins)
            db.record_daily_bonus(user["telegram_id"], rank, bonus_coins)
            try:
                await context.bot.send_message(
                    user["telegram_id"],
                    f"🌟 *Daily Bonus!*\n\n"
                    f"🏆 Rank #{rank}\n"
                    f"🪙 Bonus Coins: +{bonus_coins}\n"
                    f"Congratulations! Keep playing to earn more!",
                    parse_mode="Markdown"
                )
            except Exception as e:
                logger.error(f"Could not notify bonus winner {user['telegram_id']}: {e}")
    logger.info("Daily bonus distributed")


def get_admin_handlers() -> list:
    return [
        CommandHandler("admin_stats", admin_stats),
        CommandHandler("admin_bonus", admin_run_bonus),
        CallbackQueryHandler(admin_deposit_callback, pattern=r"^adm_dep_"),
        CallbackQueryHandler(admin_withdraw_callback, pattern=r"^adm_wd_"),
    ]
