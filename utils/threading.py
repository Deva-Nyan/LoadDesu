# utils/threading.py
import asyncio

async def run_io(func, *args, **kwargs):
    """Run sync/CPU/IO func in threadpool (не блокируя event-loop)."""
    return await asyncio.to_thread(func, *args, **kwargs)
