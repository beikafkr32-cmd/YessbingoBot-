import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
import database as db
import config
from utils import format_currency

logger = logging.getLogger(__name__)

AWAIT_WD_AMOUNT, AWAIT_WD_NUMBER = range(2)


async def withdraw_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    if not user:
        await update.message.reply_text("Please /start first.")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("100 ETB", callback_data="wd_100"),
         InlineKeyboardButton("200 ETB", callback_data="wd_200")],
        [InlineKeyboardButton("500 ETB", callback_data="wd_500"),
         InlineKeyboardButton("✏️ Custom Amount", callback_data="wd_custom")],
        [InlineKeyboardButton("🔙 Back", callback_data="wd_cancel")],
    ]
    text = (
        f"💸 *Withdraw Funds*\n\n"
        f"💰 Your Balance: {format_currency(user['wallet_balance'])}\n"
        f"Minimum withdrawal: {format_currency(config.MIN_WITHDRAW)}\n\n"
        f"Select or enter the amount to withdraw:"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    return AWAIT_WD_AMOUNT


async def withdraw_amount_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "wd_cancel":
        await query.edit_message_text("❌ Withdrawal cancelled.")
        return ConversationHandler.END

    if data == "wd_custom":
        await query.edit_message_text(
            f"✏️ *Enter Amount*\n\nMinimum: {format_currency(config.MIN_WITHDRAW)}\n\nType the amount in ETB:",
            parse_mode="Markdown"
        )
        return AWAIT_WD_AMOUNT

    amount = float(data.split("_")[1])
    user_id = query.from_user.id
    user = db.get_user(user_id)
    if not user or user["wallet_balance"] < amount:
        await query.edit_message_text(
            f"❌ Insufficient balance.\n\nYour balance: {format_currency(user['wallet_balance'] if user else 0.0)}"
        )
        return ConversationHandler.END

    context.user_data["wd_amount"] = amount
    await query.edit_message_text(
        f"📱 *Enter Your Telebirr Number*\n\nAmount: {format_currency(amount)}\n\nType your Telebirr phone number:",
        parse_mode="Markdown"
    )
    return AWAIT_WD_NUMBER


async def withdraw_amount_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user_id = update.effective_user.id
    user = db.get_user(user_id)
    try:
        amount = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid amount.")
        return AWAIT_WD_AMOUNT

    if amount < config.MIN_WITHDRAW:
        await update.message.reply_text(f"❌ Minimum withdrawal is {format_currency(config.MIN_WITHDRAW)}.")
        return AWAIT_WD_AMOUNT

    if not user or user["wallet_balance"] < amount:
        await update.message.reply_text(
            f"❌ Insufficient balance.\n\nYour balance: {format_currency(user['wallet_balance'] if user else 0.0)}"
        )
        return ConversationHandler.END

    context.user_data["wd_amount"] = amount
    await update.message.reply_text(
        f"📱 *Enter Your Telebirr Number*\n\nAmount: {format_currency(amount)}\n\nType your Telebirr phone number:",
        parse_mode="Markdown"
    )
    return AWAIT_WD_NUMBER


async def withdraw_number(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    telebirr_number = update.message.text.strip()
    user_id = update.effective_user.id
    amount = context.user_data.get("wd_amount", 0.0)

    import re
    if not re.match(r"^0[79]\d{8}$", telebirr_number):
        await update.message.reply_text("❌ Invalid phone number. Use format: 09XXXXXXXX or 07XXXXXXXX")
        return AWAIT_WD_NUMBER

    user = db.get_user(user_id)
    if not user or user["wallet_balance"] < amount:
        await update.message.reply_text("❌ Insufficient balance.")
        return ConversationHandler.END

    tx_db_id = db.create_transaction(
        user_id=user_id,
        amount=amount,
        tx_type="withdraw",
        telebirr_number=telebirr_number,
        description=f"Withdrawal to {telebirr_number}"
    )

    admin_text = (
        f"💸 *New Withdrawal Request*\n\n"
        f"👤 User: {user['first_name']} (ID: {user_id})\n"
        f"💵 Amount: {format_currency(amount)}\n"
        f"📱 Telebirr: `{telebirr_number}`\n"
        f"💰 User Balance: {format_currency(user['wallet_balance'])}"
    )
    keyboard = [
        [InlineKeyboardButton("💸 Money Sent ✅", callback_data=f"adm_wd_approve_{tx_db_id}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"adm_wd_reject_{tx_db_id}")]
    ]
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, admin_text, parse_mode="Markdown",
                                            reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

    await update.message.reply_text(
        f"✅ *Withdrawal Request Submitted!*\n\n"
        f"💵 Amount: {format_currency(amount)}\n"
        f"📱 To: `{telebirr_number}`\n\n"
        f"⏳ Pending admin approval. You will be notified once processed.",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


def get_withdraw_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("withdraw", withdraw_start),
            CallbackQueryHandler(withdraw_start, pattern="^btn_withdraw$"),
        ],
        states={
            AWAIT_WD_AMOUNT: [
                CallbackQueryHandler(withdraw_amount_callback, pattern="^wd_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_amount_text),
            ],
            AWAIT_WD_NUMBER: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, withdraw_number),
            ],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        per_message=False,
    )
