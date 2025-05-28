import logging

class MarketMakingStrategy:
    def __init__(self, api, tracker, executor, cfg):
        self.api = api
        self.tracker = tracker
        self.exec = executor
        self.cfg = cfg
        self.logger = logging.getLogger("MarketMaking")

    async def check_and_trade(self, symbol):
        try:
            book = await self.api.fetch_order_book(symbol, 5)
            bids = book.get("bids", [])
            asks = book.get("asks", [])

            if not bids or not asks:
                self.logger.warning(f"[MM] No bid/ask data for {symbol}")
                return

            bid = bids[0][0]
            ask = asks[0][0]

            spread_pct = self.cfg.get("market_make_spread_pct", 0.0002)
            target = bid * spread_pct
            size = self.cfg.get("market_make_size_usdt", 5) / bid

            if symbol not in self.tracker.open_positions:
                await self.exec.enter(symbol, "buy", size, bid + target, bid - target)
                await self.exec.enter(symbol, "sell", size, ask - target, ask + target)
                self.logger.info(f"[MM] Market making orders placed for {symbol} @ spread {spread_pct:.5f}")
        except Exception as e:
            self.logger.error(f"[MM] Error in {symbol}: {e}")