import logging
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
        Evaluate if trading is permitted based on volatility
        with robust error handling and performance optimizations
        """
        try:
            # Calculate since parameter to get exactly needed candles
            since = await self._calculate_since_timestamp(symbol)
            
            # Fetch OHLCV data with calculated since parameter
            ohlcv = await self.api.get_ohlcv(
                symbol, 
                self.timeframe, 
                limit=self.lookback + 5,  # Extra buffer for data gaps
                since=since
            )
            
            if not ohlcv:
                log.debug(f"[VRF] No OHLCV data for {symbol}")
                return False
                
            # Validate data structure
            if len(ohlcv) < self.min_data_points:
                log.debug(f"[VRF] Insufficient data for {symbol} (only {len(ohlcv)} candles)")
                return False
                
            # Extract and validate closing prices
            closes = []
            for candle in ohlcv:
                if len(candle) >= 5:  # Ensure candle has close price
                    close_price = candle[4]
                    if isinstance(close_price, (int, float)) and close_price > 0:
                        closes.append(close_price)
            
            if len(closes) < 2:
                log.debug(f"[VRF] Not enough valid closes for {symbol}")
                return False

            # Calculate returns using vectorized operations
            closes_array = np.array(closes)
            prev_closes = closes_array[:-1]
            current_closes = closes_array[1:]
            
            # Avoid division by zero
            valid_mask = prev_closes > 0
            if not np.any(valid_mask):
                log.debug(f"[VRF] All previous closes are zero for {symbol}")
                return False
                
            returns = np.abs((current_closes[valid_mask] - prev_closes[valid_mask]) / 
                             prev_closes[valid_mask])
            
            if len(returns) == 0:
                log.debug(f"[VRF] No valid returns for {symbol}")
                return False
                
            # Calculate volatility as median to reduce outlier impact
            volatility = np.median(returns)
            
            if self.threshold is None:
                log.error("[VRF] Volatility threshold not configured")
                return False

            allowed = volatility < self.threshold
            log.debug(f"[VRF] {symbol} volatility={volatility:.5f}, threshold={self.threshold} → allowed={allowed}")
            return allowed
            
        except Exception as e:
            log.error(f"[VRF] Error processing {symbol}: {e}", exc_info=True)
            return False
            
    async def _calculate_since_timestamp(self, symbol: str) -> Optional[int]:
        """Calculate since timestamp for efficient data fetching"""
        try:
            # Get current time in milliseconds
            current_time_ms = int(time.time() * 1000)
            
            # Calculate timeframe in milliseconds
            timeframe_seconds = self.api._timeframe_to_seconds(self.timeframe)
            timeframe_ms = timeframe_seconds * 1000
            
            # Calculate needed timeframe
            needed_timeframe = (self.lookback + 5) * timeframe_ms
            
            # Return start timestamp
            return current_time_ms - needed_timeframe
        except Exception as e:
            log.error(f"[VRF] Error calculating since timestamp for {symbol}: {e}")
            return None
