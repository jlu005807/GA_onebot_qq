import asyncio
import contextvars
import functools
from typing import Any, Callable


async def run_in_thread(func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run blocking function in thread, compatible with Python 3.8+."""
    if hasattr(asyncio, "to_thread"):
        return await asyncio.to_thread(func, *args, **kwargs)
    loop = asyncio.get_running_loop()
    ctx = contextvars.copy_context()
    call = functools.partial(ctx.run, func, *args, **kwargs)
    return await loop.run_in_executor(None, call)
