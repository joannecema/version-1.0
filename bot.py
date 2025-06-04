import os
import json
import asyncio
import logging
import signal
import pandas as pd
from aiohttp import web

from src.api_handler import ApiHandler
from src.position_tracker import PositionTracker
from src.trade_executor import TradeExecutor
from src.strategy_manager import StrategyManager
from src.volatility_regime_filter import VolatilityRegimeFilter


def validate_config(cfg):
    required = {
        "symbols_count": int,
        "timeframe": str,
        "lookback": int,
        "risk_pct": float,
        "atr_period": int,
        "tp_atr_mult": float,
        "sl_atr_mult": float,
        "volume_multiplier": float,
        "imbalance_levels": int,
        "imbalance_threshold": float,
        "idle_exit_pct": float,
        "execution_interval_sec": int,
        "limit_offset_pct": float,
        "ioc_timeout_ms": int,
        "health_port": int,
        "dynamic_universe": bool,
        "max_consecutive_losses": int,
        "pause_seconds_on_break": int,
        "report_interval_cycles": int,
        "log_file": str,
        "trade_history_file": str,
        "volatility_threshold_atr": float,
    }
    for k, t in required.items():
        if k not in cfg or not isinstance(cfg[k], t):
            raise RuntimeError(f"Config error: {k} missing or not {t.__name__}")


def setup_logging(log_file):
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    ch = logging.StreamHandler(); ch.setFormatter(fmt)
    fh = logging.FileHandler(log_file); fh.setFormatter(fmt)
    logger.addHandler(ch); logger.addHandler(fh)


async def health(request):
    return web.Response(text="OK")


async def shutdown(api):
    logging.info("Shutdown signal received — cleaning up")
    await api.close()
    asyncio.get_event_loop().stop()


async def start_http(app, cfg):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', cfg["health_port"])
    await site.start()


async def main():
    repo_root = os.path.dirname(os.path.abspath(__file__))
    try:
        with open(os.path.join(repo_root, "config.json"), "r") as f:
            cfg = json.load(f)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"Could not parse config.json: {e}")

    validate_config(cfg)
    cfg["threshold"] = cfg["volatility_threshold_atr"]
    setup_logging(cfg["log_file"])
    logging.info("🚀 HFT bot starting…")

    api_key = os.getenv("API_KEY")
    api_secret = os.getenv("API_SECRET")
    api = ApiHandler(api_key, api_secret, cfg)
    await api.exchange.load_markets()

    try:
        symbols = cfg.get("symbols", [])
        if not symbols:
            raise ValueError("No symbols configured")
        cfg["symbols"] = symbols
    except Exception as e:
        logging.error(f"[API] ❌ Failed to fetch top symbols: {e}")
        return

    tracker = PositionTracker(cfg, api)
    executor = TradeExecutor(api, tracker, cfg)
    manager = StrategyManager(cfg, api, tracker, executor)
    vrf = VolatilityRegimeFilter(api, cfg)

    app = web.Application()
    app["tracker"] = tracker
    app.router.add_get('/health', health)
    asyncio.create_task(start_http(app, cfg))

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown(api)))

    consecutive_losses = 0
    cycle = 0

    while True:
        if consecutive_losses >= cfg["max_consecutive_losses"]:
            logging.warning("⚠️ Circuit breaker – pausing")
            await asyncio.sleep(cfg["pause_seconds_on_break"])
            consecutive_losses = 0

        if not await vrf.allow_trading("BTC/USDT"):
            logging.info("🚫 High volatility – skipping cycle")
            await asyncio.sleep(cfg["execution_interval_sec"])
            continue

        await manager.execute()  # ✅ FIXED: Correct call without extra args
        await tracker.evaluate_open_positions()
        cycle += 1

        if cycle % cfg["report_interval_cycles"] == 0:
            df = pd.DataFrame(tracker.trade_history)
            wins = len(df[df["pnl"] > 0])
            tot = len(df)
            roi = (tracker.equity / tracker.config.get("initial_equity", 900) - 1) * 100
            logging.info(
                f"📊 Equity: ${tracker.equity:.2f} | ROI: {roi:.1f}% | Trades: {tot} | Wins: {wins}"
            )

        if tracker.trade_history and tracker.trade_history[-1]["pnl"] < 0:
            consecutive_losses += 1
        else:
            consecutive_losses = 0

        await asyncio.sleep(cfg["execution_interval_sec"])

    await api.close()  # ✅ FIXED: Clean shutdown

if __name__ == "__main__":
    asyncio.run(main())
