# src/strategy_grid.py

import logging

log = logging.getLogger("GridStrategy")

class GridStrategy:
    def __init__(self, api, config, tracker, executor):
        self.api = api
        self.config = config
        self.tracker = tracker
        self.executor = executor

        self.timeframe = config.get("timeframe", "1m")
        self.lookback = int(config.get("mm_lookback", 10))
        self.threshold = float(config.get("mm_deviation_threshold", 0.002))

    async def check_signal(self, symbol: str):
        try:
            ohlcv = await self.api.get_ohlcv(symbol, timeframe=self.timeframe, limit=self.lookback + 1)
            if not ohlcv or len(ohlcv) <= self.lookback:
                log.warning(f"[GRID] Not enough OHLCV for {symbol} (have {len(ohlcv)})")
                return None

            closes = [c[4] for c in ohlcv[-self.lookback:] if c and isinstance(c[4], (float, int))]
            if len(closes) < self.lookback:
                log.warning(f"[GRID] Invalid close prices for {symbol}: {closes}")
                return None

            avg_price = sum(closes) / len(closes)
            ticker = await self.api.get_ticker(symbol)
            if not ticker:
                log.warning(f"[GRID] No ticker for {symbol}")
                return None

            bid = ticker.get("bid")
            ask = ticker.get("ask")
            if bid is None or ask is None or bid <= 0 or ask <= 0:
                log.warning(f"[GRID] Invalid bid/ask for {symbol}: bid={bid}, ask={ask}")
                return None

            mid_price = (bid + ask) / 2
            spread = ask - bid
            log.debug(f"[GRID] {symbol} avg={avg_price:.4f} mid={mid_price:.4f} spread={spread:.5f}")

            capital = await self.tracker.get_available_usdt()
            size = self.executor._calculate_trade_size(capital, mid_price)

            if mid_price < avg_price * (1 - self.threshold):
                log.info(f"[GRID] BUY signal on {symbol} @ {mid_price:.4f}")
                return "buy", size
            elif mid_price > avg_price * (1 + self.threshold):
                log.info(f"[GRID] SELL signal on {symbol} @ {mid_price:.4f}")
                return "sell", size
            else:
                log.debug(f"[GRID] No action on {symbol}, within threshold")
                return None

        except Exception as e:
            log.error(f"[GRID] ❌ Error on {symbol}: {e}")
            return None
