import { Router } from "express";
import { wsManager } from "../lib/wsManager";

const router = Router();

/**
 * POST /api/internal/broadcast
 * Called by the Python bot whenever a game event occurs.
 * Body: { game_id: string, event: object }
 */
router.post("/broadcast", (req, res) => {
  const { game_id, event } = req.body as { game_id?: string; event?: unknown };
  if (!game_id || !event) {
    res.status(400).json({ error: "missing game_id or event" });
    return;
  }
  wsManager.broadcast(game_id, event);
  res.json({ ok: true, room_size: wsManager.roomSize(game_id) });
});

export default router;
