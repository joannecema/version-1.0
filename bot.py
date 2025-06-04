import os
import json
import asyncio
import logging
import signal
import contextlib
from aiohttp import web
from datetime import datetime

from src.api_handler import ApiHandler
from src.position_tracker import PositionTracker
from src.trade_executor import AsyncTradeExecutor
from src.strategy_manager import StrategyManager
from src.volatility_regime_filter import VolatilityRegimeFilter

# Configure shutdown timeout
GRACEFUL_SHUTDOWN_TIMEOUT = 30  # seconds

def validate_config(cfg):
    """Flexible configuration validation with strategy awareness"""
    required_core = {
        "api_key": str,
        "api_secret": str,
        "symbols_count": int,
        "timeframe": str,
        "risk_pct": float,
        "health_port": int,
        "log_file": str,
    }
    
    for k, t in required_core.items():
        if k not in cfg or not isinstance(cfg[k], t):
            raise RuntimeError(f"Config error: {k} missing or not {t.__name__}")
            
    # Strategy-specific validation
    strategy_params = {
        "breakout": ["breakout_lookback", "volume_multiplier"],
        "ema_rsi": ["ema_short", "ema_long", "rsi_period"],
        "scalping": ["scalping_momentum_period", "scalping_volume_multiplier"],
        "grid": ["mm_deviation_threshold", "mm_lookback"]
    }
    
    for strategy in cfg.get("strategy_stack", []):
        if strategy in strategy_params:
            for param in strategy_params[strategy]:
                if param not in cfg:
                    logging.warning(f"Missing recommended parameter for {strategy}: {param}")

def setup_logging(log_file):
    """Configure structured logging with file rotation"""
    logger = logging.getLogger()
    logger.setLevel(logging.INFO)
    
    # Console logging
    console_fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)s %(message)s", 
        "%Y-%m-%d %H:%M:%S"
    )
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(console_fmt)
    
    # File logging with rotation
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
    
    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    
    # Suppress noisy logs
    logging.getLogger("aiohttp").setLevel(logging.WARNING)
    logging.getLogger("ccxt").setLevel(logging.INFO)

async def health(request):
    """Enhanced health check with system status"""
    tracker = request.app["tracker"]
    status = {
        "status": "running",
        "positions": len(tracker.positions),
        "last_cycle": request.app.get("last_cycle", "never"),
        "balance": tracker.balance,
        "daily_pnl": await tracker.daily_pnl()
    }
    return web.json_response(status)

async def shutdown(app):
    """Graceful shutdown procedure"""
    logging.info("🚦 Shutdown signal received - initiating graceful shutdown")
    app["shutting_down"] = True
    
    # Close HTTP server
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(app.shutdown(), timeout=5)
    
    # Close components
    components = app.get("components", {})
    for name, component in components.items():
        if hasattr(component, "close"):
            logging.info(f"Closing {name}")
            await component.close()
        elif hasattr(component, "stop"):
            component.stop()
    
    logging.info("✅ Shutdown complete")

async def start_http_server(app, port):
    """Start HTTP server for health checks"""
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", port)
    await site.start()
    logging.info(f"🌐 Health check server running on port {port}")

async def fetch_dynamic_symbols(api, config):
    """Fetch top symbols with volume filtering"""
    return await api.get_top_symbols(
        count=config["symbols_count"],
        min_volume=config.get("min_symbol_volume", 1000000),
        min_liquidity=config.get("min_liquidity", 0.001),
        exclude_stable=True
    )

async def main():
    """Main trading bot execution"""
    # Load configuration
    try:
        with open("config.json") as f:
            config = json.load(f)
        validate_config(config)
    except Exception as e:
        logging.critical(f"Configuration error: {str(e)}")
        return
    
    # Initialize logging
    setup_logging(config["log_file"])
    logging.info("🚀 Starting HFT bot - Phemex deployment")
    
    # Initialize API handler
    api = ApiHandler(
        api_key=config["api_key"],
        api_secret=config["api_secret"],
        config=config
    )
    await api.load_markets()
    
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
        "strategy_manager": strategy_manager
    }
    app.router.add_get("/health", health)
    
    # Start health server
    await start_http_server(app, config["health_port"])
    
    # Register shutdown signals
    loop = asyncio.get_running_loop()
    for signame in ("SIGINT", "SIGTERM"):
        loop.add_signal_handler(
            getattr(signal, signame),
            lambda: asyncio.create_task(shutdown(app))
        )
    
    # Trading loop variables
    cycle_count = 0
    consecutive_losses = 0
    last_symbol_refresh = 0
    symbols = config.get("symbols", ["BTC/USDT"])
    
    try:
        logging.info("✅ Bot initialization complete - starting trading loop")
        while True:
            app["last_cycle"] = datetime.utcnow().isoformat()
            cycle_count += 1
            
            # Refresh symbols periodically (if dynamic universe enabled)
            if config.get("dynamic_universe", False):
                now = time.time()
                if now - last_symbol_refresh > 3600:  # Refresh hourly
                    try:
                        symbols = await fetch_dynamic_symbols(api, config)
                        logging.info(f"🔄 Updated symbols: {symbols}")
                        last_symbol_refresh = now
                    except Exception as e:
                        logging.error(f"Symbol refresh failed: {str(e)}")
            
            # Check circuit breaker conditions
            daily_pnl = await tracker.daily_pnl()
            if daily_pnl <= -config["daily_loss_limit"]:
                logging.critical(f"🔴 Daily loss limit reached: {daily_pnl*100:.2f}% - trading halted")
                break
                
            if consecutive_losses >= config["max_consecutive_losses"]:
                logging.warning(f"⚠️ Consecutive losses ({consecutive_losses}) - pausing")
                await asyncio.sleep(config["pause_seconds_on_break"])
                consecutive_losses = 0
            
            # Execute trading cycle
            try:
                # Apply volatility filter
                tradable_symbols = []
                for symbol in symbols:
                    if await volatility_filter.allow_trading(symbol):
                        tradable_symbols.append(symbol)
                    else:
                        logging.debug(f"Volatility filter blocked {symbol}")
                
                # Run strategy manager
                if tradable_symbols:
                    await strategy_manager.execute(tradable_symbols)
                
                # Manage risk for open positions
                await tracker.manage_risk()
                
            except Exception as e:
                logging.error(f"Trading cycle error: {str(e)}", exc_info=True)
            
            # Update loss counter
            if tracker.trade_history and tracker.trade_history[-1]["pnl"] < 0:
                consecutive_losses += 1
            else:
                consecutive_losses = 0
                
            # Periodic reporting
            if cycle_count % config["report_interval_cycles"] == 0:
                await report_status(tracker, cycle_count)
            
            # Sleep until next cycle
            await asyncio.sleep(config["execution_interval_sec"])
            
    except asyncio.CancelledError:
        logging.info("Main loop cancelled")
    finally:
        # Final shutdown cleanup
        await shutdown(app)
        logging.info("👋 Bot shutdown complete")

async def report_status(tracker, cycle_count):
    """Generate periodic performance report"""
    await tracker.sync()
    
    # Calculate performance metrics
    win_count = sum(1 for t in tracker.trade_history if t["pnl"] > 0)
    loss_count = len(tracker.trade_history) - win_count
    win_rate = (win_count / len(tracker.trade_history)) * 100 if tracker.trade_history else 0
    
    # Get daily P&L
    daily_pnl = await tracker.daily_pnl()
    
    # Open positions summary
    position_summary = ", ".join(
        f"{pos['symbol']}:{pos['side'][0]}{pos['size']:.2f}" 
        for pos in tracker.positions.values()
    ) if tracker.positions else "None"
    
    logging.info(
        f"📊 CYCLE {cycle_count} | "
        f"Equity: ${tracker.balance:.2f} | "
        f"Daily P&L: {daily_pnl*100:+.2f}% | "
        f"Positions: {len(tracker.positions)} [{position_summary}] | "
        f"Trades: {len(tracker.trade_history)} | "
        f"Win Rate: {win_rate:.1f}%"
    )

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot terminated by user")
    except Exception as e:
        logging.critical(f"Unhandled error: {str(e)}", exc_info=True)
