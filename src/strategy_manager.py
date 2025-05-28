import asyncio
import logging

from src.strategies.scalping_strategy import ScalpingStrategy
from src.strategies.pairs_trading_strategy import PairsTradingStrategy
from src.strategies.market_making_strategy import MarketMakingStrategy
from src.strategies.cross_exchange_arbitrage_strategy import CrossExchangeArbitrageStrategy

log = logging.getLogger("StrategyManager")

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
        # fallback to 10 if not set
        self.sem = asyncio.Semaphore(self.cfg.get("max_concurrent_strategies", 10))

    async def _run_strat(self, strat, sym=None):
        async with self.sem:
            try:
                await strat.check_and_trade(sym)
            except Exception as e:
                # catch-and-log so one bad strategy doesn't take down everything
                name = strat.__class__.__name__
                log.error(f"[{name}] error on {sym or 'ALL'}: {e}")

    async def run_cycle(self):
        # 1) cross-exchange arb first
        await self._run_strat(self.strategies[3], None)

        # 2) dynamic universe based on volume/spread
        tickers = await self.api.fetch_tickers()
        scores = []
        for sym, t in tickers.items():
            if not sym.endswith("/USDT"):
                continue
            # avoid divide-by-zero if ask==bid
            spread = t["ask"] - t["bid"]
            if spread <= 0:
                continue
            scores.append((sym, t["quoteVolume"] / spread))

        top = [s for s, _ in sorted(scores, key=lambda x: x[1], reverse=True)]
        top = top[: self.cfg["symbols_count"]]

        # 3) run the other three strategies in parallel slots
        tasks = []
        for strat in self.strategies[:3]:
            if isinstance(strat, PairsTradingStrategy):
                # pairs strategy decides its own symbols internally
                tasks.append(self._run_strat(strat, None))
            else:
                for s in top:
                    tasks.append(self._run_strat(strat, s))

        if tasks:
            await asyncio.gather(*tasks)
