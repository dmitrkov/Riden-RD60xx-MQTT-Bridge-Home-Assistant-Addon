import asyncio
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "riden_rd60xx_home_assistant_addon"))

from bridge import Bridge
from psu_state import PSUState
from rd60xx import RD60xx


class KeepaliveFrameTests(unittest.TestCase):
    def test_frame_is_valid_rtu_read_of_one_register(self):
        # Reference frame: read 1 holding register at 0 from device 1
        self.assertEqual(RD60xx.keepalive_frame(1), bytes.fromhex("010300000001840A"))

    def test_default_frame_goes_to_unused_address(self):
        self.assertEqual(RD60xx.keepalive_frame()[0], RD60xx.KEEPALIVE_ADDR)
        self.assertEqual(RD60xx.KEEPALIVE_ADDR, 247)


class BridgeKeepaliveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.state = PSUState()
        self.psu = Mock(connected=True, get_state=AsyncMock(), set_state=AsyncMock(),
                        set_clock=AsyncMock(), send_keepalive=Mock())
        self.tasks = []

    async def asyncTearDown(self):
        for task in self.tasks:
            task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)

    def make_bridge(self, keepalive):
        worker = Bridge("127.0.0.1", 8080, "60301_7565", 60301, 7565, self.psu, self.state,
                        AsyncMock(), False, keepalive)
        self.tasks.append(worker._psu_task)
        return worker

    async def test_idle_bridge_sends_keepalive_without_reading_state(self):
        self.state.update_period = 0
        self.make_bridge(0.05)
        await asyncio.sleep(0.18)
        self.assertGreaterEqual(self.psu.send_keepalive.call_count, 2)
        self.psu.get_state.assert_not_called()

    async def test_polling_bridge_needs_no_keepalive(self):
        self.state.update_period = 0.05
        self.make_bridge(0.05)
        await asyncio.sleep(0.18)
        self.psu.send_keepalive.assert_not_called()
        self.assertGreaterEqual(self.psu.get_state.await_count, 2)

    async def test_keepalive_zero_disables_it(self):
        self.state.update_period = 0
        self.make_bridge(0)
        await asyncio.sleep(0.1)
        self.psu.send_keepalive.assert_not_called()


if __name__ == "__main__":
    unittest.main()
