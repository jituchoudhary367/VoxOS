"""
OpenTelemetry-compatible tracing wrappers.

Provides decorators and context managers to trace agent and tool execution
for observability and debugging.
"""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, TypeVar

from voxos.observability.logger import get_logger

log = get_logger(__name__)

F = TypeVar('F', bound=Callable[..., Any])


def trace(name: str | None = None) -> Callable[[F], F]:
    """
    A simple tracing decorator.
    
    In a real implementation, this would integrate with OpenTelemetry
    tracer.start_as_current_span(). For now, it adds structured logging
    and timing around the function call.
    """
    def decorator(func: F) -> F:
        span_name = name or func.__qualname__
        
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                import time
                start = time.monotonic()
                log.debug(f"trace_start", span=span_name)
                try:
                    result = await func(*args, **kwargs)
                    duration_ms = (time.monotonic() - start) * 1000
                    log.debug(f"trace_end", span=span_name, duration_ms=duration_ms, status="ok")
                    return result
                except Exception as e:
                    duration_ms = (time.monotonic() - start) * 1000
                    log.error(f"trace_error", span=span_name, duration_ms=duration_ms, error=str(e), status="error")
                    raise
            return async_wrapper  # type: ignore
        else:
            @functools.wraps(func)
            def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
                import time
                start = time.monotonic()
                log.debug(f"trace_start", span=span_name)
                try:
                    result = func(*args, **kwargs)
                    duration_ms = (time.monotonic() - start) * 1000
                    log.debug(f"trace_end", span=span_name, duration_ms=duration_ms, status="ok")
                    return result
                except Exception as e:
                    duration_ms = (time.monotonic() - start) * 1000
                    log.error(f"trace_error", span=span_name, duration_ms=duration_ms, error=str(e), status="error")
                    raise
            return sync_wrapper  # type: ignore
            
    return decorator
