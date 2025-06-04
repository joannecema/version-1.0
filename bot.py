import os
import json
import asyncio
import logging
import signal
import time  # Added missing import
import contextlib
import logging.handlers
from datetime import datetime, timezone  # Improved time handling
from aiohttp import web

from src.api_handler import ApiHandler
from src.position_tracker import PositionTracker
from src.trade_executor import AsyncTradeExecutor
from src.strategy_manager import StrategyManager
from src.volatility_regime_filter import VolatilityRegimeFilter

# Configure shutdown timeout
GRACEFUL_SHUTDOWN_TIMEOUT = 30  # seconds

def validate_config(cfg):
    """Enhanced configuration validation with exchange section support"""
    # Check for exchange credentials in both top-level and exchange section
    api_key = cfg.get("api_key") or (cfg.get("exchange", {}).get("api_key"))
    api_secret = cfg.get("api_secret") or (cfg.get("exchange", {}).get("api_secret"))
    
    if not api_key or not isinstance(api_key, str):
        raise RuntimeError("api_key missing or not str")
    if not api_secret or not isinstance(api_secret, str):
        raise RuntimeError("api_secret missing or not str")
    
    # Required core parameters
    required_params = {
        "symbols_count": int,
        "timeframe": str,
        "risk_pct": float,
        "health_port": int,
        "log_file": str,
        "execution_interval_sec": int,
        "report_interval_cycles": int,
    }
    
    for param, param_type in required_params.items():
        if param not in cfg:
            raise RuntimeError(f"Missing required parameter: {param}")
        if not isinstance(cfg[param], param_type):
            raise RuntimeError(f"{param} should be {param_type.__name__}")

def setup_logging(log_file):
    """Robust logging setup with error handling"""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Clear existing handlers to prevent duplication
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
    
    # Console logging
    console_fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(message)s", 
        "%Y-%m-%d %H:%M:%S"
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_fmt)
    logger.addHandler(console_handler)
    
    # File logging with rotation (if log file specified)
    if log_file:
        try:
            file_handler = logging.handlers.RotatingFileHandler(
                log_file,
                maxBytes=10*1024*1024,  # 10 MB
                backupCount=5
            )
            file_fmt = logging.Formatter(
                "%(asctime)s | %(levelname)8s | %(name)20s | %(message)s",
                "%Y-%m-%d %H:%M:%S"
            )
            file_handler.setFormatter(file_fmt)
            logger.addHandler(file_handler)
        except PermissionError:
            logger.error(f"Permission denied for log file: {log_file}")
        except Exception as e:
            logger.error(f"Failed to setup file logging: {str(e)}")
    
    # Suppress noisy logs
    for lib in ["aiohttp", "ccxt", "asyncio"]:
        logging.getLogger(lib).setLevel(logging.WARNING)

async def health(request):
    """Enhanced health check with trading status"""
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
    """Graceful shutdown with timeout handling"""
    if app.get("shutting_down"):
        return
    
    logging.info("🚦 Shutdown signal received - initiating graceful shutdown")
    app["shutting_down"] = True
    
    # Close HTTP server
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(app.shutdown(), timeout=5)
    
    # Close components with timeout protection
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
    """Start HTTP server with error handling"""
    try:
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logging.info(f"🌐 Health check server running on port {port}")
        return True
    except OSError as e:
        logging.critical(f"Failed to start health server: {str(e)}")
        return False

async def fetch_dynamic_symbols(api, config):
    """Fetch symbols with error handling"""
    try:
        return await api.get_top_symbols(
            count=config["symbols_count"],
            min_volume=config.get("min_symbol_volume", 1000000),
            min_liquidity=config.get("min_liquidity", 0.001),
            exclude_stable=True
        )
    except Exception as e:
        logging.error(f"Symbol refresh failed: {str(e)}")
        return config.get("symbols", ["BTC/USDT"])  # Fallback to default

async def main():
    """Main trading bot execution with enhanced robustness"""
    # Load configuration with improved error handling
    try:
        with open("config.json") as f:
            config = json.load(f)
        validate_config(config)
    except json.JSONDecodeError as e:
        logging.critical(f"Configuration JSON error: {str(e)}")
        return
    except Exception as e:
        logging.critical(f"Configuration error: {str(e)}")
        return
    
    # Initialize logging
    setup_logging(config.get("log_file"))
    logging.info("🚀 Starting Trading Bot")
    
    # Get credentials from exchange section if available
    exchange_config = config.get("exchange", {})
    api_key = exchange_config.get("api_key", config.get("api_key"))
    api_secret = exchange_config.get("api_secret", config.get("api_secret"))
    
    # Initialize API handler
    try:
        api = ApiHandler(api_key=api_key, api_secret=api_secret, config=config)
        await api.load_markets()
    except Exception as e:
        logging.critical(f"API initialization failed: {str(e)}")
        return
    
    # Initialize core components
    tracker = PositionTracker(config, api)
    executor = AsyncTradeExecutor(api, config)
    await executor.start()
    
    strategy_manager = StrategyManager(config, api, tracker, executor)
    volatility_filter = VolatilityRegimeFilter(api, config)
    
    # Create application context
    app = web.Application()
    app["tracker"] = tracker
    app["components"] = {
        "api": api,
        "executor": executor,
        "strategy_manager": strategy_manager,
        "volatility_filter": volatility_filter
    }
    app.router.add_get("/health", health)
    
    # Start health server
    if not await start_http_server(app, config["health_port"]):
        return
    
    # Register shutdown signals
    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        loop.add_signal_handler(
            getattr(signal, signame),
            lambda: asyncio.create_task(shutdown(app))
        )
    
    # Trading loop variables
    app["cycle_count"] = 0
    consecutive_losses = 0
    last_symbol_refresh = 0
    symbols = config.get("symbols", ["BTC/USDT"])
    
    try:
        logging.info("✅ Bot initialization complete - starting trading loop")
        while not app.get("shutting_down", False):
            app["last_cycle"] = datetime.now(timezone.utc).isoformat()
            app["cycle_count"] += 1
            cycle_count = app["cycle_count"]
            
            # Refresh symbols periodically
            if config.get("dynamic_universe", False):
                now = time.time()
                if now - last_symbol_refresh > 3600:  # Refresh hourly
                    symbols = await fetch_dynamic_symbols(api, config)
                    last_symbol_refresh = now
            
            # Check circuit breaker conditions
            try:
                daily_pnl = await tracker.daily_pnl()
                if daily_pnl <= -config.get("daily_loss_limit", 0.05):
                    logging.critical(f"🔴 Daily loss limit reached: {daily_pnl*100:.2f}%")
                    break
            except Exception as e:
                logging.error(f"PNL check failed: {str(e)}")
            
            # Execute trading cycle
            try:
                # Apply volatility filter
                tradable_symbols = [
                    symbol for symbol in symbols 
                    if await volatility_filter.allow_trading(symbol)
                ]
                
                # Run strategy manager
                if tradable_symbols:
                    await strategy_manager.execute(tradable_symbols)
                
                # Manage risk for open positions
                await tracker.manage_risk()
                
            except Exception as e:
                logging.error(f"Trading cycle error: {str(e)}", exc_info=True)
            
            # Periodic reporting
            if cycle_count % config["report_interval_cycles"] == 0:
                await report_status(tracker, cycle_count)
            
            # Sleep until next cycle
            await asyncio.sleep(config["execution_interval_sec"])
            
    except asyncio.CancelledError:
        logging.info("Main loop cancelled")
    finally:
        if not app.get("shutting_down"):
            await shutdown(app)
        logging.info("👋 Bot shutdown complete")

async def report_status(tracker, cycle_count):
    """Generate periodic performance report with error handling"""
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
