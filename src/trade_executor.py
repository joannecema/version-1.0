# trade_executor.py (Async Version)
import asyncio
import logging
import time
import hashlib
from collections import deque
from abc import ABC, abstractmethod
from typing import Dict, Optional, Tuple

logger = logging.getLogger("TradeExecutor")

class OrderState:
    PENDING = 1
    PARTIALLY_FILLED = 2
    FILLED = 3
    CANCELLED = 4
    REJECTED = 5

class Order:
    def __init__(self, symbol, side, order_type, quantity, price=None, strategy_id=""):
        self.order_id = self._generate_id()
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
        
    def _generate_id(self):
        unique_str = f"{time.time_ns()}-{random.randint(0, 1000000)}"
        return hashlib.sha256(unique_str.encode()).hexdigest()[:20]
        
    def update_fill(self, fill_qty, fill_price):
        self.filled_quantity += fill_qty
        self.last_update = time.time()
        if abs(self.filled_quantity - self.quantity) < 1e-6:
            self.state = OrderState.FILLED
        else:
            self.state = OrderState.PARTIALLY_FILLED

class AsyncTradeExecutor:
    def __init__(self, api, config):
        self.api = api
        self.config = config
        self.logger = logging.getLogger("AsyncTradeExecutor")
        self.order_queue = asyncio.Queue()
        self.active_orders = {}
        self._stop_event = asyncio.Event()
        self._processor_task = None
        self._monitor_task = None
        
    async def start(self):
        """Start order processing and monitoring tasks"""
        self._processor_task = asyncio.create_task(self._process_orders())
        self._monitor_task = asyncio.create_task(self._monitor_orders())
        
    async def stop(self):
        """Gracefully shutdown executor"""
        self._stop_event.set()
        await asyncio.gather(
            self._processor_task,
            self._monitor_task,
            return_exceptions=True
        )
        
    async def execute_order(self, symbol, side, quantity, price=None, 
                          price_validation=True, strategy_id=""):
        """Submit and execute an order asynchronously"""
        order = Order(
            symbol=symbol,
            side=side,
            order_type="limit" if price else "market",
            quantity=quantity,
            price=price,
            strategy_id=strategy_id
        )
        
        await self.order_queue.put(order)
        return await self._wait_for_order_completion(order.order_id)
        
    async def _wait_for_order_completion(self, order_id, timeout=30):
        """Wait for order to reach final state"""
        start = time.time()
        while time.time() - start < timeout:
            order = self.active_orders.get(order_id)
            if order and order.state in (OrderState.FILLED, OrderState.CANCELLED, OrderState.REJECTED):
                return {
                    "status": self._state_to_string(order.state),
                    "filled_size": order.filled_quantity,
                    "avg_price": order.price  # Simplified for example
                }
            await asyncio.sleep(0.1)
        return {"status": "timeout"}
        
    def _state_to_string(self, state):
        return {
            OrderState.FILLED: "filled",
            OrderState.PARTIALLY_FILLED: "partial",
            OrderState.CANCELLED: "cancelled",
            OrderState.REJECTED: "rejected"
        }.get(state, "unknown")
        
    async def _process_orders(self):
        """Process orders from the queue"""
        while not self._stop_event.is_set():
            try:
                order = await asyncio.wait_for(self.order_queue.get(), timeout=1.0)
                self.active_orders[order.order_id] = order
                
                if order.state == OrderState.PENDING:
                    success = await self._execute_order(order)
                    if not success:
                        await self._handle_order_failure(order)
                        
                self.order_queue.task_done()
            except asyncio.TimeoutError:
                continue
            except Exception as e:
                self.logger.error("Order processing error: %s", e, exc_info=True)
                
    async def _execute_order(self, order):
        """Execute order through API"""
        try:
            # Get Phemex price scale
            scale = await self.api.get_price_scale(order.symbol)
            price_ep = int(order.price * scale) if order.price else None
            
            # Place order
            response = await self.api.place_order(
                symbol=order.symbol,
                side=order.side,
                order_type=order.order_type,
                quantity=order.quantity,
                price_ep=price_ep,
                ioc_timeout=self.config.get("ioc_timeout_ms", 1000)
            )
            
            # Handle response
            if response["status"] == "filled":
                order.update_fill(order.quantity, response["avg_price"])
                return True
            elif response["status"] == "partial":
                order.update_fill(response["filled_qty"], response["avg_price"])
                # Submit remaining quantity as new order
                remaining = order.quantity - order.filled_quantity
                if remaining > 0:
                    new_order = Order(
                        symbol=order.symbol,
                        side=order.side,
                        order_type=order.order_type,
                        quantity=remaining,
                        price=order.price,
                        strategy_id=order.strategy_id
                    )
                    await self.order_queue.put(new_order)
                return True
            else:
                order.state = OrderState.REJECTED
                return False
                
        except Exception as e:
            self.logger.error("Order execution failed: %s", e, exc_info=True)
            return False
            
    async def _monitor_orders(self):
        """Monitor active orders for timeouts"""
        while not self._stop_event.is_set():
            await asyncio.sleep(5)
            current_time = time.time()
            
            for order_id, order in list(self.active_orders.items()):
                # Handle pending orders timeout
                if order.state == OrderState.PENDING and current_time - order.timestamp > 30:
                    self.logger.warning("Order timeout: %s", order_id)
                    order.state = OrderState.CANCELLED
                    await self._cancel_order(order)
                    
                # Handle partial fills
                if (order.state == OrderState.PARTIALLY_FILLED and 
                    current_time - order.last_update > 60):
                    self.logger.info("Refreshing partial order: %s", order_id)
                    await self._cancel_order(order)
                    # Resubmit remaining quantity
                    remaining = order.quantity - order.filled_quantity
                    if remaining > 0:
                        new_order = Order(
                            symbol=order.symbol,
                            side=order.side,
                            order_type=order.order_type,
                            quantity=remaining,
                            price=order.price,
                            strategy_id=order.strategy_id
                        )
                        await self.order_queue.put(new_order)
                        
    async def _cancel_order(self, order):
        """Cancel order on exchange"""
        try:
            await self.api.cancel_order(order.symbol, order.order_id)
            order.state = OrderState.CANCELLED
        except Exception as e:
            self.logger.warning("Cancel failed: %s", e)
            
    async def _handle_order_failure(self, order):
        """Handle order failure with fallback"""
        if order.order_type != "market":
            self.logger.info("Falling back to market order for %s", order.order_id)
            market_order = Order(
                symbol=order.symbol,
                side=order.side,
                order_type="market",
                quantity=order.quantity,
                strategy_id=order.strategy_id
            )
            await self.order_queue.put(market_order)
