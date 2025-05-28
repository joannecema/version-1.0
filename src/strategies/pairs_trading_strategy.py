import logging
from src.utils import calculate_spread_zscore

class PairsTradingStrategy:
    def __init__(self, api, tracker, executor, cfg):
        self.api = api
        self.tracker = tracker
        self.executor = executor
        self.cfg = cfg

        self.zscore_entry = cfg.get("zscore_entry", 1.5)
        self.zscore_exit = cfg.get("zscore_exit", 0.5)

        self.logger = logging.getLogger("PairsTrading")

    async def check_and_trade(self, _):
        pairs = self.cfg.get("cross_ex_pairs", [])
        for sym_a, sym_b in pairs:
            try:
                ohlcv_a = await self.api.get_ohlcv(sym_a, self.cfg["timeframe"], self.cfg["lookback"] + 1)
                ohlcv_b = await self.api.get_ohlcv(sym_b, self.cfg["timeframe"], self.cfg["lookback"] + 1)

                if len(ohlcv_a) < self.cfg["lookback"] + 1 or len(ohlcv_b) < self.cfg["lookback"] + 1:
                    self.logger.warning(f"[PAIRS] Not enough data for pair {sym_a}/{sym_b}")
                    continue

                z = calculate_spread_zscore(ohlcv_a, ohlcv_b)

                if z is None or not isinstance(z, (float, int)):
                    self.logger.warning(f"[PAIRS] Invalid z-score for {sym_a}/{sym_b}: {z}")
                    continue

                if z > self.zscore_entry:
                    self.logger.info(f"[PAIRS] Short {sym_a}/Long {sym_b} z={z:.2f}")
                    await self.executor.enter_short(sym_a)
                    await self.executor.enter_long(sym_b)

                elif z < -self.zscore_entry:
                    self.logger.info(f"[PAIRS] Long {sym_a}/Short {sym_b} z={z:.2f}")
                    await self.executor.enter_long(sym_a)
                    await self.executor.enter_short(sym_b)

            except Exception as e:
                self.logger.error(f"[PAIRS] Error on pair {sym_a}/{sym_b}: {e}")