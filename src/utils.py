# utils.py
import asyncio
import logging
import numpy as np
from typing import Callable, Any
from functools import wraps

log = logging.getLogger("Utils")

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = logging.StreamHandler()
        formatter = logging.Formatter('[%(asctime)s] %(levelname)s [%(name)s] %(message)s')
        handler.setFormatter(formatter)
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
    return logger

def exponential_backoff(retries: int = 3, delay: float = 1.0, max_delay: float = 10.0):
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            attempt = 0
            wait = delay
            while attempt < retries:
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    attempt += 1
                    log.warning(f"[BACKOFF] {func.__name__} failed (attempt {attempt}/{retries}): {e}")
                    if attempt >= retries:
                        raise
                    await asyncio.sleep(min(wait, max_delay))
                    wait *= 2
        return wrapper
    return decorator

def calculate_spread_zscore(ohlcv_a, ohlcv_b):
    try:
        closes_a = np.array([c[4] for c in ohlcv_a], dtype=float)
        closes_b = np.array([c[4] for c in ohlcv_b], dtype=float)
        
        if len(closes_a) != len(closes_b):
            min_len = min(len(closes_a), len(closes_b))
            closes_a = closes_a[-min_len:]
            closes_b = closes_b[-min_len:]
            
        spread = closes_a - closes_b
        mean = np.mean(spread)
        std = np.std(spread)
        
        if std == 0:
            return 0
            
        return (spread[-1] - mean) / std
    except Exception:
        return None
