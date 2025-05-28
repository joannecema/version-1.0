import logging

log = logging.getLogger("VRF")

class VolatilityRegimeFilter:
    def __init__(self, api: "ApiHandler", cfg: dict):
        self.api = api
        self.cfg = cfg
        self.timeframe = cfg.get("timeframe", "1m")
        self.lookback = int(cfg.get("lookback", 10))
        self.threshold = cfg.get("threshold") or cfg.get("volatility_threshold_atr", 0.02)

    async def allow_trading(self, symbol: str) -> bool:
        """
        Evaluate if trading is permitted based on average volatility over the lookback window.
        """
        try:
            ohlcv = await self.api.get_ohlcv(symbol, self.timeframe, self.lookback + 1)
        except Exception as e:
            log.error(f"[VRF] ❌ Failed to fetch OHLCV for {symbol}: {e}")
            return False

        if not ohlcv or len(ohlcv) < 2:
            log.warning(f"[VRF] ❌ Not enough OHLCV data for {symbol} (received {len(ohlcv)})")
            return False

        closes = [bar[4] for bar in ohlcv if isinstance(bar[4], (float, int)) and bar[4] > 0]
        if len(closes) < 2:
            log.warning(f"[VRF] ❌ Invalid or insufficient closing prices for {symbol}: {closes}")
            return False

        try:
            returns = [
                abs((closes[i] - closes[i - 1]) / closes[i - 1])
                for i in range(1, len(closes)) if closes[i - 1] != 0
            ]
            if not returns:
                log.warning(f"[VRF] ❌ No valid returns for {symbol}")
                return False
            volatility = sum(returns) / len(returns)
        except Exception as e:
            log.error(f"[VRF] ❌ Error calculating volatility for {symbol}: {e}")
            return False

        log.info(f"[VRF] {symbol} volatility={volatility:.5f}")

        if self.threshold is None:
            log.error("[VRF] ❌ Volatility threshold is not set in config.")
            return False

        allowed = volatility < self.threshold
        log.debug(f"[VRF] {symbol} allowed={allowed} (vol={volatility:.5f}, threshold={self.threshold})")
        return allowed