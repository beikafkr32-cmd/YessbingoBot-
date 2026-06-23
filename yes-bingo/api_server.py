"""
Lightweight HTTP server for the Mini App web endpoints.
Runs alongside the Telegram bot in the same process.
"""
import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs
import threading
import database as db
import config

logger = logging.getLogger(__name__)


def json_response(handler: BaseHTTPRequestHandler, data: dict, status: int = 200) -> None:
    body = json.dumps(data).encode()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Access-Control-Allow-Origin", "*")
    handler.end_headers()
    handler.wfile.write(body)


class MiniAppHandler(BaseHTTPRequestHandler):

    def log_message(self, format: str, *args) -> None:
        logger.debug(f"HTTP {format % args}")

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, X-User-Id")
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        def qp(key: str) -> str | None:
            vals = qs.get(key)
            return vals[0] if vals else None

        path = parsed.path

        if path == "/api/game/state":
            game_id = qp("game_id")
            user_id = qp("user_id")
            if not game_id:
                json_response(self, {"error": "Missing game_id"}, 400)
                return
            game = db.get_game(game_id)
            if not game:
                json_response(self, {"error": "Game not found"}, 404)
                return
            player = db.get_player_in_game(game_id, int(user_id)) if user_id else None
            extra: dict = {}
            if game.get("winner_id"):
                w = db.get_user(game["winner_id"])
                if w:
                    extra["winner_name"] = w["first_name"]
                if player and player.get("is_winner"):
                    # compute prize
                    prize = game["total_pot"] * config.WINNER_PERCENTAGE * config.FIRST_WINNER_SHARE
                    extra["winner_amount"] = round(prize, 2)
                    extra["winner_board"] = player["board_numbers"][0] if player["board_numbers"] else "-"
            json_response(self, {"game": game, "player": player, **extra})

        elif path == "/api/leaderboard":
            players = db.get_leaderboard(10)
            json_response(self, {"players": players})

        elif path == "/api/history":
            user_id = qp("user_id")
            if not user_id:
                json_response(self, {"transactions": []})
                return
            txs = db.get_user_transactions(int(user_id), 20)
            json_response(self, {"transactions": txs})

        elif path == "/api/profile":
            user_id = qp("user_id")
            if not user_id:
                json_response(self, {"error": "Missing user_id"}, 400)
                return
            user = db.get_user(int(user_id))
            if not user:
                json_response(self, {"error": "User not found"}, 404)
                return
            json_response(self, {"user": user})

        elif path == "/api/health":
            json_response(self, {"status": "ok"})

        else:
            json_response(self, {"error": "Not found"}, 404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path

        length = int(self.headers.get("Content-Length", 0))
        body: dict = {}
        if length:
            try:
                body = json.loads(self.rfile.read(length))
            except Exception:
                json_response(self, {"error": "Invalid JSON"}, 400)
                return

        if path == "/api/game/claim-bingo":
            game_id = body.get("game_id")
            user_id = body.get("user_id")
            if not game_id or not user_id:
                json_response(self, {"error": "Missing fields"}, 400)
                return

            game = db.get_game(game_id)
            if not game or game["status"] != "active":
                json_response(self, {"success": False, "message": "Game not active"})
                return

            player = db.get_player_in_game(game_id, int(user_id))
            if not player:
                json_response(self, {"success": False, "message": "Not in game"})
                return
            if player["is_eliminated"]:
                json_response(self, {"success": False, "eliminated": True})
                return

            from utils import check_bingo
            called = game["called_numbers"]
            boards = [player["main_board"]] + player["extra_boards"]
            has_bingo = False
            for flat in boards:
                board_2d = [[flat[c * 5 + r] for r in range(5)] for c in range(5)]
                if check_bingo(board_2d, called):
                    has_bingo = True
                    break

            if not has_bingo:
                db.update_player(game_id, int(user_id), is_eliminated=1)
                json_response(self, {"success": False, "eliminated": True})
                return

            prize = game["total_pot"] * config.WINNER_PERCENTAGE * config.FIRST_WINNER_SHARE
            db.update_game(game_id, status="finished", winner_id=int(user_id))
            db.update_player(game_id, int(user_id), is_winner=1)
            db.update_balance(int(user_id), prize)
            db.create_transaction(int(user_id), prize, "win", description=f"Bingo win in {game_id}")

            conn = db.get_connection()
            try:
                with conn:
                    conn.execute(
                        "UPDATE users SET total_wins = total_wins + 1, win_streak = win_streak + 1 WHERE telegram_id = ?",
                        (int(user_id),)
                    )
            finally:
                conn.close()

            user = db.get_user(int(user_id))
            board_number = player["board_numbers"][0] if player["board_numbers"] else "-"
            json_response(self, {
                "success": True,
                "winner_name": user["first_name"] if user else "Player",
                "board_number": board_number,
                "amount": round(prize, 2),
            })

        else:
            json_response(self, {"error": "Not found"}, 404)


def start_api_server(port: int = 8082) -> threading.Thread:
    server = HTTPServer(("0.0.0.0", port), MiniAppHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    logger.info(f"Mini App API server running on port {port}")
    return thread
