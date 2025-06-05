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
        Determine if trading should be allowed based on volatility regime
        with rate limit handling for Phemex API
        """
        max_retries = 5
        base_delay = 1.5  # seconds
        
        for attempt in range(max_retries):
            try:
                # Fetch OHLCV data with rate limit protection
                ohlcv = await self.api.exchange.fetch_ohlcv(
                    symbol,
                    timeframe='1h',
                    limit=self.lookback_period
                )
                
                # Calculate volatility if data retrieval successful
                if len(ohlcv) < 2:
                    logger.warning(f"Insufficient data for {symbol}")
                    return False
                
                # Calculate price changes
                price_changes = []
                for i in range(1, len(ohlcv)):
                    prev_close = ohlcv[i-1][4]  # previous close
                    current_range = ohlcv[i][2] - ohlcv[i][3]  # current high-low
                    if prev_close > 0:
                        price_changes.append(current_range / prev_close)
                
                if not price_changes:
                    return False
                
                # Calculate average volatility
                avg_volatility = sum(price_changes) / len(price_changes)
                logger.info(f"{symbol} volatility: {avg_volatility:.4f}")
                
                # Determine trading allowance
                return avg_volatility >= self.threshold
                
            except RateLimitExceeded:
                if attempt < max_retries - 1:
                    # Exponential backoff with jitter
                    delay = base_delay * (2 ** attempt) + (0.1 * attempt)
                    logger.warning(
                        f"Rate limit exceeded for {symbol}. "
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
                logger.error(f"Error processing {symbol}: {str(e)}", exc_info=True)
                return False
