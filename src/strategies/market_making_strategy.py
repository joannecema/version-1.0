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
            lookback = int(self.config.get("mm_lookback", 10))
            ohlcv = await self.api.get_ohlcv(symbol, timeframe="1m", limit=lookback + 1)

            if not ohlcv or len(ohlcv) <= lookback:
                self.logger.warning(f"[MM] Not enough OHLCV data for {symbol} (have {len(ohlcv)}, need {lookback + 1})")
                return

            prices = [c[4] for c in ohlcv[-lookback:] if c and isinstance(c[4], (int, float))]
            if not prices or len(prices) < lookback:
                self.logger.warning(f"[MM] Invalid OHLCV close prices for {symbol}: {prices}")
                return

            average_price = sum(prices) / len(prices)

            ticker = await self.api.get_ticker(symbol)
            if not ticker:
                self.logger.warning(f"[MM] No ticker data for {symbol}")
                return

            bid = ticker.get("bid")
            ask = ticker.get("ask")
            if bid is None or ask is None or bid <= 0 or ask <= 0:
                self.logger.warning(f"[MM] Invalid bid/ask for {symbol}: bid={bid}, ask={ask}")
                return

            mid_price = (bid + ask) / 2
            spread = ask - bid
            threshold = self.config.get("mm_deviation_threshold", 0.002)

            self.logger.debug(f"[MM] {symbol} avg={average_price:.4f} mid={mid_price:.4f} spread={spread:.5f}")

            if mid_price < average_price * (1 - threshold):
                qty = self.api.calculate_order_size(symbol, bid)
                if qty > 0:
                    self.logger.info(f"[MM] BUY {symbol} qty={qty:.6f} @ {bid}")
                    await self.executor.execute_market_order(symbol, "buy", qty)
                else:
                    self.logger.warning(f"[MM] Skipped BUY — qty too low for {symbol}")

            elif mid_price > average_price * (1 + threshold):
                qty = self.api.calculate_order_size(symbol, ask)
                if qty > 0:
                    self.logger.info(f"[MM] SELL {symbol} qty={qty:.6f} @ {ask}")
                    await self.executor.execute_market_order(symbol, "sell", qty)
                else:
                    self.logger.warning(f"[MM] Skipped SELL — qty too low for {symbol}")

        except Exception as e:
            self.logger.error(f"[MM] Error during check_and_trade for {symbol}: {e}")