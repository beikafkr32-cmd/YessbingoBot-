import logging
import asyncio
import os
from datetime import time as dt_time
from telegram.ext import Application, ApplicationBuilder
import database as db
import config
from handlers.navigation import get_navigation_handlers
from handlers.deposit import get_deposit_handler, deposit_confirm_callback
from handlers.withdraw import get_withdraw_handler
from handlers.admin import get_admin_handlers, run_daily_bonus
from handlers.game import get_game_handlers
from api_server import start_api_server
from telegram.ext import CallbackQueryHandler

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


async def post_init(application: Application) -> None:
    db.init_db()
    logger.info("Database initialized")
    bot = application.bot
    await bot.set_my_commands([
        ("start", "Start the bot"),
        ("playbingo", "Start playing Bingo"),
        ("playspin", "Start playing Spin (Coming Soon)"),
        ("balance", "Check account balance"),
        ("deposit", "Deposit funds"),
        ("withdraw", "Withdraw funds"),
        ("history", "View transaction history"),
        ("invite", "Get referral link"),
        ("convert", "Convert coins to ETB"),
        ("help", "Show help menu"),
        ("transfer", "Transfer (Disabled)"),
    ])
    logger.info("Bot commands set")


def main() -> None:
    api_port = int(os.environ.get("API_PORT", "8082"))
    start_api_server(api_port)

    app = (
        ApplicationBuilder()
        .token(config.BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(get_deposit_handler())
    app.add_handler(get_withdraw_handler())

    for handler in get_navigation_handlers():
        app.add_handler(handler)

    for handler in get_game_handlers():
        app.add_handler(handler)

    for handler in get_admin_handlers():
        app.add_handler(handler)

    app.add_handler(CallbackQueryHandler(deposit_confirm_callback, pattern=r"^dep_confirm_|^dep_cancel_final$"))

    job_queue = app.job_queue
    if job_queue:
        job_queue.run_daily(
            lambda ctx: asyncio.ensure_future(run_daily_bonus(ctx)),
            time=dt_time(hour=0, minute=0, second=0),
            name="daily_bonus"
        )
        logger.info("Daily bonus job scheduled at midnight")

    logger.info("YES BINGO bot starting...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
