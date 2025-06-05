# bot.py
import os
import json
import asyncio
import logging
import signal
import time
import contextlib
import logging.handlers
from datetime import datetime, timezone
from aiohttp import web

from src.api_handler import ApiHandler
from src.position_tracker import PositionTracker
from src.trade_executor import AsyncTradeExecutor
from src.strategy_manager import StrategyManager
from src.volatility_regime_filter import VolatilityRegimeFilter

GRACEFUL_SHUTDOWN_TIMEOUT = 30

def validate_config(cfg):
    api_key = os.getenv("PHEMEX_API_KEY")
    api_secret = os.getenv("PHEMEX_API_SECRET")
    
    if not api_key or not api_secret:
        raise RuntimeError("API credentials not found in environment variables")
    
    required_params = ["symbols_count", "timeframe", "risk_pct", 
                      "health_port", "log_file", "execution_interval_sec",
                      "report_interval_cycles"]
    
    for param in required_params:
        if param not in cfg:
            raise RuntimeError(f"Missing required parameter: {param}")

def setup_logging(log_file):
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    console_fmt = logging.Formatter("[%(asctime)s] %(levelname)s %(message)s", "%Y-%m-%d %H:%M:%S")
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_fmt)
    logger.addHandler(console_handler)
    
    if log_file:
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                log_file, maxBytes=10*1024*1024, backupCount=5
            )
            file_fmt = logging.Formatter(
                "%(asctime)s | %(levelname)8s | %(name)20s | %(message)s",
                "%Y-%m-%d %H:%M:%S"
            )
            file_handler.setFormatter(file_fmt)
            logger.addHandler(file_handler)
        except Exception as e:
            logging.error(f"File logging failed: {str(e)}")

async def health(request):
    app = request.app
    tracker = app.get("tracker", None)
    
    status = {
        "status": "running" if not app.get("shutting_down") else "shutting_down",
        "last_cycle": app.get("last_cycle", "never"),
        "cycle_count": app.get("cycle_count", 0),
    }
    
    if tracker:
        status.update({
            "positions": len(tracker.positions),
            "balance": tracker.balance,
            "daily_pnl": await tracker.daily_pnl() if hasattr(tracker, "daily_pnl") else 0
        })
    
    return web.json_response(status)

async def shutdown(app):
    if app.get("shutting_down"):
        return
    
    logging.info("🚦 Initiating graceful shutdown")
    app["shutting_down"] = True
    
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(app.shutdown(), timeout=5)
    
    components = app.get("components", {})
    for name, component in components.items():
        try:
            if hasattr(component, "close"):
                logging.info(f"Closing {name}")
                await asyncio.wait_for(component.close(), timeout=5)
            elif hasattr(component, "stop"):
                component.stop()
        except asyncio.TimeoutError:
            logging.warning(f"Timeout closing {name}")
        except Exception as e:
            logging.error(f"Error closing {name}: {str(e)}")
    
    logging.info("✅ Shutdown complete")

async def start_http_server(app, port):
    try:
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logging.info(f"🌐 Health server running on port {port}")
        return True
    except OSError as e:
        logging.critical(f"Failed to start health server: {str(e)}")
        return False

async def main():
    try:
        with open("config.json") as f:
            config = json.load(f)
        validate_config(config)
    except Exception as e:
        logging.critical(f"Configuration error: {str(e)}")
        return
    
    setup_logging(config.get("log_file"))
    logging.info("🚀 Starting Trading Bot")
    
    api_key = os.getenv("PHEMEX_API_KEY")
    api_secret = os.getenv("PHEMEX_API_SECRET")
    
    try:
        api = ApiHandler(api_key=api_key, api_secret=api_secret, config=config)
        await api.load_markets()
    except Exception as e:
        logging.critical(f"API initialization failed: {str(e)}")
        return
    
    tracker = PositionTracker(config, api)
    executor = AsyncTradeExecutor(api, config)
    await executor.start()
    
    strategy_manager = StrategyManager(config, api, tracker, executor)
    volatility_filter = VolatilityRegimeFilter(api, config)
    
    app = web.Application()
    app["tracker"] = tracker
    app["components"] = {
        "api": api,
        "executor": executor,
        "strategy_manager": strategy_manager,
        "volatility_filter": volatility_filter
    }
    app.router.add_get("/health", health)
    
    if not await start_http_server(app, config["health_port"]):
        return
    
    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        loop.add_signal_handler(
            getattr(signal, signame),
            lambda: asyncio.create_task(shutdown(app))
        )
    
    app["cycle_count"] = 0
    consecutive_errors = 0
    symbols = config.get("symbols", ["BTC/USDT"])
    
    try:
        logging.info("✅ Starting trading loop")
        while not app.get("shutting_down", False):
            app["last_cycle"] = datetime.now(timezone.utc).isoformat()
            app["cycle_count"] += 1
            cycle_count = app["cycle_count"]
            
            try:
                # Apply volatility filter
                tradable_symbols = []
                for symbol in symbols:
                    if await volatility_filter.allow_trading(symbol):
                        tradable_symbols.append(symbol)
                
                # Run strategy manager
                if tradable_symbols:
                    await strategy_manager.execute(tradable_symbols)
                
                # Manage risk
                await tracker.manage_risk()
                
                # Reset error counter on success
                consecutive_errors = 0
                
            except Exception as e:
                consecutive_errors += 1
                logging.error(f"Trading cycle error: {str(e)}")
                if consecutive_errors > 5:
                    logging.critical("Too many consecutive errors, shutting down")
                    await shutdown(app)
                    break
            
            # Periodic reporting
            if cycle_count % config["report_interval_cycles"] == 0:
                await report_status(tracker, cycle_count)
            
            # Circuit breaker
            daily_pnl = await tracker.daily_pnl()
            if daily_pnl <= -config.get("daily_loss_limit", 0.05):
                logging.critical(f"🔴 Daily loss limit reached: {daily_pnl*100:.2f}%")
                await shutdown(app)
                break
                
            await asyncio.sleep(config["execution_interval_sec"])
            
    except asyncio.CancelledError:
        logging.info("Main loop cancelled")
    finally:
        if not app.get("shutting_down"):
            await shutdown(app)
        logging.info("👋 Bot shutdown complete")

async def report_status(tracker, cycle_count):
    try:
        await tracker.sync()
        win_count = sum(1 for t in tracker.trade_history if t.get("pnl", 0) > 0)
        loss_count = len(tracker.trade_history) - win_count
        win_rate = (win_count / len(tracker.trade_history)) * 100 if tracker.trade_history else 0
        
        position_summary = ", ".join(
            f"{pos['symbol']}:{pos['side'][0]}{pos['size']:.2f}" 
            for pos in tracker.positions.values()
        ) if tracker.positions else "None"
        
        logging.info(
            f"📊 CYCLE {cycle_count} | "
            f"Equity: ${tracker.balance:.2f} | "
            f"Positions: {len(tracker.positions)} [{position_summary}] | "
            f"Trades: {len(tracker.trade_history)} | "
            f"Win Rate: {win_rate:.1f}%"
        )
    except Exception as e:
        logging.error(f"Reporting error: {str(e)}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot terminated by user")
    except Exception as e:
        logging.critical(f"Unhandled error: {str(e)}", exc_info=True)
