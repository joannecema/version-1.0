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

def exponential_backoff(retries: int = 3, delay: float = 1.0, max_delay: float = 10.0):
    """
    Retry a coroutine function with exponential backoff.
    - retries: number of attempts
    - delay: initial delay in seconds
    - max_delay: maximum delay between attempts
    """
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
                        log.error(f"[BACKOFF] {func.__name__} failed after {retries} retries.")
                        raise
                    await asyncio.sleep(min(wait, max_delay))
                    wait *= 2
        return wrapper
    return decorator
