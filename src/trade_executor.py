import time
import hashlib
import threading
import logging
from queue import PriorityQueue
from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple

from src.utils import exponential_backoff, get_logger
from src.api_handler import PhemexAPI

class OrderState:
    PENDING = 1
    PARTIALLY_FILLED = 2
    FILLED = 3
    CANCELLED = 4
    REJECTED = 5

class Order:
    def __init__(self, symbol: str, side: str, order_type: str, quantity: float,
                 price: Optional[float] = None, strategy_id: str = ""):
        self.order_id = self.generate_id(symbol, side)
        self.symbol = symbol
        self.side = side
        self.order_type = order_type
        self.quantity = quantity
        self.filled_quantity = 0.0
        self.price = price
        self.strategy_id = strategy_id
        self.state = OrderState.PENDING
        self.timestamp = time.time()
        self.last_update = time.time()

    @staticmethod
    def generate_id(symbol: str, side: str) -> str:
        base = f"{symbol}-{side}-{time.time_ns()}"
        return hashlib.sha256(base.encode()).hexdigest()[:20]

    def update_fill(self, fill_qty: float, fill_price: float):
        self.filled_quantity += fill_qty
        self.last_update = time.time()
        if abs(self.filled_quantity - self.quantity) < 1e-6:
            self.state = OrderState.FILLED
        else:
            self.state = OrderState.PARTIALLY_FILLED

class TradeExecutor(ABC):
    @abstractmethod
    def execute_order(self, order: Order) -> bool:
        pass

class PhemexTradeExecutor(TradeExecutor):
    def __init__(self, api: PhemexAPI, config: dict):
        self.api = api
        self.config = config
        self.logger = get_logger("trade_executor")
        self.order_queue = PriorityQueue()
        self.active_orders: Dict[str, Order] = {}
        self.lock = threading.Lock()

        self.worker_thread = threading.Thread(target=self.process_orders, daemon=True)
        self.worker_thread.start()

        self.status_monitor = threading.Thread(target=self.monitor_orders, daemon=True)
        self.status_monitor.start()

    def submit_order(self, order: Order) -> bool:
        with self.lock:
            if order.order_id in self.active_orders:
                self.logger.warning(f"[EXECUTOR] Duplicate order ID: {order.order_id}")
                return False
            self.active_orders[order.order_id] = order
            self.order_queue.put((order.timestamp, order))
            self.logger.info(f"[EXECUTOR] Submitted order {order.order_id} | {order.side} {order.quantity} {order.symbol}")
            return True

    @exponential_backoff(retries=3, delay=1, max_delay=10)
    def execute_order(self, order: Order) -> bool:
        try:
            # Determine price
            if order.order_type == "market":
                order_price = self.api.get_reference_price(order.symbol)
            else:
                order_price = order.price * (
                    1 + self.config.get('limit_offset_pct', 0.001) * (1 if order.side == "buy" else -1)
                )

            response = self.api.place_order(
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                quantity=order.quantity,
                price=order_price,
                ioc_timeout=self.config.get('ioc_timeout_ms', 1000)
            )

            if response.get('status') == 'filled':
                order.update_fill(order.quantity, response['avg_price'])
                return True

            elif response.get('status') == 'partial':
                order.update_fill(response['filled_qty'], response['avg_price'])
                remaining_qty = order.quantity - response['filled_qty']
                new_order = Order(
                    symbol=order.symbol,
                    side=order.side,
                    order_type=order.order_type,
                    quantity=remaining_qty,
                    price=order_price
                )
                self.submit_order(new_order)
                return True

            else:
                order.state = OrderState.REJECTED
                self.logger.warning(f"[EXECUTOR] Order rejected: {response.get('error', 'unknown')}")
                return False

        except Exception as e:
            self.logger.exception(f"[EXECUTOR] Order execution error: {str(e)}")
            return False

    def process_orders(self):
        while True:
            try:
                _, order = self.order_queue.get(timeout=1)
                if order.state == OrderState.PENDING:
                    success = self.execute_order(order)
                    if not success:
                        self.handle_order_failure(order)
            except Exception as e:
                self.logger.error(f"[EXECUTOR] Order processing error: {e}")

    def monitor_orders(self):
        while True:
            time.sleep(5)
            with self.lock:
                for order_id, order in list(self.active_orders.items()):
                    if order.state == OrderState.PENDING and time.time() - order.timestamp > 30:
                        self.logger.warning(f"[EXECUTOR] Order timeout: {order_id}")
                        order.state = OrderState.CANCELLED
                        self.cancel_order(order_id)
                    elif order.state == OrderState.PARTIALLY_FILLED and time.time() - order.last_update > 60:
                        self.logger.info(f"[EXECUTOR] Refreshing stale partial: {order_id}")
                        self.api.cancel_order(order_id)
                        remaining_qty = order.quantity - order.filled_quantity
                        new_order = Order(
                            symbol=order.symbol,
                            side=order.side,
                            order_type=order.order_type,
                            quantity=remaining_qty,
                            price=order.price
                        )
                        self.submit_order(new_order)

    def cancel_order(self, order_id: str) -> bool:
        with self.lock:
            if order_id in self.active_orders:
                self.api.cancel_order(order_id)
                self.active_orders[order_id].state = OrderState.CANCELLED
                return True
        return False

    def handle_order_failure(self, order: Order):
        self.logger.warning(f"[EXECUTOR] Fallback for failed order {order.order_id}")
        if order.order_type != "market":
            fallback_order = Order(
                symbol=order.symbol,
                side=order.side,
                order_type="market",
                quantity=order.quantity
            )
            self.submit_order(fallback_order)

    def get_order_state(self, order_id: str) -> Tuple[int, float]:
        with self.lock:
            order = self.active_orders.get(order_id)
            if order:
                return order.state, order.filled_quantity
        return OrderState.CANCELLED, 0.0
