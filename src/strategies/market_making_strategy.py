import logging

class MarketMakingStrategy:
    def __init__(self, api, tracker, executor, config):
        self.api = api
        self.tracker = tracker
        self.executor = executor
        self.config = config
        self.logger = logging.getLogger("MarketMakingStrategy")

    async def check_and_trade(self, symbol: str):
        try:
            # Ensure we use integers for OHLCV limits
            lookback = int(self.config.get("mm_lookback", 10))
            ohlcv = await self.api.fetch_ohlcv(symbol, timeframe="1m", limit=lookback + 1)

            if not ohlcv or len(ohlcv) <= lookback:
                self.logger.warning(f"[MM] Not enough OHLCV data for {symbol} (have {len(ohlcv)}, need {lookback + 1})")
                return

            # Example market-making logic (mid-price mean reversion):
            prices = [c[4] for c in ohlcv[-lookback:]]  # Close prices
            average_price = sum(prices) / lookback

            ticker = await self.api.fetch_ticker(symbol)
            bid = ticker.get("bid")
            ask = ticker.get("ask")
            if not bid or not ask:
                self.logger.warning(f"[MM] Missing bid/ask for {symbol}")
                return

            mid_price = (bid + ask) / 2
            spread = ask - bid

            # Decision: if price significantly deviates from mean, enter position
            threshold = self.config.get("mm_deviation_threshold", 0.002)
            if mid_price < average_price * (1 - threshold):
                qty = self.api.calculate_order_size(symbol, bid)
                self.logger.info(f"[EXEC] ENTRY BUY {symbol} qty={qty}")
                await self.executor.execute_market_order(symbol, "buy", qty)

            elif mid_price > average_price * (1 + threshold):
                qty = self.api.calculate_order_size(symbol, ask)
                self.logger.info(f"[EXEC] ENTRY SELL {symbol} qty={qty}")
                await self.executor.execute_market_order(symbol, "sell", qty)

        except Exception as e:
            self.logger.error(f"[MM] Error in {symbol}: {e}")