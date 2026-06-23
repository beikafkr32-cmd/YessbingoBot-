import { createServer } from "http";
import app from "./app";
import { logger } from "./lib/logger";
import { wsManager } from "./lib/wsManager";
import type { Duplex } from "stream";

const rawPort = process.env["PORT"];

if (!rawPort) {
  throw new Error(
    "PORT environment variable is required but was not provided.",
  );
}

const port = Number(rawPort);

if (Number.isNaN(port) || port <= 0) {
  throw new Error(`Invalid PORT value: "${rawPort}"`);
}

const server = createServer(app);

// Hand off WebSocket upgrades to the WsManager — only /ws is accepted
server.on("upgrade", (req, socket, head) => {
  wsManager.handleUpgrade(req, socket as Duplex, head as Buffer);
});

server.listen(port, (err?: Error) => {
  if (err) {
    logger.error({ err }, "Error listening on port");
    process.exit(1);
  }
  logger.info({ port }, "Server listening");
});
