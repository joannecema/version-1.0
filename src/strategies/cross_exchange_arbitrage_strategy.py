import logging, ccxt.pro as ccxtpro

class CrossExchangeArbitrageStrategy:
    def __init__(self, api, tracker, executor, cfg):
        self.phemex = api.exchange
        self.binance = ccxtpro.binance({"enableRateLimit": True})
        self.tracker, self.exec, self.cfg = tracker, executor, cfg
        self.pairs = cfg["cross_ex_pairs"]
        self.thresh = cfg["arb_threshold_pct"]

    async def check_and_trade(self, _):
        for symbol, _ in self.pairs:
            p_tick = await self.phemex.watch_ticker(symbol)
            b_tick = await self.binance.watch_ticker(symbol)
            spread = p_tick["bid"] - b_tick["ask"]
            if spread/b_tick["ask"]>self.thresh:
                qty=(self.tracker.equity*self.cfg["risk_pct"])/b_tick["ask"]
                await self.exec.market_cross_order("binance",symbol,"buy",qty)
                await self.exec.market_cross_order("phemex", symbol,"sell",qty)
                logging.info(f"[ARB] Executed {symbol} qty={qty:.6f}")
