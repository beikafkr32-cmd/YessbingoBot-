import random
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

BINGO_COLS = {
    "B": (1, 15),
    "I": (16, 30),
    "N": (31, 45),
    "G": (46, 60),
    "O": (61, 75),
}


def generate_bingo_board() -> list[list[Optional[int]]]:
    board: list[list[Optional[int]]] = []
    for col, (low, high) in BINGO_COLS.items():
        nums = random.sample(range(low, high + 1), 5)
        board.append(nums)
    board[2][2] = None
    return board


def flatten_board(board: list[list[Optional[int]]]) -> list[Optional[int]]:
    flat = []
    for row in range(5):
        for col in range(5):
            flat.append(board[col][row])
    return flat


def board_from_flat(flat: list[Optional[int]]) -> list[list[Optional[int]]]:
    board = []
    for col in range(5):
        board.append([flat[col * 5 + row] for row in range(5)])
    return board


def check_bingo(board: list[list[Optional[int]]], called: list[int]) -> bool:
    called_set = set(called)

    def marked(val: Optional[int]) -> bool:
        return val is None or val in called_set

    for col in range(5):
        if all(marked(board[col][row]) for row in range(5)):
            return True
    for row in range(5):
        if all(marked(board[col][row]) for col in range(5)):
            return True
    if all(marked(board[i][i]) for i in range(5)):
        return True
    if all(marked(board[4 - i][i]) for i in range(5)):
        return True
    return False


def parse_telebirr_sms(sms: str) -> Optional[dict]:
    patterns = [
        r"(?:sent|transferred|paid)\s+(?:ETB\s*)?(\d+(?:\.\d{1,2})?)\s*(?:ETB)?",
        r"ETB\s*(\d+(?:\.\d{1,2})?)\s*(?:has been|was)?\s*(?:sent|transferred|paid)",
        r"(\d+(?:\.\d{1,2})?)\s*(?:Birr|ETB)",
    ]
    amount: Optional[float] = None
    for pat in patterns:
        m = re.search(pat, sms, re.IGNORECASE)
        if m:
            try:
                amount = float(m.group(1))
                break
            except ValueError:
                continue

    tx_patterns = [
        r"(?:transaction|ref(?:erence)?|receipt|TxID|Ref\.?\s*No\.?)[:\s#]*([A-Za-z0-9]{6,20})",
        r"\b([A-Z]{2,4}\d{6,15})\b",
        r"\b(\d{10,16})\b",
    ]
    tx_id: Optional[str] = None
    for pat in tx_patterns:
        m = re.search(pat, sms, re.IGNORECASE)
        if m:
            tx_id = m.group(1).strip()
            break

    if amount is not None and tx_id is not None:
        return {"amount": amount, "transaction_id": tx_id}
    return None


def format_currency(amount: float) -> str:
    return f"{amount:.2f} ETB"


def get_board_number() -> int:
    return random.randint(1, 99)


def generate_game_id() -> str:
    return f"G{random.randint(1000, 9999)}"


def get_win_message(winner_name: str, board_number: int, amount: float) -> str:
    return (
        f"🎉 *BINGO!* 🎉\n"
        f"👤 Winner: {winner_name}\n"
        f"🎯 Board Number: {board_number}\n"
        f"💰 Amount Won: {format_currency(amount)}\n"
    )


def build_main_menu(balance: float, coins: int, active_games: int) -> str:
    return (
        f"🎯 *Welcome to YES BINGO!* 🇪🇹\n\n"
        f"💰 Balance: {format_currency(balance)}\n"
        f"🪙 Coins: {coins}\n"
        f"👥 Active Games: {active_games}\n\n"
        f"Choose an option below:"
    )


def build_profile_text(user: dict) -> str:
    return (
        f"👤 *Your Profile*\n\n"
        f"🆔 ID: {user['telegram_id']}\n"
        f"👤 Name: {user['first_name']}\n"
        f"📛 Username: @{user.get('username') or 'N/A'}\n"
        f"💰 Balance: {format_currency(user['wallet_balance'])}\n"
        f"🪙 Coins: {user['coin_balance']}\n"
        f"🏆 Total Wins: {user['total_wins']}\n"
        f"🎮 Total Games: {user['total_games']}\n"
        f"🔥 Win Streak: {user['win_streak']}\n"
        f"💵 Total Deposits: {format_currency(user['total_deposits'])}\n"
        f"💸 Total Withdrawals: {format_currency(user['total_withdrawals'])}\n"
        f"📅 Joined: {user['created_at'][:10]}\n"
        f"🔗 Referral Code: `{user.get('referral_code', 'N/A')}`"
    )


def build_history_text(transactions: list[dict]) -> str:
    if not transactions:
        return "📊 *Transaction History*\n\nNo transactions yet."
    lines = ["📊 *Transaction History*\n"]
    icons = {
        "deposit": "💰", "withdraw": "💸", "win": "🏆",
        "bet": "🎲", "refund": "🔄", "bonus": "🎁",
        "credit_conversion": "🪙", "extra_board": "📋", "daily_bonus": "🌟"
    }
    for tx in transactions:
        icon = icons.get(tx["type"], "💳")
        status_icon = {"approved": "✅", "pending": "⏳", "rejected": "❌"}.get(tx["status"], "❓")
        lines.append(
            f"{icon} {tx['type'].upper()} {status_icon}\n"
            f"   Amount: {format_currency(tx['amount'])}\n"
            f"   {tx['created_at'][:16]}\n"
        )
    return "\n".join(lines)
