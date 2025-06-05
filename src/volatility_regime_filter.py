import logging
import asyncio
from ccxt import RateLimitExceeded

logger = logging.getLogger(__name__)

class VolatilityRegimeFilter:
    def __init__(self, api, lookback_period=24, threshold=0.05):
        self.api = api
        self.lookback_period = lookback_period
        self.threshold = threshold
        
    async def allow_trading(self, symbol):
        """
        Determine if trading should be allowed based on volatility regime,
        using the ApiHandler’s rate-limited get_ohlcv method.
        """
        max_retries = 5
        base_delay = 1.5  # seconds
        
        for attempt in range(max_retries):
            try:
                # Use ApiHandler.get_ohlcv (which includes its own semaphore and retry)
                ohlcv = await self.api.get_ohlcv(
                    symbol,
                    timeframe='1h',
                    limit=self.lookback_period
                )
                
                if not ohlcv or len(ohlcv) < 2:
                    logger.warning(f"Insufficient data for {symbol}")
                    return False
                
                # Calculate volatility based on high-low relative to prior close
                price_changes = []
                for i in range(1, len(ohlcv)):
                    prev_close = ohlcv[i-1][4]
                    high = ohlcv[i][2]
                    low = ohlcv[i][3]
                    if prev_close > 0:
                        price_changes.append((high - low) / prev_close)
                
                if not price_changes:
                    return False
                
                avg_volatility = sum(price_changes) / len(price_changes)
                logger.info(f"{symbol} volatility: {avg_volatility:.4f}")
                
                return avg_volatility >= self.threshold
                
            except RateLimitExceeded:
                if attempt < max_retries - 1:
                    # Exponential backoff with a small jitter
                    delay = base_delay * (2 ** attempt) + (0.1 * attempt)
                    logger.warning(
                        f"Rate limit hit for {symbol}. "
                        f"Retry {attempt+1}/{max_retries} in {delay:.1f}s"
                    )
                    await asyncio.sleep(delay)
                    continue
                else:
                    logger.error(
                        f"Rate limit exceeded for {symbol} after {max_retries} attempts"
                    )
                    return False
                
            except Exception as e:
                logger.error(f"Error processing {symbol}: {e}", exc_info=True)
                return False
