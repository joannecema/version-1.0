import logging

log = logging.getLogger("VRF")

class VolatilityRegimeFilter:
    def __init__(self, api: "ApiHandler", cfg: dict):
        self.api = api
        self.cfg = cfg

    async def allow_trading(self, symbol: str) -> bool:
        """
        Compute volatility over the lookback window.
        Uses ApiHandler.get_ohlcv so it always returns valid data.
        """
        try:
            ohlcv = await self.api.get_ohlcv(
                symbol,
                self.cfg["timeframe"],
                self.cfg["lookback"] + 1,
            )
        except Exception as e:
            log.error(f"[VRF] ❌ OHLCV fetch failed for {symbol}: {e}")
            return False

        if not ohlcv or len(ohlcv) < 2:
            log.warning(f"[VRF] ❌ Not enough OHLCV data for {symbol}")
            return False

        closes = [candle[4] for candle in ohlcv if candle[4] > 0]
        if len(closes) < 2:
            log.warning(f"[VRF] ❌ Invalid or insufficient closing price data for {symbol}")
            return False

        try:
            returns = [
                abs((closes[i] - closes[i - 1]) / closes[i - 1])
                for i in range(1, len(closes))
            ]
            vol = sum(returns) / len(returns)
        except ZeroDivisionError:
            log.warning(f"[VRF] ⚠️ Division by zero in return calculation for {symbol}")
            return False

        log.info(f"[VRF] {symbol} volatility={vol:.4f}")

        threshold = self.cfg.get("threshold", self.cfg.get("volatility_threshold_atr"))
        if threshold is None:
            log.error("[VRF] ❌ No volatility threshold configured (missing 'threshold' and 'volatility_threshold_atr')")
            return False

        allowed = vol < threshold
        log.debug(f"[VRF] {symbol} allowed={allowed} (vol={vol:.5f}, threshold={threshold})")
        return allowed