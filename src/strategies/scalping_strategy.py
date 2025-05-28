import logging
from src.utils import get_sma, calculate_atr, orderbook_imbalance

class ScalpingStrategy:
    def __init__(self, api, tracker, executor, cfg):
        self.api, self.tracker, self.exec, self.cfg = api, tracker, executor, cfg

    async def check_and_trade(self, symbol):
        ohlcv = await self.api.watch_ohlcv(symbol, self.cfg["timeframe"], self.cfg["lookback"]+1)
        closes = [c[4] for c in ohlcv]
        if get_sma(closes[-self.cfg["sma_short"]:]) <= get_sma(closes[-self.cfg["sma_long"]:]):
            return
        vols = [c[5] for c in ohlcv[:-1]]
        if ohlcv[-1][5] <= get_sma(vols)*self.cfg["volume_multiplier"]:
            return
        book = await self.api.fetch_order_book(symbol, self.cfg["imbalance_levels"])
        if orderbook_imbalance(book, self.cfg["imbalance_levels"]) < self.cfg["imbalance_threshold"]:
            return
        atr = calculate_atr(ohlcv, self.cfg["atr_period"])
        entry = closes[-1]
        tp = entry + atr*self.cfg["tp_atr_mult"]
        sl = entry - atr*self.cfg["sl_atr_mult"]
        risk = self.tracker.equity*self.cfg["risk_pct"]
        qty = risk/(entry-sl) if (entry-sl)>0 else 0
        if symbol not in self.tracker.open_positions and qty>0:
            await self.exec.enter(symbol, "buy", qty, tp, sl)
        for sym in list(self.tracker.open_positions):
            price = (await self.api.watch_ticker(sym))["last"]
            if self.tracker.should_exit(sym, price):
                logging.info(f"[STRAT] Auto-exit {sym}")
                await self.exec.exit(sym, price)
