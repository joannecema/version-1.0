# pairs_trading_strategy.py
import logging
import asyncio
import numpy as np
from src.utils import calculate_spread_zscore

log = logging.getLogger("PairsTrading")

class PairsTradingStrategy:
    def __init__(self, api, tracker, executor, cfg):
        self.api = api
        self.tracker = tracker
        self.executor = executor
        self.cfg = cfg

        self.zscore_entry = cfg.get("zscore_entry", 1.5)
        self.zscore_exit = cfg.get("zscore_exit", 0.5)
        self.lookback = int(cfg.get("lookback", 30))
        self.timeframe = cfg.get("timeframe", "1m")

    async def check_and_trade(self):
        pairs = self.cfg.get("trading_pairs", [])
        if not pairs:
            return

        for sym_a, sym_b in pairs:
            try:
                ohlcv_a, ohlcv_b = await asyncio.gather(
                    self.api.get_ohlcv(sym_a, self.timeframe, self.lookback + 1),
                    self.api.get_ohlcv(sym_b, self.timeframe, self.lookback + 1)
                )
                
                if not ohlcv_a or not ohlcv_b:
                    continue
                    
                z = calculate_spread_zscore(ohlcv_a, ohlcv_b)
                if z is None:
                    continue

                has_a = self.tracker.has_open_position(sym_a)
                has_b = self.tracker.has_open_position(sym_b)

                if z > self.zscore_entry and not (has_a or has_b):
                    log.info(f"[PAIRS] Short {sym_a} / Long {sym_b} | Z={z:.2f}")
                    await asyncio.gather(
                        self.executor.execute_order(sym_a, "sell", self.cfg.get("pair_size", 1)),
                        self.executor.execute_order(sym_b, "buy", self.cfg.get("pair_size", 1))
                    )
                elif z < -self.zscore_entry and not (has_a or has_b):
                    log.info(f"[PAIRS] Long {sym_a} / Short {sym_b} | Z={z:.2f}")
                    await asyncio.gather(
                        self.executor.execute_order(sym_a, "buy", self.cfg.get("pair_size", 1)),
                        self.executor.execute_order(sym_b, "sell", self.cfg.get("pair_size", 1))
                    )
                elif abs(z) < self.zscore_exit and (has_a or has_b):
                    log.info(f"[PAIRS] Exiting {sym_a}/{sym_b} | Z={z:.2f}")
                    await asyncio.gather(
                        self.executor.execute_order(sym_a, "close", self.cfg.get("pair_size", 1)),
                        self.executor.execute_order(sym_b, "close", self.cfg.get("pair_size", 1))
                    )

            except Exception as e:
                log.error(f"[PAIRS] Error for {sym_a}/{sym_b}: {e}")
