import logging
import ccxt.pro as ccxtpro

log = logging.getLogger("CrossExchangeArbitrage")

class CrossExchangeArbitrageStrategy:
    def __init__(self, api, tracker, executor, cfg):
        self.api       = api
        self.phemex    = api.exchange
        self.binance   = ccxtpro.binance({"enableRateLimit": True})
        self.tracker   = tracker
        self.exec      = executor
        self.cfg       = cfg
        self.pairs     = cfg.get("cross_ex_pairs", [])
        self.thresh    = cfg.get("arb_threshold_pct", 0)

    async def check_and_trade(self, _):
        for symbol, _ in self.pairs:
            try:
                # ✅ fetch ticker with fallback from your patched ApiHandler
                p_tick = await self.api.get_ticker(symbol)
            except Exception as e:
                log.error(f"[ARB] Phemex ticker fetch failed for {symbol}: {e}")
                continue

            try:
                # ✅ Binance WebSocket (we assume it’s supported and stable here)
                b_tick = await self.binance.watch_ticker(symbol)
            except Exception as e:
                log.error(f"[ARB] Binance ticker fetch failed for {symbol}: {e}")
                continue

            # ensure valid tick data
            p_bid = p_tick.get("bid")
            b_ask = b_tick.get("ask")
            if p_bid is None or b_ask is None or b_ask <= 0:
                log.warning(f"[ARB] Invalid tick data for {symbol}: p_bid={p_bid}, b_ask={b_ask}")
                continue

            spread_pct = (p_bid - b_ask) / b_ask
            log.debug(f"[ARB] {symbol} spread_pct={spread_pct:.5f}")

            if spread_pct > self.thresh:
                try:
                    qty = (self.tracker.equity * self.cfg["risk_pct"]) / b_ask
                    await self.exec.market_cross_order("binance", symbol, "buy", qty)
                    await self.exec.market_cross_order("phemex",  symbol, "sell", qty)
                    log.info(f"[ARB] ✅ Executed {symbol} qty={qty:.6f} spread={spread_pct:.4f}")
                except Exception as e:
                    log.error(f"[ARB] ❌ Order execution failed for {symbol}: {e}")