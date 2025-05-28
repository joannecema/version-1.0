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
        self.lookback = int(cfg.get("lookback", 30))
        self.timeframe = cfg.get("timeframe", "1m")

        self.logger = logging.getLogger("PairsTrading")

    async def check_and_trade(self, _):
        pairs = self.cfg.get("cross_ex_pairs", [])
        if not pairs:
            self.logger.warning("[PAIRS] No trading pairs configured.")
            return

        for sym_a, sym_b in pairs:
            try:
                ohlcv_a = await self.api.get_ohlcv(sym_a, self.timeframe, self.lookback + 1)
                ohlcv_b = await self.api.get_ohlcv(sym_b, self.timeframe, self.lookback + 1)

                if not ohlcv_a or not ohlcv_b:
                    self.logger.warning(f"[PAIRS] Empty OHLCV data for {sym_a}/{sym_b}")
                    continue

                if len(ohlcv_a) < self.lookback + 1 or len(ohlcv_b) < self.lookback + 1:
                    self.logger.warning(f"[PAIRS] Insufficient data: {sym_a}={len(ohlcv_a)}, {sym_b}={len(ohlcv_b)}")
                    continue

                z = calculate_spread_zscore(ohlcv_a, ohlcv_b)

                if z is None or not isinstance(z, (int, float)):
                    self.logger.warning(f"[PAIRS] Invalid z-score for {sym_a}/{sym_b}: {z}")
                    continue

                self.logger.debug(f"[PAIRS] Z-score for {sym_a}/{sym_b} = {z:.4f}")

                if z > self.zscore_entry:
                    self.logger.info(f"[PAIRS] 🔻 Short {sym_a} / Long {sym_b} | Z={z:.2f}")
                    await self.executor.enter_short(sym_a)
                    await self.executor.enter_long(sym_b)

                elif z < -self.zscore_entry:
                    self.logger.info(f"[PAIRS] 🔺 Long {sym_a} / Short {sym_b} | Z={z:.2f}")
                    await self.executor.enter_long(sym_a)
                    await self.executor.enter_short(sym_b)

                elif abs(z) < self.zscore_exit:
                    self.logger.info(f"[PAIRS] ⏹ Z-score below exit threshold | Z={z:.2f}")
                    await self.executor.exit_position(sym_a)
                    await self.executor.exit_position(sym_b)

            except Exception as e:
                self.logger.error(f"[PAIRS] Error in pair {sym_a}/{sym_b}: {e}")