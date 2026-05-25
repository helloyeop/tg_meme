import time
from collections.abc import Callable
from functools import wraps
from typing import TypeVar


T = TypeVar("T")


def simple_retry(attempts: int = 2, initial_delay: float = 0.25) -> Callable:
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @wraps(func)
        def wrapper(*args, **kwargs) -> T:
            delay = initial_delay
            last_error: Exception | None = None
            for _ in range(attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as exc:
                    last_error = exc
                    time.sleep(delay)
                    delay *= 2
            raise last_error  # type: ignore[misc]

        return wrapper

    return decorator
