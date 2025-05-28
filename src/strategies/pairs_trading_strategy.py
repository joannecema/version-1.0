import logging, numpy as np
from src.utils import get_sma

class PairsTradingStrategy:
    def __init__(self, api, tracker, executor, cfg):
        self.api, self.tracker, self.exec, self.cfg = api, tracker, executor, cfg
        self.pairs = cfg["pairs"]
        self.z_threshold = 1.0

    async def check_and_trade(self, _):
        for base, alt in self.pairs:
            ohlcv_b = await self.api.watch_ohlcv(base, self.cfg["timeframe"], self.cfg["lookback"])
            ohlcv_a = await self.api.watch_ohlcv(alt,  self.cfg["timeframe"], self.cfg["lookback"])
            mids_b = [(c[2]+c[3])/2 for c in ohlcv_b]
            mids_a = [(c[2]+c[3])/2 for c in ohlcv_a]
            ratio = np.polyfit(mids_a, mids_b, 1)[0]
            spread = [b - ratio*a for b,a in zip(mids_b, mids_a)]
            mu, sd = np.mean(spread), np.std(spread)
            z = (spread[-1]-mu)/sd if sd>0 else 0
            if abs(z)>self.z_threshold and base not in self.tracker.open_positions:
                side="buy" if z<0 else "sell"
                amt=(self.tracker.equity*self.cfg["risk_pct"])/mids_b[-1]
                tp=mids_b[-1]+(mu-spread[-1])
                sl=mids_b[-1]-(mu-spread[-1])
                await self.exec.enter(base,side,amt,tp,sl)
            if base in self.tracker.open_positions and abs(z)<0.2:
                price=(await self.api.watch_ticker(base))["last"]
                await self.exec.exit(base,price)
