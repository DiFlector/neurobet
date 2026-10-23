import asyncio
import threading

try:
    from app.main import run_periodic_predictions_cycle
except ImportError:
    from services.research.app.main import run_periodic_predictions_cycle


def test_periodic_predictions_cycle_does_not_block_api_event_loop():
    started = threading.Event()
    release = threading.Event()

    class BlockingCoordinator:
        def run_cycle(self):
            started.set()
            release.wait(timeout=2)

    async def scenario():
        cycle = asyncio.create_task(run_periodic_predictions_cycle(BlockingCoordinator()))
        # Even if the coordinator regresses to a synchronous call, release it
        # from another thread so the test cannot deadlock the test process.
        release_timer = threading.Timer(0.5, release.set)
        release_timer.start()

        async def api_heartbeat():
            await asyncio.sleep(0.05)
            return "responsive"

        try:
            result = await asyncio.wait_for(api_heartbeat(), timeout=0.25)
        finally:
            release.set()
            release_timer.cancel()
        await cycle
        return result

    assert asyncio.run(scenario()) == "responsive"
