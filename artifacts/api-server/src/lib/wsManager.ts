import { WebSocketServer, WebSocket } from "ws";
import type { IncomingMessage } from "http";
import type { Duplex } from "stream";
import { logger } from "./logger";

interface WsClient {
  ws: WebSocket;
  userId: string;
}

class WsManager {
  public readonly wss: WebSocketServer;
  private rooms = new Map<string, Set<WsClient>>();

  constructor() {
    this.wss = new WebSocketServer({ noServer: true });
  }

  handleUpgrade(req: IncomingMessage, socket: Duplex, head: Buffer): void {
    const url = new URL(req.url ?? "/", `http://${req.headers.host}`);
    if (url.pathname !== "/ws") {
      socket.destroy();
      return;
    }
    this.wss.handleUpgrade(req, socket, head, (ws) => {
      const gameId = url.searchParams.get("game_id") ?? "";
      const userId = url.searchParams.get("user_id") ?? "";
      this.onConnect(ws, gameId, userId);
    });
  }

  private onConnect(ws: WebSocket, gameId: string, userId: string): void {
    if (!this.rooms.has(gameId)) this.rooms.set(gameId, new Set());
    const client: WsClient = { ws, userId };
    this.rooms.get(gameId)!.add(client);
    logger.info({ gameId, userId }, "WS connected");

    ws.on("message", (raw) => {
      try {
        const msg = JSON.parse(raw.toString()) as { type?: string };
        if (msg.type === "ping") ws.send(JSON.stringify({ type: "pong" }));
      } catch {
        // ignore malformed
      }
    });

    const cleanup = (): void => {
      this.rooms.get(gameId)?.delete(client);
      logger.info({ gameId, userId }, "WS disconnected");
    };
    ws.on("close", cleanup);
    ws.on("error", cleanup);
  }

  broadcast(gameId: string, data: unknown): void {
    const room = this.rooms.get(gameId);
    if (!room) return;
    const msg = JSON.stringify(data);
    for (const client of room) {
      if (client.ws.readyState === WebSocket.OPEN) {
        client.ws.send(msg);
      }
    }
  }

  roomSize(gameId: string): number {
    return this.rooms.get(gameId)?.size ?? 0;
  }
}

export const wsManager = new WsManager();
