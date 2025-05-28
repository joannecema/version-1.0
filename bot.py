import os
import json
import asyncio
import logging
import signal
import queue

import pandas as pd
from aiohttp import web

from src.api_handler import ApiHandler
from src.market_data_fix import start_fix_md_session
from src.position_tracker import PositionTracker
from src.trade_executor import TradeExecutor
from src.strategy_manager import StrategyManager
from src.strategies.volatility_regime_filter import VolatilityRegimeFilter

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
        # remove metrics_port here
    }
    for k, t in required.items():
        if k not in cfg or not isinstance(cfg[k], t):
            raise RuntimeError(f"Config error: {k} missing or not {t.__name__}")

def setup_logging(log_file):
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    ch = logging.StreamHandler(); ch.setFormatter(fmt)
    fh = logging.FileHandler(log_file); fh.setFormatter(fmt)
    logger.addHandler(ch); logger.addHandler(fh)

async def health(request):
    return web.Response(text="OK")

async def shutdown():
    logging.info("Shutdown signal received—cleaning up")
    asyncio.get_event_loop().stop()

async def start_http(app, cfg):
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', cfg["health_port"])
    await site.start()

async def main():
    # Load and validate config
    repo_root = os.path.dirname(os.path.abspath(__file__))
    cfg_path = os.path.join(repo_root, "config.json")
    cfg = json.load(open(cfg_path))
    validate_config(cfg)

    # Logging
    setup_logging(cfg["log_file"])
    logging.info("🚀 HFT bot starting…")

    # FIX Market Data
    md_queue = queue.Queue() if cfg.get("use_fix_md") else None
    if md_queue:
        fix_cfg_path = os.path.join(repo_root, "fix_md.cfg")
        start_fix_md_session(fix_cfg_path, md_queue)

    # Initialize core components
    api = ApiHandler(os.getenv("API_KEY"), os.getenv("API_SECRET"), cfg)
    tracker = PositionTracker(cfg)
    executor = TradeExecutor(api, tracker, cfg, md_queue)
    manager = StrategyManager(api, tracker, executor, cfg)
    vrf = VolatilityRegimeFilter(api, cfg)

    # HTTP health endpoint
    app = web.Application()
    app["tracker"] = tracker
    app.router.add_get('/health', health)
    asyncio.create_task(start_http(app, cfg))

    # Graceful shutdown handlers
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

    consecutive_losses = 0
    cycle = 0

    # Main loop
    while True:
        # Circuit breaker pause
        if consecutive_losses >= cfg["max_consecutive_losses"]:
            logging.warning("Circuit breaker – pausing")
            await asyncio.sleep(cfg["pause_seconds_on_break"])
            consecutive_losses = 0

        # Volatility regime filter
        if not await vrf.allow_trading("BTC/USDT"):
            logging.info("High volatility – skipping cycle")
            await asyncio.sleep(cfg["execution_interval_sec"])
            continue

        # Run all strategies
        await manager.run_cycle()
        cycle += 1

        # Periodic report
        if cycle % cfg["report_interval_cycles"] == 0:
            df = pd.DataFrame(tracker.trade_history)
            wins = len(df[df["pnl"] > 0])
            total = len(df)
            logging.info(
                f"Equity: ${tracker.equity:.2f} | "
                f"ROI: {(tracker.equity/900-1)*100:.1f}% | "
                f"Trades: {total} | Wins: {wins}"
            )

        # Update consecutive losses counter
        if tracker.trade_history and tracker.trade_history[-1]["pnl"] < 0:
            consecutive_losses += 1
        else:
            consecutive_losses = 0

        await asyncio.sleep(cfg["execution_interval_sec"])

if __name__ == "__main__":
    asyncio.run(main())
