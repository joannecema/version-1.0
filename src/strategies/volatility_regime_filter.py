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
            log.error(f"[VRF] OHLCV fetch failed for {symbol}: {e}")
            return False

        # closing prices are column index 4
        closes = [candle[4] for candle in ohlcv]
        returns = [
            abs((closes[i] - closes[i - 1]) / closes[i - 1])
            for i in range(1, len(closes))
        ]
        vol = sum(returns) / len(returns)
        log.info(f"[VRF] {symbol} volatility={vol:.4f}")

        return vol < self.cfg["threshold"]
