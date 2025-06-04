import logging
from typing import Optional

class VolatilityRegimeFilter:
    def __init__(self, api_handler, config: dict):
        self.api = api_handler
        self.config = config
        self.logger = logging.getLogger("VolatilityRegimeFilter")
        self.threshold = config.get("volatility_threshold_atr", 0.015)  # ~1.5% ATR default

    async def allow_trading(self, symbol: str) -> bool:
        try:
            ohlcv = await self.api.fetch_ohlcv(symbol, timeframe=self.config["timeframe"], limit=self.config["atr_period"] + 1)
            if not ohlcv or len(ohlcv) <= self.config["atr_period"]:
                self.logger.warning(f"[VRF] ❌ Not enough data for {symbol}")
                return False

            atr = self._calculate_atr(ohlcv)
            last_close = ohlcv[-1][4]
            volatility = atr / last_close

            self.logger.info(f"[VRF] {symbol} ATR={atr:.4f} Close={last_close:.4f} Volatility={volatility:.2%} Threshold={self.threshold:.2%}")

            return volatility < self.threshold
        except Exception as e:
            self.logger.error(f"[VRF] Error evaluating {symbol}: {e}")
            return True  # Fallback: don't block trading

    def _calculate_atr(self, ohlcv: list) -> float:
        tr_list = []
        for i in range(1, len(ohlcv)):
            high = ohlcv[i][2]
            low = ohlcv[i][3]
            prev_close = ohlcv[i - 1][4]
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            tr_list.append(tr)
        return sum(tr_list) / len(tr_list)
