import asyncio
from types import SimpleNamespace

from api.routers.trace import trace_socket


def test_idle_trace_socket_releases_subscription_on_disconnect():
    async def scenario():
        queue = asyncio.Queue()
        released = []
        trace = SimpleNamespace(subscribe=lambda: queue, recent=lambda _: [], unsubscribe=released.append)
        class Socket:
            app = SimpleNamespace(state=SimpleNamespace(runtime=SimpleNamespace(trace=trace)))
            async def accept(self): pass
            async def receive(self): return {'type': 'websocket.disconnect'}
            async def send_json(self, event): raise AssertionError('empty queue')
        await asyncio.wait_for(trace_socket(Socket()), 1)
        assert released == [queue]
    asyncio.run(scenario())


def test_trace_delivery_and_cancellation_release_pending_waiters():
    async def scenario():
        queue = asyncio.Queue()
        queue.put_nowait({'event': 'test'})
        sent, released = [], []
        ready = asyncio.Event()
        trace = SimpleNamespace(subscribe=lambda: queue, recent=lambda _: [], unsubscribe=released.append)
        class Socket:
            app = SimpleNamespace(state=SimpleNamespace(runtime=SimpleNamespace(trace=trace)))
            async def accept(self): pass
            async def receive(self): await asyncio.Future()
            async def send_json(self, event): sent.append(event); ready.set()
        task = asyncio.create_task(trace_socket(Socket()))
        await asyncio.wait_for(ready.wait(), 1)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
        assert sent == [{'event': 'test'}]
        assert released == [queue]
        assert not [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
    asyncio.run(scenario())
