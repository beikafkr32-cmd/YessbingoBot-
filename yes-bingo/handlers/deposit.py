import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, MessageHandler, CallbackQueryHandler, filters
import database as db
import config
from utils import parse_telebirr_sms, format_currency

logger = logging.getLogger(__name__)

AWAIT_AMOUNT, AWAIT_SMS = range(2)


async def deposit_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    user = update.effective_user
    if not db.get_user(user.id):
        await update.message.reply_text("Please /start first.")
        return ConversationHandler.END

    keyboard = [
        [InlineKeyboardButton("30 ETB", callback_data="dep_30"),
         InlineKeyboardButton("50 ETB", callback_data="dep_50")],
        [InlineKeyboardButton("100 ETB", callback_data="dep_100"),
         InlineKeyboardButton("200 ETB", callback_data="dep_200")],
        [InlineKeyboardButton("500 ETB", callback_data="dep_500"),
         InlineKeyboardButton("✏️ Custom Amount", callback_data="dep_custom")],
        [InlineKeyboardButton("🔙 Back", callback_data="dep_cancel")],
    ]
    text = (
        f"💰 *Deposit Funds*\n\n"
        f"Minimum deposit: {format_currency(config.MIN_DEPOSIT)}\n"
        f"Send to Telebirr:\n"
        f"📱 Number: `{config.TELEBIRR_NUMBER}`\n"
        f"👤 Name: *{config.TELEBIRR_NAME}*\n\n"
        f"Select or enter the amount you want to deposit:"
    )
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, parse_mode="Markdown", reply_markup=InlineKeyboardMarkup(keyboard))
    return AWAIT_AMOUNT


async def deposit_amount_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "dep_cancel":
        await query.edit_message_text("❌ Deposit cancelled.")
        return ConversationHandler.END

    if data == "dep_custom":
        await query.edit_message_text(
            f"✏️ *Enter Amount*\n\nMinimum: {format_currency(config.MIN_DEPOSIT)}\n\nType the amount in ETB:",
            parse_mode="Markdown"
        )
        context.user_data["dep_custom"] = True
        return AWAIT_AMOUNT

    amount = float(data.split("_")[1])
    context.user_data["deposit_amount"] = amount
    return await ask_for_sms(update, context, amount)


async def deposit_amount_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    try:
        amount = float(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Please enter a number.")
        return AWAIT_AMOUNT

    if amount < config.MIN_DEPOSIT:
        await update.message.reply_text(f"❌ Minimum deposit is {format_currency(config.MIN_DEPOSIT)}.")
        return AWAIT_AMOUNT

    context.user_data["deposit_amount"] = amount
    return await ask_for_sms(update, context, amount)


async def ask_for_sms(update: Update, context: ContextTypes.DEFAULT_TYPE, amount: float) -> int:
    context.user_data["deposit_amount"] = amount
    text = (
        f"📨 *Send {format_currency(amount)} via Telebirr*\n\n"
        f"📱 Number: `{config.TELEBIRR_NUMBER}`\n"
        f"👤 Name: *{config.TELEBIRR_NAME}*\n\n"
        f"After sending, paste the *SMS confirmation* you received from Telebirr:\n\n"
        f"_Example: You have sent ETB 100 to 0928641996. Ref: TB123456789_"
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, parse_mode="Markdown")
    return AWAIT_SMS


async def deposit_sms(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    sms_text = update.message.text.strip()
    user_id = update.effective_user.id
    expected_amount = context.user_data.get("deposit_amount")

    parsed = parse_telebirr_sms(sms_text)
    if not parsed:
        await update.message.reply_text(
            "❌ Could not read your SMS. Please paste the *exact* SMS text from Telebirr.\n\n"
            "Try again or send /cancel to stop.",
            parse_mode="Markdown"
        )
        return AWAIT_SMS

    sms_amount = parsed["amount"]
    tx_id = parsed["transaction_id"]

    if db.is_transaction_id_used(tx_id):
        await update.message.reply_text(
            "❌ This transaction ID has already been used. Please send a new transfer.",
            parse_mode="Markdown"
        )
        return ConversationHandler.END

    final_amount = sms_amount
    if expected_amount and abs(sms_amount - expected_amount) > 0.01:
        if sms_amount < expected_amount:
            keyboard = [
                [InlineKeyboardButton(f"✅ Deposit {format_currency(sms_amount)}", callback_data=f"dep_confirm_{sms_amount}_{tx_id}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="dep_cancel_final")],
            ]
            await update.message.reply_text(
                f"⚠️ *Amount Mismatch*\n\n"
                f"Expected: {format_currency(expected_amount)}\n"
                f"Sent: {format_currency(sms_amount)}\n\n"
                f"Do you want to deposit the lower amount?",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            context.user_data["sms_text"] = sms_text
            return ConversationHandler.END
        else:
            keyboard = [
                [InlineKeyboardButton(f"💰 Deposit {format_currency(sms_amount)}", callback_data=f"dep_confirm_{sms_amount}_{tx_id}")],
                [InlineKeyboardButton(f"💰 Deposit {format_currency(expected_amount)} + Save Credit", callback_data=f"dep_confirm_{expected_amount}_{tx_id}_credit_{sms_amount - expected_amount}")],
                [InlineKeyboardButton("❌ Cancel", callback_data="dep_cancel_final")],
            ]
            await update.message.reply_text(
                f"⚠️ *Overpayment Detected*\n\n"
                f"Expected: {format_currency(expected_amount)}\n"
                f"Sent: {format_currency(sms_amount)}\n\n"
                f"Choose how to handle the extra {format_currency(sms_amount - expected_amount)}:",
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            context.user_data["sms_text"] = sms_text
            return ConversationHandler.END

    context.user_data["sms_text"] = sms_text
    await submit_deposit_request(update, context, user_id, final_amount, tx_id, sms_text)
    return ConversationHandler.END


async def submit_deposit_request(update: Update, context: ContextTypes.DEFAULT_TYPE,
                                  user_id: int, amount: float, tx_id: str, sms_text: str,
                                  credit_amount: float = 0.0) -> None:
    tx_db_id = db.create_transaction(
        user_id=user_id,
        amount=amount,
        tx_type="deposit",
        transaction_id=tx_id,
        sms_text=sms_text,
        description=f"Deposit request via Telebirr. TX: {tx_id}" + (f" Credit: {credit_amount} ETB" if credit_amount else "")
    )
    user = db.get_user(user_id)
    user_name = user["first_name"] if user else "Unknown"
    admin_text = (
        f"💰 *New Deposit Request*\n\n"
        f"👤 User: {user_name} (ID: {user_id})\n"
        f"💵 Amount: {format_currency(amount)}\n"
        f"🔑 TX ID: `{tx_id}`\n"
        f"📱 SMS: {sms_text[:200]}"
        + (f"\n💳 Extra Credit: {format_currency(credit_amount)}" if credit_amount > 0 else "")
    )
    keyboard = [
        [InlineKeyboardButton("✅ Approve", callback_data=f"adm_dep_approve_{tx_db_id}"),
         InlineKeyboardButton("❌ Reject", callback_data=f"adm_dep_reject_{tx_db_id}")]
    ]
    for admin_id in config.ADMIN_IDS:
        try:
            await context.bot.send_message(admin_id, admin_text, parse_mode="Markdown",
                                            reply_markup=InlineKeyboardMarkup(keyboard))
        except Exception as e:
            logger.error(f"Failed to notify admin {admin_id}: {e}")

    reply_text = (
        f"✅ *Deposit Request Submitted!*\n\n"
        f"💵 Amount: {format_currency(amount)}\n"
        f"🔑 TX ID: `{tx_id}`\n\n"
        f"⏳ Your request is pending admin approval. You will be notified once approved."
    )
    msg = update.callback_query.message if update.callback_query else update.message
    await msg.reply_text(reply_text, parse_mode="Markdown")


async def deposit_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "dep_cancel_final":
        await query.edit_message_text("❌ Deposit cancelled.")
        return

    parts = data.split("_")
    amount = float(parts[2])
    tx_id = parts[3]
    credit = float(parts[5]) if len(parts) > 5 else 0.0
    sms_text = context.user_data.get("sms_text", "")
    user_id = query.from_user.id
    await submit_deposit_request(update, context, user_id, amount, tx_id, sms_text, credit)


def get_deposit_handler() -> ConversationHandler:
    return ConversationHandler(
        entry_points=[
            CommandHandler("deposit", deposit_start),
            CallbackQueryHandler(deposit_start, pattern="^btn_deposit$"),
        ],
        states={
            AWAIT_AMOUNT: [
                CallbackQueryHandler(deposit_amount_callback, pattern="^dep_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, deposit_amount_text),
            ],
            AWAIT_SMS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, deposit_sms),
            ],
        },
        fallbacks=[CommandHandler("cancel", lambda u, c: ConversationHandler.END)],
        per_message=False,
    )
