import time, csv, os

class PositionTracker:
    def __init__(self, cfg):
        self.open_positions = {}
        self.equity = 900.0
        self.idle_exit_pct = cfg["idle_exit_pct"]
        self.history_file = cfg["trade_history_file"]
        self.trade_history = []

        if not os.path.isfile(self.history_file):
            with open(self.history_file, "w", newline="") as f:
                writer = csv.writer(f)
                writer.writerow([
                    "symbol", "side", "entry_time", "exit_time",
                    "entry_price", "exit_price", "amount", "pnl"
                ])

    def has_position(self, symbol: str) -> bool:
        """
        Returns True if a position is currently open for the given symbol.
        """
        return symbol in self.open_positions and self.open_positions[symbol].get("is_open", False)

    def record_entry(self, symbol, side, amount, entry_price, tp, sl):
        self.open_positions[symbol] = {
            "side": side,
            "amount": amount,
            "entry_price": entry_price,
            "tp": tp,
            "sl": sl,
            "timestamp": time.time(),
            "is_open": True
        }

    def record_exit(self, symbol, exit_price):
        if symbol not in self.open_positions:
            return  # safety check

        pos = self.open_positions.pop(symbol)
        pos["is_open"] = False

        pnl = ((exit_price - pos["entry_price"]) if pos["side"] == "buy"
               else (pos["entry_price"] - exit_price)) * pos["amount"]
        self.equity += pnl

        rec = {
            "symbol": symbol,
            "side": pos["side"],
            "entry_time": pos["timestamp"],
            "exit_time": time.time(),
            "entry_price": pos["entry_price"],
            "exit_price": exit_price,
            "amount": pos["amount"],
            "pnl": pnl
        }

        self.trade_history.append(rec)
        with open(self.history_file, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(list(rec.values()))

    def should_exit(self, symbol, current_price):
        if symbol not in self.open_positions:
            return False

        pos = self.open_positions[symbol]
        side = pos["side"]

        if (side == "buy" and current_price >= pos["tp"]) or \
           (side == "sell" and current_price <= pos["tp"]):
            return True

        if (side == "buy" and current_price <= pos["sl"]) or \
           (side == "sell" and current_price >= pos["sl"]):
            return True

        if time.time() - pos["timestamp"] > 60 and \
           abs((current_price - pos["entry_price"]) / pos["entry_price"]) < self.idle_exit_pct:
            return True

        return False