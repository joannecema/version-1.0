import os, json, asyncio, logging, signal, queue
import pandas as pd
from aiohttp import web
from prometheus_client import Counter, Gauge, start_http_server
from src.api_handler import ApiHandler
from src.market_data_fix import start_fix_md_session
from src.position_tracker import PositionTracker
from src.trade_executor import TradeExecutor
from src.strategy_manager import StrategyManager
from src.strategies.volatility_regime_filter import VolatilityRegimeFilter

# Prometheus metrics
TRADE_COUNTER = Counter("total_trades", "Total trades executed")
EQUITY_GAUGE  = Gauge("current_equity", "Live equity")

def validate_config(cfg):
    required = {
        "symbols_count": int, "timeframe": str, "lookback": int,
        "risk_pct": float, "atr_period": int,
        "tp_atr_mult": float, "sl_atr_mult": float,
        # …add all keys with types…
    }
    for k, t in required.items():
        if k not in cfg or not isinstance(cfg[k], t):
            raise RuntimeError(f"Config error: {k} missing or not {t.__name__}")

def setup_logging(log_file):
    logger = logging.getLogger(); logger.setLevel(logging.INFO)
    fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s",
                            "%Y-%m-%d %H:%M:%S")
    ch = logging.StreamHandler(); ch.setFormatter(fmt)
    fh = logging.FileHandler(log_file); fh.setFormatter(fmt)
    logger.addHandler(ch); logger.addHandler(fh)

async def health(request):
    return web.Response(text="OK")

async def metrics(request):
    tracker = request.app["tracker"]
    return web.json_response({
        "equity": tracker.equity,
        "open_positions": len(tracker.open_positions),
        "total_trades": len(tracker.trade_history)
    })

async def shutdown():
    logging.info("Shutdown signal received—cleaning up")
    asyncio.get_event_loop().stop()

async def start_http(app, cfg):
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', cfg["health_port"])
    await site.start()

async def main():
    cfg = json.load(open("config.json"))
    validate_config(cfg)
    setup_logging(cfg["log_file"])
    logging.info("🚀 HFT bot starting…")

    # Prometheus server
    start_http_server(cfg["metrics_port"])

    # FIX MD
    md_queue = queue.Queue() if cfg["use_fix_md"] else None
    if md_queue:
        start_fix_md_session("fix_md.cfg", md_queue)

    api = ApiHandler(os.getenv("API_KEY"), os.getenv("API_SECRET"), cfg)
    tracker = PositionTracker(cfg)
    executor = TradeExecutor(api, tracker, cfg, md_queue)
    manager = StrategyManager(api, tracker, executor, cfg)
    vrf = VolatilityRegimeFilter(api, cfg)

    # Setup HTTP
    app = web.Application()
    app["tracker"] = tracker
    app.router.add_get('/health', health)
    app.router.add_get('/metrics', metrics)
    asyncio.create_task(start_http(app, cfg))

    # graceful shutdown
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, lambda: asyncio.create_task(shutdown()))

    consecutive_losses = 0
    cycle = 0
    while True:
        if consecutive_losses >= cfg["max_consecutive_losses"]:
            logging.warning("Circuit breaker – pausing")
            await asyncio.sleep(cfg["pause_seconds_on_break"])
            consecutive_losses = 0

        if not await vrf.allow_trading("BTC/USDT"):
            logging.info("High volatility – skipping cycle")
            await asyncio.sleep(cfg["execution_interval_sec"])
            continue

        await manager.run_cycle()
        cycle += 1
        if cycle % cfg["report_interval_cycles"] == 0:
            df = pd.DataFrame(tracker.trade_history)
            wins = len(df[df["pnl"]>0]); total = len(df)
            logging.info(f"Equity: {tracker.equity:.2f} | ROI: {(tracker.equity/900-1)*100:.1f}% | Trades: {total} | Wins: {wins}")

        # update losses count
        if tracker.trade_history and tracker.trade_history[-1]["pnl"] < 0:
            consecutive_losses += 1
        else:
            consecutive_losses = 0

        await asyncio.sleep(cfg["execution_interval_sec"])

if __name__ == "__main__":
    asyncio.run(main())
