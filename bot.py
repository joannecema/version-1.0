import asyncio
from src.strategies.scalping_strategy import ScalpingStrategy
from src.strategies.pairs_trading_strategy import PairsTradingStrategy
from src.strategies.market_making_strategy import MarketMakingStrategy
from src.strategies.cross_exchange_arbitrage_strategy import CrossExchangeArbitrageStrategy

class StrategyManager:
    def __init__(self, api, tracker, executor, cfg):
        self.api = api
        self.tracker = tracker
        self.exec = executor
        self.cfg = cfg
        self.strategies = [
            ScalpingStrategy(api, tracker, executor, cfg),
            PairsTradingStrategy(api, tracker, executor, cfg),
            MarketMakingStrategy(api, tracker, executor, cfg),
            CrossExchangeArbitrageStrategy(api, tracker, executor, cfg)
        ]
        self.sem = asyncio.Semaphore(self.cfg.get("max_concurrent_strategies", 10))

    async def _run_strat(self, strat, sym=None):
        async with self.sem:
            await strat.check_and_trade(sym)

    async def run_cycle(self):
        # cross-exchange arb first
        await self._run_strat(self.strategies[3], None)

        # dynamic universe based on volume/spread
        tickers = await self.api.fetch_tickers()
        scores = [
            (sym, t["quoteVolume"] / (t["ask"] - t["bid"] + 1e-8))
            for sym, t in tickers.items() if sym.endswith("/USDT")
        ]
        top = [s for s,_ in sorted(scores, key=lambda x: x[1], reverse=True)][: self.cfg["symbols_count"]]

        tasks = []
        for strat in self.strategies[:3]:
            if isinstance(strat, PairsTradingStrategy):
                tasks.append(self._run_strat(strat, None))
            else:
                for s in top:
                    tasks.append(self._run_strat(strat, s))
        await asyncio.gather(*tasks)
