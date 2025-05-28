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
            CrossExchangeArbitrageStrategy(api, tracker, executor, cfg),
        ]

        self.sem = asyncio.Semaphore(self.cfg.get("max_concurrent_strategies", 10))
        self.symbols = self.cfg.get("symbols", [])

    async def _run_strat(self, strat, sym=None):
        async with self.sem:
            try:
                strategy_name = strat.__class__.__name__
                log.info(f"[{strategy_name}] Running for: {sym or 'ALL'}")
                await strat.check_and_trade(sym)
            except Exception as e:
                log.error(f"[{strat.__class__.__name__}] Error on {sym or 'ALL'}: {e}")

    async def run_cycle(self):
        # 1) Run Cross Exchange Arbitrage strategy first (usually symbol-agnostic)
        try:
            await self._run_strat(self.strategies[3], None)
        except Exception as e:
            log.warning(f"[CrossExchangeArbitrageStrategy] Failed: {e}")

        # 2) Fetch tickers individually (Phemex doesn't support fetch_tickers())
        try:
            tickers = await self.api.fetch_tickers(self.symbols)
        except Exception as e:
            log.warning(f"[StrategyManager] Failed to fetch tickers: {e}")
            tickers = {}

        scores = []
        for sym, t in tickers.items():
            if not sym.endswith("/USDT"):
                continue
            try:
                ask = t.get("ask")
                bid = t.get("bid")
                volume = t.get("quoteVolume", 0)

                if ask is None or bid is None or ask <= bid:
                    continue

                spread = ask - bid
                if spread > 0 and volume > 0:
                    score = volume / spread
                    scores.append((sym, score))
            except Exception as e:
                log.warning(f"[StrategyManager] Skipping {sym} due to error: {e}")
                continue

        top = [s for s, _ in sorted(scores, key=lambda x: x[1], reverse=True)]
        top = top[:self.cfg.get("symbols_count", 10)]

        # 3) Run scalping, market-making, and pairs-trading strategies
        tasks = []
        for strat in self.strategies[:3]:
            try:
                if isinstance(strat, PairsTradingStrategy):
                    tasks.append(self._run_strat(strat, None))  # Global check for pairs
                else:
                    for s in top:
                        tasks.append(self._run_strat(strat, s))
            except Exception as e:
                log.error(f"[{strat.__class__.__name__}] Strategy scheduling failed: {e}")

        if tasks:
            await asyncio.gather(*tasks)