import { Router, type IRouter } from "express";
import healthRouter from "./health";
import gameRouter from "./game";
import internalRouter from "./internal";

const router: IRouter = Router();

router.use(healthRouter);
router.use(gameRouter);            // handles /game/*, /leaderboard, /history, /profile
router.use("/internal", internalRouter);

export default router;
