import logging

log = logging.getLogger("VRF")

class VolatilityRegimeFilter:
    def __init__(self, api: "ApiHandler", cfg: dict):
        self.api = api
        self.cfg = cfg
        self.timeframe = cfg.get("timeframe", "1m")
        self.lookback = int(cfg.get("lookback", 10))
        self.threshold = cfg.get("threshold", cfg.get("volatility_threshold_atr", 0.02))

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
            log.warning(f"[VRF] ❌ Not enough OHLCV data for {symbol}")
            return False

        closes = [bar[4] for bar in ohlcv if isinstance(bar[4], (float, int)) and bar[4] > 0]
        if len(closes) < 2:
            log.warning(f"[VRF] ❌ Invalid or insufficient close data for {symbol}")
            return False

        try:
            returns = [abs((closes[i] - closes[i - 1]) / closes[i - 1]) for i in range(1, len(closes))]
            volatility = sum(returns) / len(returns)
        except ZeroDivisionError:
            log.warning(f"[VRF] ⚠️ ZeroDivisionError during return calculation for {symbol}")
            return False
        except Exception as e:
            log.error(f"[VRF] ❌ Unexpected error during volatility calc for {symbol}: {e}")
            return False

        log.info(f"[VRF] {symbol} volatility={volatility:.5f}")

        if self.threshold is None:
            log.error("[VRF] ❌ Volatility threshold not configured.")
            return False

        allowed = volatility < self.threshold
        log.debug(f"[VRF] {symbol} allowed={allowed} (vol={volatility:.5f}, threshold={self.threshold})")
        return allowed