import sqlite3
import json
import logging
import asyncio
from typing import Optional, Any, List, Dict
from datetime import datetime
from config import DATABASE_FILE

logger = logging.getLogger(__name__)

# NOTE:
# This module keeps synchronous implementations (prefixed with _sync_) for clarity,
# and exposes async wrappers that run the sync work in a thread via asyncio.to_thread.
# This lets existing synchronous SQL code be reused while providing an async API
# that won't block the event loop.


def _get_connection_sync() -> sqlite3.Connection:
    conn = sqlite3.connect(DATABASE_FILE, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _init_db_sync() -> None:
    conn = _get_connection_sync()
    try:
        with conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    telegram_id INTEGER PRIMARY KEY,
                    username TEXT,
                    first_name TEXT NOT NULL,
                    wallet_balance REAL NOT NULL DEFAULT 0.0,
                    coin_balance INTEGER NOT NULL DEFAULT 0,
                    total_deposits REAL NOT NULL DEFAULT 0.0,
                    total_withdrawals REAL NOT NULL DEFAULT 0.0,
                    total_wins INTEGER NOT NULL DEFAULT 0,
                    total_games INTEGER NOT NULL DEFAULT 0,
                    is_registered INTEGER NOT NULL DEFAULT 1,
                    win_streak INTEGER NOT NULL DEFAULT 0,
                    last_login TEXT,
                    referral_code TEXT UNIQUE,
                    referred_by INTEGER,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    amount REAL NOT NULL,
                    type TEXT NOT NULL,
                    transaction_id TEXT,
                    telebirr_number TEXT,
                    sms_text TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    approved_by INTEGER,
                    description TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (user_id) REFERENCES users(telegram_id)
                );

                CREATE TABLE IF NOT EXISTS used_transaction_ids (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    transaction_id TEXT NOT NULL UNIQUE,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS games (
                    game_id TEXT PRIMARY KEY,
                    board_number INTEGER NOT NULL DEFAULT 0,
                    stake REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'waiting',
                    winner_id INTEGER,
                    called_numbers TEXT NOT NULL DEFAULT '[]',
                    total_pot REAL NOT NULL DEFAULT 0.0,
                    player_count INTEGER NOT NULL DEFAULT 0,
                    started_at TEXT,
                    ended_at TEXT,
                    created_at TEXT NOT NULL DEFAULT (datetime('now'))
                );

                CREATE TABLE IF NOT EXISTS game_players (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    game_id TEXT NOT NULL,
                    user_id INTEGER NOT NULL,
                    main_board TEXT NOT NULL DEFAULT '[]',
                    extra_boards TEXT NOT NULL DEFAULT '[]',
                    board_numbers TEXT NOT NULL DEFAULT '[]',
                    is_winner INTEGER NOT NULL DEFAULT 0,
                    is_eliminated INTEGER NOT NULL DEFAULT 0,
                    joined_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (game_id) REFERENCES games(game_id),
                    FOREIGN KEY (user_id) REFERENCES users(telegram_id),
                    UNIQUE (game_id, user_id)
                );

                CREATE TABLE IF NOT EXISTS referrals (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    referrer_id INTEGER NOT NULL,
                    referred_id INTEGER NOT NULL UNIQUE,
                    coins_earned INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (referrer_id) REFERENCES users(telegram_id),
                    FOREIGN KEY (referred_id) REFERENCES users(telegram_id)
                );

                CREATE TABLE IF NOT EXISTS achievements (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    achievement_type TEXT NOT NULL,
                    earned_at TEXT NOT NULL DEFAULT (datetime('now')),
                    FOREIGN KEY (user_id) REFERENCES users(telegram_id)
                );

                CREATE TABLE IF NOT EXISTS daily_bonus_history (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    rank INTEGER NOT NULL,
                    coins_bonus REAL NOT NULL,
                    bonus_date TEXT NOT NULL DEFAULT (date('now')),
                    FOREIGN KEY (user_id) REFERENCES users(telegram_id)
                );
            """)
        logger.info("Database initialized successfully")
    finally:
        conn.close()


# ---------- Sync implementations above, async wrappers below ----------

async def init_db() -> None:
    await asyncio.to_thread(_init_db_sync)


async def register_user(telegram_id: int, username: Optional[str], first_name: str, referral_code: Optional[str] = None) -> bool:
    def _work():
        conn = _get_connection_sync()
        try:
            import secrets as sec
            code = sec.token_hex(4).upper()
            with conn:
                existing = conn.execute("SELECT telegram_id FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE users SET username = ?, first_name = ?, last_login = datetime('now'), updated_at = datetime('now') WHERE telegram_id = ?",
                        (username, first_name, telegram_id)
                    )
                    return False
                conn.execute(
                    """INSERT INTO users (telegram_id, username, first_name, referral_code, last_login)
                       VALUES (?, ?, ?, ?, datetime('now'))""",
                    (telegram_id, username, first_name, code)
                )
                if referral_code:
                    referrer = conn.execute("SELECT telegram_id FROM users WHERE referral_code = ?", (referral_code,)).fetchone()
                    if referrer and referrer["telegram_id"] != telegram_id:
                        conn.execute(
                            "INSERT OR IGNORE INTO referrals (referrer_id, referred_id) VALUES (?, ?)",
                            (referrer["telegram_id"], telegram_id)
                        )
                        conn.execute(
                            "UPDATE users SET coin_balance = coin_balance + 1, updated_at = datetime('now') WHERE telegram_id = ?",
                            (referrer["telegram_id"],)
                        )
                        conn.execute(
                            "UPDATE users SET referred_by = ?, updated_at = datetime('now') WHERE telegram_id = ?",
                            (referrer["telegram_id"], telegram_id)
                        )
            return True
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def get_user(telegram_id: int) -> Optional[dict]:
    def _work():
        conn = _get_connection_sync()
        try:
            row = conn.execute("SELECT * FROM users WHERE telegram_id = ?", (telegram_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def update_balance(telegram_id: int, amount: float) -> bool:
    def _work():
        conn = _get_connection_sync()
        try:
            with conn:
                conn.execute(
                    "UPDATE users SET wallet_balance = wallet_balance + ?, updated_at = datetime('now') WHERE telegram_id = ?",
                    (amount, telegram_id)
                )
            return True
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def update_coins(telegram_id: int, amount: int) -> bool:
    def _work():
        conn = _get_connection_sync()
        try:
            with conn:
                conn.execute(
                    "UPDATE users SET coin_balance = coin_balance + ?, updated_at = datetime('now') WHERE telegram_id = ?",
                    (amount, telegram_id)
                )
            return True
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def create_transaction(user_id: int, amount: float, tx_type: str, transaction_id: Optional[str] = None,
                             telebirr_number: Optional[str] = None, sms_text: Optional[str] = None,
                             description: Optional[str] = None, status: str = "pending") -> int:
    def _work():
        conn = _get_connection_sync()
        try:
            with conn:
                cursor = conn.execute(
                    """INSERT INTO transactions (user_id, amount, type, transaction_id, telebirr_number, sms_text, description, status)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                    (user_id, amount, tx_type, transaction_id, telebirr_number, sms_text, description, status)
                )
            return cursor.lastrowid
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def get_transaction(tx_id: int) -> Optional[dict]:
    def _work():
        conn = _get_connection_sync()
        try:
            row = conn.execute("SELECT * FROM transactions WHERE id = ?", (tx_id,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def approve_transaction(tx_id: int, approved_by: int) -> bool:
    def _work():
        conn = _get_connection_sync()
        try:
            with conn:
                conn.execute(
                    "UPDATE transactions SET status = 'approved', approved_by = ? WHERE id = ?",
                    (approved_by, tx_id)
                )
            return True
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def reject_transaction(tx_id: int, approved_by: int) -> bool:
    def _work():
        conn = _get_connection_sync()
        try:
            with conn:
                conn.execute(
                    "UPDATE transactions SET status = 'rejected', approved_by = ? WHERE id = ?",
                    (approved_by, tx_id)
                )
            return True
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def is_transaction_id_used(transaction_id: str) -> bool:
    def _work():
        conn = _get_connection_sync()
        try:
            row = conn.execute("SELECT id FROM used_transaction_ids WHERE transaction_id = ?", (transaction_id,)).fetchone()
            return row is not None
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def mark_transaction_id_used(transaction_id: str) -> None:
    def _work():
        conn = _get_connection_sync()
        try:
            with conn:
                conn.execute("INSERT OR IGNORE INTO used_transaction_ids (transaction_id) VALUES (?)", (transaction_id,))
        finally:
            conn.close()
    await asyncio.to_thread(_work)


async def get_user_transactions(user_id: int, limit: int = 10) -> List[dict]:
    def _work():
        conn = _get_connection_sync()
        try:
            rows = conn.execute(
                "SELECT * FROM transactions WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
                (user_id, limit)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def create_game(game_id: str, stake: float) -> bool:
    def _work():
        conn = _get_connection_sync()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO games (game_id, stake, status) VALUES (?, ?, 'waiting')",
                    (game_id, stake)
                )
            return True
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def get_game(game_id: str) -> Optional[dict]:
    def _work():
        conn = _get_connection_sync()
        try:
            row = conn.execute("SELECT * FROM games WHERE game_id = ?", (game_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            d["called_numbers"] = json.loads(d["called_numbers"])
            return d
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def get_waiting_game(stake: float) -> Optional[dict]:
    def _work():
        conn = _get_connection_sync()
        try:
            row = conn.execute(
                "SELECT * FROM games WHERE stake = ? AND status = 'waiting' ORDER BY created_at ASC LIMIT 1",
                (stake,)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["called_numbers"] = json.loads(d["called_numbers"])
            return d
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def update_game(game_id: str, **kwargs: Any) -> bool:
    def _work():
        conn = _get_connection_sync()
        try:
            if "called_numbers" in kwargs:
                kwargs["called_numbers"] = json.dumps(kwargs["called_numbers"])
            sets = ", ".join(f"{k} = ?" for k in kwargs)
            vals = list(kwargs.values()) + [game_id]
            with conn:
                conn.execute(f"UPDATE games SET {sets} WHERE game_id = ?", vals)
            return True
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def add_player_to_game(game_id: str, user_id: int, main_board: List[Optional[int]], board_number: int) -> bool:
    def _work():
        conn = _get_connection_sync()
        try:
            with conn:
                conn.execute(
                    """INSERT OR IGNORE INTO game_players (game_id, user_id, main_board, board_numbers)
                       VALUES (?, ?, ?, ?)""",
                    (game_id, user_id, json.dumps(main_board), json.dumps([board_number]))
                )
                conn.execute(
                    "UPDATE games SET player_count = player_count + 1, total_pot = total_pot + stake WHERE game_id = ?",
                    (game_id,)
                )
            return True
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def get_game_players(game_id: str) -> List[dict]:
    def _work():
        conn = _get_connection_sync()
        try:
            rows = conn.execute(
                "SELECT gp.*, u.username, u.first_name FROM game_players gp JOIN users u ON gp.user_id = u.telegram_id WHERE gp.game_id = ?",
                (game_id,)
            ).fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d["main_board"] = json.loads(d["main_board"])
                d["extra_boards"] = json.loads(d["extra_boards"])
                d["board_numbers"] = json.loads(d["board_numbers"])
                result.append(d)
            return result
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def get_player_in_game(game_id: str, user_id: int) -> Optional[dict]:
    def _work():
        conn = _get_connection_sync()
        try:
            row = conn.execute(
                "SELECT * FROM game_players WHERE game_id = ? AND user_id = ?",
                (game_id, user_id)
            ).fetchone()
            if not row:
                return None
            d = dict(row)
            d["main_board"] = json.loads(d["main_board"])
            d["extra_boards"] = json.loads(d["extra_boards"])
            d["board_numbers"] = json.loads(d["board_numbers"])
            return d
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def update_player(game_id: str, user_id: int, **kwargs: Any) -> bool:
    def _work():
        conn = _get_connection_sync()
        try:
            if "main_board" in kwargs:
                kwargs["main_board"] = json.dumps(kwargs["main_board"])
            if "extra_boards" in kwargs:
                kwargs["extra_boards"] = json.dumps(kwargs["extra_boards"])
            if "board_numbers" in kwargs:
                kwargs["board_numbers"] = json.dumps(kwargs["board_numbers"])
            sets = ", ".join(f"{k} = ?" for k in kwargs)
            vals = list(kwargs.values()) + [game_id, user_id]
            with conn:
                conn.execute(f"UPDATE game_players SET {sets} WHERE game_id = ? AND user_id = ?", vals)
            return True
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def remove_player_from_game(game_id: str, user_id: int) -> bool:
    def _work():
        conn = _get_connection_sync()
        try:
            with conn:
                conn.execute("DELETE FROM game_players WHERE game_id = ? AND user_id = ?", (game_id, user_id))
                # Prevent negative values
                conn.execute(
                    "UPDATE games SET player_count = CASE WHEN player_count>0 THEN player_count - 1 ELSE 0 END, total_pot = CASE WHEN total_pot>stake THEN total_pot - stake ELSE 0 END WHERE game_id = ?",
                    (game_id,)
                )
            return True
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def get_leaderboard(limit: int = 10) -> List[dict]:
    def _work():
        conn = _get_connection_sync()
        try:
            rows = conn.execute(
                "SELECT telegram_id, first_name, username, coin_balance, total_wins FROM users ORDER BY coin_balance DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def get_top_users_for_bonus(limit: int = 10) -> List[dict]:
    def _work():
        conn = _get_connection_sync()
        try:
            rows = conn.execute(
                "SELECT telegram_id, first_name, coin_balance FROM users ORDER BY coin_balance DESC LIMIT ?",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


async def record_daily_bonus(user_id: int, rank: int, coins_bonus: float) -> None:
    def _work():
        conn = _get_connection_sync()
        try:
            with conn:
                conn.execute(
                    "INSERT INTO daily_bonus_history (user_id, rank, coins_bonus) VALUES (?, ?, ?)",
                    (user_id, rank, coins_bonus)
                )
        finally:
            conn.close()
    await asyncio.to_thread(_work)


async def get_admin_stats() -> dict:
    def _work():
        conn = _get_connection_sync()
        try:
            users_count = conn.execute("SELECT COUNT(*) as c FROM users").fetchone()["c"]
            total_deposits = conn.execute("SELECT COALESCE(SUM(amount), 0) as s FROM transactions WHERE type = 'deposit' AND status = 'approved'").fetchone()["s"]
            total_withdrawals = conn.execute("SELECT COALESCE(SUM(amount), 0) as s FROM transactions WHERE type = 'withdraw' AND status = 'approved'").fetchone()["s"]
            active_games = conn.execute("SELECT COUNT(*) as c FROM games WHERE status IN ('waiting', 'active')").fetchone()["c"]
            pending_deposits = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE type = 'deposit' AND status = 'pending'").fetchone()["c"]
            pending_withdrawals = conn.execute("SELECT COUNT(*) as c FROM transactions WHERE type = 'withdraw' AND status = 'pending'").fetchone()["c"]
            return {
                "users_count": users_count,
                "total_deposits": total_deposits,
                "total_withdrawals": total_withdrawals,
                "active_games": active_games,
                "pending_deposits": pending_deposits,
                "pending_withdrawals": pending_withdrawals,
            }
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


# Convenience helper used by api_server.api_lobby
async def get_lobby_snapshot() -> Dict[str, Any]:
    def _work():
        conn = _get_connection_sync()
        try:
            stake_levels = [10, 20, 50, 100]
            stakes_out = []
            for stake in stake_levels:
                best = conn.execute(
                    "SELECT * FROM games WHERE stake=? AND status='waiting' ORDER BY player_count DESC LIMIT 1",
                    (stake,)
                ).fetchone()
                active_rows = conn.execute(
                    "SELECT COUNT(*) as c FROM games WHERE stake=? AND status='active'",
                    (stake,)
                ).fetchone()
                active_count = active_rows["c"] if active_rows else 0
                pcount = conn.execute(
                    "SELECT COALESCE(SUM(player_count),0) as t FROM games WHERE stake=? AND status IN ('waiting','active')",
                    (stake,)
                ).fetchone()
                total_players = int(pcount["t"]) if pcount else 0
                potrow = conn.execute(
                    "SELECT COALESCE(SUM(total_pot),0) as t FROM games WHERE stake=? AND status IN ('waiting','active')",
                    (stake,)
                ).fetchone()
                total_pot = float(potrow["t"]) if potrow else 0.0
                prize = round(total_pot * 0.8, 2)
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
            demo_waiting = conn.execute("SELECT COUNT(*) as c FROM games WHERE stake=0 AND status='waiting'").fetchone()
            demo_active = conn.execute("SELECT COUNT(*) as c FROM games WHERE stake=0 AND status='active'").fetchone()
            return {
                "stakes": stakes_out,
                "demo": {
                    "active_count": (demo_active["c"] if demo_active else 0),
                    "waiting_count": (demo_waiting["c"] if demo_waiting else 0),
                }
            }
        finally:
            conn.close()
    return await asyncio.to_thread(_work)


# Helper: find an active/waiting game that the user is part of
async def get_active_game_for_user(user_id: int) -> Optional[str]:
    def _work():
        conn = _get_connection_sync()
        try:
            row = conn.execute(
                "SELECT gp.game_id FROM game_players gp JOIN games g ON gp.game_id=g.game_id WHERE gp.user_id=? AND g.status IN ('waiting','active') LIMIT 1",
                (user_id,)
            ).fetchone()
            return row["game_id"] if row else None
        finally:
            conn.close()
    return await asyncio.to_thread(_work)
