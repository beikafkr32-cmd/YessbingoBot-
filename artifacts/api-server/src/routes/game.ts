import { Router, type Request, type Response } from "express";

const router = Router();
const PYTHON_BASE = "http://localhost:8082";

async function proxyGet(path: string, query: Record<string, string>): Promise<Response> {
  const url = new URL(PYTHON_BASE + path);
  for (const [k, v] of Object.entries(query)) url.searchParams.set(k, v);
  return fetch(url.toString(), { signal: AbortSignal.timeout(5000) });
}

async function proxyPost(path: string, body: unknown): Promise<Response> {
  return fetch(PYTHON_BASE + path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
    signal: AbortSignal.timeout(5000),
  });
}

router.get("/game/state", async (req: Request, res: Response) => {
  try {
    const r = await proxyGet("/api/game/state", req.query as Record<string, string>);
    res.status(r.status).json(await r.json());
  } catch {
    res.status(503).json({ error: "game server unavailable" });
  }
});

router.post("/game/claim-bingo", async (req: Request, res: Response) => {
  try {
    const r = await proxyPost("/api/game/claim-bingo", req.body as unknown);
    res.status(r.status).json(await r.json());
  } catch {
    res.status(503).json({ error: "game server unavailable" });
  }
});

router.get("/leaderboard", async (_req: Request, res: Response) => {
  try {
    const r = await proxyGet("/api/leaderboard", {});
    res.status(r.status).json(await r.json());
  } catch {
    res.status(503).json({ error: "game server unavailable" });
  }
});

router.get("/history", async (req: Request, res: Response) => {
  try {
    const r = await proxyGet("/api/history", req.query as Record<string, string>);
    res.status(r.status).json(await r.json());
  } catch {
    res.status(503).json({ error: "game server unavailable" });
  }
});

router.get("/profile", async (req: Request, res: Response) => {
  try {
    const r = await proxyGet("/api/profile", req.query as Record<string, string>);
    res.status(r.status).json(await r.json());
  } catch {
    res.status(503).json({ error: "game server unavailable" });
  }
});

export default router;
