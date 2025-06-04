import asyncio
import logging
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

def exponential_backoff(retries: int = 3, base_delay: float = 1.0):
    def decorator(func: Callable):
        @wraps(func)
        async def wrapper(*args, **kwargs) -> Any:
            for attempt in range(retries):
                try:
                    return await func(*args, **kwargs)
                except Exception as e:
                    delay = base_delay * (2 ** attempt)
                    log.warning(f"[BACKOFF] {func.__name__} failed (attempt {attempt + 1}/{retries}): {e}")
                    await asyncio.sleep(delay)
            log.error(f"[BACKOFF] {func.__name__} failed after {retries} retries.")
            return None
        return wrapper
    return decorator
