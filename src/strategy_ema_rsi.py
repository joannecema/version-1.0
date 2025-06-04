import logging
from typing import Optional, Tuple

log = logging.getLogger("EMARSI")

class EmaRsiStrategy:
    def __init__(self, api, config, tracker, executor):
        self.api = api
        self.config = config
        self.tracker = tracker
        self.executor = executor

        self.timeframe = config.get("timeframe", "1m")
        self.lookback = config.get("lookback", 50)
        self.ema_short_period = config.get("sma_short", 12)
        self.ema_long_period = config.get("sma_long", 26)
        self.rsi_period = config.get("rsi_period", 14)
        self.rsi_oversold = config.get("rsi_oversold", 30)
        self.rsi_overbought = config.get("rsi_overbought", 70)

    async def check_signal(self, symbol: str) -> Optional[Tuple[str, float]]:
        try:
            ohlcv = await self.api.fetch_ohlcv(symbol, self.timeframe, limit=self.lookback + 1)
        except Exception as e:
            log.error(f"[EMA-RSI] ❌ Failed to fetch OHLCV for {symbol}: {e}")
            return None

        if not ohlcv or len(ohlcv) < self.lookback:
            log.warning(f"[EMA-RSI] ⚠️ Not enough OHLCV data for {symbol}")
            return None

        closes = [bar[4] for bar in ohlcv if isinstance(bar[4], (float, int))]
        if len(closes) < max(self.ema_long_period, self.rsi_period) + 1:
            log.warning(f"[EMA-RSI] ⚠️ Not enough close data for {symbol}")
            return None

        def ema(data, period):
            k = 2 / (period + 1)
            ema_val = data[0]
            for price in data[1:]:
                ema_val = price * k + ema_val * (1 - k)
            return ema_val

        ema_short = ema(closes[-self.ema_short_period:], self.ema_short_period)
        ema_long = ema(closes[-self.ema_long_period:], self.ema_long_period)

        # RSI calculation
        gains, losses = [], []
        for i in range(1, self.rsi_period + 1):
            diff = closes[-i] - closes[-i - 1]
            if diff >= 0:
                gains.append(diff)
            else:
                losses.append(abs(diff))

        avg_gain = sum(gains) / self.rsi_period if gains else 0.0001
        avg_loss = sum(losses) / self.rsi_period if losses else 0.0001
        rs = avg_gain / avg_loss
        rsi = 100 - (100 / (1 + rs))

        log.debug(f"[EMA-RSI] {symbol} EMA_S={ema_short:.4f}, EMA_L={ema_long:.4f}, RSI={rsi:.2f}")

        try:
            capital = await self.tracker.get_available_usdt()
            price = closes[-1]
            size = self.executor._calculate_trade_size(capital, price)
        except Exception as e:
            log.error(f"[EMA-RSI] ❌ Trade sizing failed for {symbol}: {e}")
            return None

        if ema_short > ema_long and rsi < self.rsi_oversold:
            log.info(f"[EMA-RSI] ✅ BUY signal {symbol} | EMA↑ & RSI={rsi:.2f}")
            return "buy", size
        elif ema_short < ema_long and rsi > self.rsi_overbought:
            log.info(f"[EMA-RSI] ✅ SELL signal {symbol} | EMA↓ & RSI={rsi:.2f}")
            return "sell", size

        log.debug(f"[EMA-RSI] ❌ No signal on {symbol}")
        return None
