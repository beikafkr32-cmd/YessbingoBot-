import express, { type Express } from "express";
import cors from "cors";
import pinoHttp from "pino-http";
import { join, dirname } from "path";
import { fileURLToPath } from "url";
import router from "./routes";
import { logger } from "./lib/logger";

const __dirname = dirname(fileURLToPath(import.meta.url));
// yes-bingo/web_app is at ../../yes-bingo/web_app relative to dist/
// In dev (src/) it's also ../../yes-bingo/web_app
const WEB_APP_DIR = join(__dirname, "..", "..", "..", "yes-bingo", "web_app");

const app: Express = express();

app.use(
  pinoHttp({
    logger,
    serializers: {
      req(req) {
        return {
          id: req.id,
          method: req.method,
          url: req.url?.split("?")[0],
        };
      },
      res(res) {
        return {
          statusCode: res.statusCode,
        };
      },
    },
  }),
);
app.use(cors());
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// Serve Mini App static files at /web_app/
app.use("/web_app", express.static(WEB_APP_DIR, { index: "index.html" }));
app.get("/web_app", (_req, res) => res.sendFile(join(WEB_APP_DIR, "index.html")));

app.use("/api", router);

export default app;
