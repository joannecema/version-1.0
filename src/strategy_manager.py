class StrategyManager:
    def __init__(self, api, tracker, executor, cfg):
        self.api, self.tracker, self.exec, self.cfg = api, tracker, executor, cfg
        self.semaphore = asyncio.Semaphore(10)
        # map each strategy to whether it needs symbols or not
        self.strategies = [
            (CrossExchangeArbitrageStrategy(api, tracker, executor, cfg), False),
            (ScalpingStrategy(api, tracker, executor, cfg), True),
            (MarketMakingStrategy(api, tracker, executor, cfg), True),
            (PairsTradingStrategy(api, tracker, executor, cfg), False),
        ]

    async def _run_strat(self, strat, symbol=None):
        async with self.semaphore:
            await strat.check_and_trade(symbol)

    async def run_cycle(self):
        # build universe once
        tickers = await self.api.fetch_tickers()
        top = sorted(
            (s for s in tickers if s.endswith("/USDT")),
            key=lambda s: tickers[s]["quoteVolume"], reverse=True
        )[: self.cfg["symbols_count"]]

        # schedule everything in one pass
        tasks = []
        for strat, needs_symbol in self.strategies:
            if needs_symbol:
                for sym in top:
                    tasks.append(self._run_strat(strat, sym))
            else:
                tasks.append(self._run_strat(strat, None))

        await asyncio.gather(*tasks)
