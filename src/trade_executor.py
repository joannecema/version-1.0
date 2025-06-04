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
        
    def submit_order(self, order: Order):
        with self.lock:
            if order.order_id in self.active_orders:
                self.logger.warning(f"Duplicate order ID: {order.order_id}")
                return False
                
            self.active_orders[order.order_id] = order
            self.order_queue.put((order.timestamp, order))
            self.logger.info(f"Order submitted: {order.order_id} {order.symbol} {order.side} {order.quantity}")
            return True

    @exponential_backoff(retries=3, delay=1, max_delay=10)
    def execute_order(self, order: Order) -> bool:
        try:
            # Apply slippage protection
            if order.order_type == "market":
                order_price = self.api.get_reference_price(order.symbol)
            else:
                order_price = order.price * (1 + self.config['limit_offset_pct'] 
                             * (1 if order.side == "buy" else -1))
            
            # Execute with IOC protection
            response = self.api.place_order(
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                quantity=order.quantity,
                price=order_price,
                ioc_timeout=self.config['ioc_timeout_ms']
            )
            
            if response['status'] == 'filled':
                order.update_fill(order.quantity, response['avg_price'])
                return True
            elif response['status'] == 'partial':
                order.update_fill(response['filled_qty'], response['avg_price'])
                # Create remainder order
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
                self.logger.error(f"Order rejected: {response['error']}")
                return False
                
        except Exception as e:
            self.logger.exception(f"Order execution failed: {str(e)}")
            return False

    def process_orders(self):
        while True:
            try:
                _, order = self.order_queue.get(timeout=1)
                if order.state == OrderState.PENDING:
                    success = self.execute_order(order)
                    if not success:
                        # Implement smart order routing fallback
                        self.handle_order_failure(order)
            except Exception as e:
                self.logger.error(f"Order processing error: {str(e)}")

    def monitor_orders(self):
        while True:
            time.sleep(5)
            with self.lock:
                for order_id, order in list(self.active_orders.items()):
                    # Timeout check for pending orders
                    if order.state == OrderState.PENDING and time.time() - order.timestamp > 30:
                        self.logger.warning(f"Order timeout: {order_id}")
                        order.state = OrderState.CANCELLED
                        self.cancel_order(order_id)
                        
                    # Stale partial fill handling
                    elif order.state == OrderState.PARTIALLY_FILLED and time.time() - order.last_update > 60:
                        self.logger.info(f"Refreshing partial order: {order_id}")
                        self.api.cancel_order(order_id)
                        # Resubmit remainder
                        remaining_qty = order.quantity - order.filled_quantity
                        new_order = Order(
                            symbol=order.symbol,
                            side=order.side,
                            order_type=order.order_type,
                            quantity=remaining_qty,
                            price=order.price
                        )
                        self.submit_order(new_order)

    def cancel_order(self, order_id: str):
        with self.lock:
            if order_id in self.active_orders:
                self.api.cancel_order(order_id)
                self.active_orders[order_id].state = OrderState.CANCELLED
                return True
        return False

    def handle_order_failure(self, order: Order):
        # Implement fallback logic:
        # 1. Try different order type
        # 2. Route to alternative exchange
        # 3. Execute as multiple smaller orders
        self.logger.warning(f"Implementing fallback for failed order {order.order_id}")
        
        # Simple retry with market order
        if order.order_type != "market":
            market_order = Order(
                symbol=order.symbol,
                side=order.side,
                order_type="market",
                quantity=order.quantity
            )
            self.submit_order(market_order)
            
    def get_order_state(self, order_id: str) -> Tuple[int, float]:
        with self.lock:
            if order_id in self.active_orders:
                order = self.active_orders[order_id]
                return order.state, order.filled_quantity
        return OrderState.CANCELLED, 0.0
