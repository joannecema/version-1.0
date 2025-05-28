import logging

class MarketMakingStrategy:
    def __init__(self, api, tracker, executor, cfg):
        self.api, self.tracker, self.exec, self.cfg = api, tracker, executor, cfg

    async def check_and_trade(self, symbol):
        book = await self.api.fetch_order_book(symbol, 5)
        bid, ask = book["bids"][0][0], book["asks"][0][0]
        target = bid*self.cfg["market_make_spread_pct"]
        size = self.cfg["market_make_size_usdt"]/bid
        if symbol not in self.tracker.open_positions:
            await self.exec.enter(symbol,"buy",size,bid+target,bid-target)
            await self.exec.enter(symbol,"sell",size,ask-target,ask+target)
