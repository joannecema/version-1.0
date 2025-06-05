# src/volatility_regime_filter.py

import logging
import time                                 # ← for timestamp calculations
from typing import Optional                 # ← for the return‐type annotation
import numpy as np

log = logging.getLogger("VRF")


class VolatilityRegimeFilter:
    def __init__(self, api: "ApiHandler", cfg: dict):
        self.api = api
        self.cfg = cfg
        self.timeframe = cfg.get("timeframe", "1m")
        self.lookback = int(cfg.get("lookback", 10))
        self.threshold = cfg.get("threshold") or cfg.get("volatility_threshold_atr", 0.02)
        self.min_data_points = 5  # Minimum candles needed for calculation

    async def allow_trading(self, symbol: str) -> bool:
        """
        Evaluate if trading is permitted based on volatility, by fetching
        OHLCV with both 'since' and 'limit' so that Phemex does not complain
        about a missing 'to' parameter.
        """
        try:
            # 1) Compute how many candles we need (lookback + 5 as buffer).
            limit = self.lookback + 5

            # 2) Compute 'since' in milliseconds (so Phemex sees a valid time window).
            since = await self._calculate_since_timestamp(symbol)
            if since is None:
                log.debug(f"[VRF] Could not calculate 'since' for {symbol}")
                return False

            # 3) Under Phemex, CCXT’s fetch_ohlcv requires both a 'from' (since) and an implicit 'to'.
            #    We grab the same semaphore that ApiHandler uses, to avoid too‐many‐requests.
            async with self.api.semaphore:
                ohlcv = await self.api.exchange.fetch_ohlcv(
                    symbol,
                    self.timeframe,
                    since,
                    limit,
                )

            if not ohlcv:
                log.debug(f"[VRF] No OHLCV data for {symbol}")
                return False

            # 4) Ensure we have at least the minimum candles before proceeding.
            if len(ohlcv) < self.min_data_points:
                log.debug(f"[VRF] Insufficient data for {symbol} (only {len(ohlcv)} candles)")
                return False

            # 5) Extract valid close prices out of each candle.
            closes = []
            for candle in ohlcv:
                # CCXT OHLCV format is [ timestamp, open, high, low, close, volume ]
                if len(candle) >= 5:
                    close_price = candle[4]
                    if isinstance(close_price, (int, float)) and close_price > 0:
                        closes.append(close_price)

            if len(closes) < 2:
                log.debug(f"[VRF] Not enough valid closes for {symbol}")
                return False

            # 6) Compute absolute returns between consecutive closes.
            arr = np.array(closes)
            prev = arr[:-1]
            curr = arr[1:]

            # Mask out any zero‐previous‐close to avoid division by zero.
            mask = prev > 0
            if not np.any(mask):
                log.debug(f"[VRF] All previous closes are zero for {symbol}")
                return False

            returns = np.abs((curr[mask] - prev[mask]) / prev[mask])
            if returns.size == 0:
                log.debug(f"[VRF] No valid returns for {symbol}")
                return False

            # 7) Use the median return as a robust volatility estimate.
            volatility = np.median(returns)

            if self.threshold is None:
                log.error("[VRF] Volatility threshold not configured")
                return False

            allowed = volatility < self.threshold
            log.debug(
                f"[VRF] {symbol} volatility={volatility:.5f}, threshold={self.threshold} → allowed={allowed}"
            )
            return allowed

        except Exception as e:
            log.error(f"[VRF] Error processing {symbol}: {e}", exc_info=True)
            return False

    async def _calculate_since_timestamp(self, symbol: str) -> Optional[int]:
        """
        Calculate 'since' timestamp (in ms) so that Phemex has a 'from'.
        CCXT will automatically fill in 'to' = current time.
        """
        try:
            # 1) Now in milliseconds
            current_time_ms = int(time.time() * 1000)

            # 2) Convert timeframe (e.g. "1m", "5m") into seconds
            #    Assumes ApiHandler implements _timeframe_to_seconds(...)
            timeframe_seconds = self.api._timeframe_to_seconds(self.timeframe)
            timeframe_ms = timeframe_seconds * 1000

            # 3) We need (lookback + 5) candles worth of history.
            needed_ms = (self.lookback + 5) * timeframe_ms
            return current_time_ms - needed_ms

        except Exception as e:
            log.error(f"[VRF] Error calculating since timestamp for {symbol}: {e}")
            return None
