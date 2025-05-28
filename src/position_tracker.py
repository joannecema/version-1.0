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
                writer.writerow(["symbol","side","entry_time","exit_time",
                                 "entry_price","exit_price","amount","pnl"])

    def record_entry(self, symbol, side, amount, entry_price, tp, sl):
        self.open_positions[symbol] = {
            "side": side, "amount": amount,
            "entry_price": entry_price, "tp": tp, "sl": sl,
            "timestamp": time.time()
        }

    def record_exit(self, symbol, exit_price):
        pos = self.open_positions.pop(symbol)
        pnl = ((exit_price - pos["entry_price"]) if pos["side"]=="buy"
               else (pos["entry_price"] - exit_price)) * pos["amount"]
        self.equity += pnl
        rec = {
            "symbol": symbol, "side": pos["side"],
            "entry_time": pos["timestamp"], "exit_time": time.time(),
            "entry_price": pos["entry_price"], "exit_price": exit_price,
            "amount": pos["amount"], "pnl": pnl
        }
        self.trade_history.append(rec)
        with open(self.history_file, "a", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(list(rec.values()))

    def should_exit(self, symbol, current_price):
        pos = self.open_positions[symbol]
        if (pos["side"]=="buy" and current_price >= pos["tp"]) or \
           (pos["side"]=="sell" and current_price <= pos["tp"]):
            return True
        if (pos["side"]=="buy" and current_price <= pos["sl"]) or \
           (pos["side"]=="sell" and current_price >= pos["sl"]):
            return True
        if time.time() - pos["timestamp"] > 60 and \
           abs((current_price - pos["entry_price"]) / pos["entry_price"]) < self.idle_exit_pct:
            return True
        return False
