import logging
from src.utils import calculate_sma

class ScalpingStrategy:
    def __init__(self, api, tracker, executor, cfg):
        self.api = api
        self.tracker = tracker
        self.executor = executor
        self.cfg = cfg
        self.logger = logging.getLogger("ScalpingStrategy")

        self.timeframe = cfg.get("timeframe", "1m")
        self.lookback = int(cfg.get("lookback", 50))
        self.sma_short_period = int(cfg.get("sma_short", 20))
        self.sma_long_period = int(cfg.get("sma_long", 50))

    async def check_and_trade(self, symbol: str):
        try:
            ohlcv = await self.api.get_ohlcv(symbol, self.timeframe, self.lookback + 1)
            if not ohlcv or len(ohlcv) < self.lookback + 1:
                self.logger.warning(f"[SCALP] Not enough OHLCV data for {symbol} (have {len(ohlcv)}, need {self.lookback + 1})")
                return

            closes = [bar[4] for bar in ohlcv]
            sma_short = calculate_sma(closes, self.sma_short_period)
            sma_long = calculate_sma(closes, self.sma_long_period)
            current_price = closes[-1]

            if sma_short is None or sma_long is None:
                self.logger.warning(f"[SCALP] Failed to compute SMA for {symbol} | short={sma_short}, long={sma_long}")
                return

            self.logger.debug(f"[SCALP] {symbol} price={current_price:.4f} | SMA{self.sma_short_period}={sma_short:.4f} SMA{self.sma_long_period}={sma_long:.4f}")

            # ENTRY logic
            if sma_short > sma_long and not self.tracker.has_position(symbol):
                self.logger.info(f"[SCALP] ✅ Enter LONG {symbol} @ {current_price:.4f}")
                await self.executor.enter_long(symbol, current_price)

            # EXIT logic
            elif sma_short < sma_long:
                if self.tracker.has_long(symbol):
                    self.logger.info(f"[SCALP] ❌ Exit LONG {symbol} @ {current_price:.4f}")
                    await self.executor.exit_position(symbol, current_price)
                else:
                    self.logger.debug(f"[SCALP] Skipped exit — no active long position for {symbol}")

        except Exception as e:
            self.logger.error(f"[SCALP] Error processing {symbol}: {e}")