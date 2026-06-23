import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.environ["BOT_TOKEN"].strip()
ADMIN_IDS: list[int] = [int(x.strip()) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
TELEBIRR_NUMBER: str = os.getenv("TELEBIRR_NUMBER", "0928641996")
TELEBIRR_NAME: str = os.getenv("TELEBIRR_NAME", "YES BINGO")
MIN_DEPOSIT: float = float(os.getenv("MIN_DEPOSIT", "30"))
MIN_WITHDRAW: float = float(os.getenv("MIN_WITHDRAW", "100"))
MAX_PLAYERS: int = int(os.getenv("MAX_PLAYERS", "100"))
COUNTDOWN_SECONDS: int = int(os.getenv("COUNTDOWN_SECONDS", "30"))
CALL_INTERVAL: int = int(os.getenv("CALL_INTERVAL", "4"))
WINNER_PERCENTAGE: float = float(os.getenv("WINNER_PERCENTAGE", "80")) / 100
HOUSE_PERCENTAGE: float = float(os.getenv("HOUSE_PERCENTAGE", "20")) / 100
FIRST_WINNER_SHARE: float = float(os.getenv("FIRST_WINNER_SHARE", "0.667"))
DATABASE_FILE: str = os.getenv("DATABASE_FILE", "bingo.db")
WEB_APP_URL: str = os.getenv("WEB_APP_URL", "")

COINS_PER_REFERRAL: int = 1
COINS_TO_ETB_RATE: int = 10
DEMO_COST_COINS: int = 10
MAX_EXTRA_BOARDS: int = 5
EXTRA_BOARD_COST: float = 10.0
EXTRA_BOARD_COIN_REWARD: int = 1
EXTRA_BOARD_ALL_COIN_REWARD: int = 2

DAILY_BONUS_RANKS: dict[int, float] = {
    1: 10.0,
    2: 7.0,
    3: 5.0,
    4: 4.0,
    5: 3.0,
    6: 1.0,
    7: 1.0,
    8: 1.0,
    9: 1.0,
    10: 1.0,
}
