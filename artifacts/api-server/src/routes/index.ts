import { Router, type IRouter } from "express";
import healthRouter from "./health";
import gameRouter from "./game";
import internalRouter from "./internal";

const router: IRouter = Router();

router.use(healthRouter);
router.use(gameRouter);           // handles /lobby, /game/*, /leaderboard, /history, /profile, /admin/*
router.use("/internal", internalRouter);

export default router;
