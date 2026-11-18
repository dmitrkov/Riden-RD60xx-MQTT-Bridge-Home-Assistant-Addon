import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "riden_rd60xx_home_assistant_addon"))

from mqtt_discovery import publish_discovery_config
from rd60xx_to_mqtt import RD60xxToMQTT


IDENTITY = "60181_286"


class MonitoringDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_monitoring_switch_toggles_period(self):
        client = Mock(publish=AsyncMock())
        await publish_discovery_config(client, "riden", "homeassistant", IDENTITY, 60181,
                                       "Riden RD6018", "1.39", monitor_period=5)

        configs = {c.args[0]: json.loads(c.kwargs["payload"]) for c in client.publish.call_args_list}
        switch = configs[f"homeassistant/switch/riden_{IDENTITY}/monitoring/config"]

        self.assertEqual(switch["name"], "Monitoring")
        self.assertEqual(switch["command_topic"], f"riden/psu/{IDENTITY}/state/set")
        self.assertEqual(json.loads(switch["payload_on"]), {"period": 5})
        self.assertEqual(json.loads(switch["payload_off"]), {"period": 0})
        self.assertEqual(switch["value_template"], "{{ value_json.period > 0 }}")


class InitialPeriodTests(unittest.TestCase):
    def test_new_psu_is_not_polled_by_default(self):
        service = RD60xxToMQTT("unused", 1883, default_update_period=5)
        state = service._psu_states.get_state(IDENTITY)
        service._apply_initial_period(state)
        self.assertEqual(state.update_period, 0)

    def test_monitor_on_connect_polls_with_default_period(self):
        service = RD60xxToMQTT("unused", 1883, default_update_period=5, monitor_on_connect=True)
        state = service._psu_states.get_state(IDENTITY)
        service._apply_initial_period(state)
        self.assertEqual(state.update_period, 5)

    def test_reconnect_keeps_period_chosen_by_user(self):
        service = RD60xxToMQTT("unused", 1883, default_update_period=5)
        state = service._psu_states.get_state(IDENTITY)
        state.update_period = 5
        service._apply_initial_period(state)
        self.assertEqual(state.update_period, 5)


class StateSetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = RD60xxToMQTT("unused", 1883, default_update_period=5)
        self.service._mqtt_client = Mock(publish=AsyncMock())
        self.state = self.service._psu_states.get_state(IDENTITY)
        self.state.update_period = 0

    async def test_period_only_reads_state_once_without_writing(self):
        psu = Mock()
        self.service._psus[IDENTITY] = psu

        await self.service._handle_state_set(IDENTITY, {"period": 5})

        self.assertEqual(self.state.update_period, 5)
        psu.queue_state_get.assert_called_once_with()
        psu.queue_state_set.assert_not_called()

    async def test_settings_are_still_written(self):
        psu = Mock()
        self.service._psus[IDENTITY] = psu

        await self.service._handle_state_set(IDENTITY, {"output_voltage_set": 5.0})

        psu.queue_state_set.assert_called_once_with({"output_voltage_set": 5.0})
        psu.queue_state_get.assert_not_called()

    async def test_period_of_disconnected_psu_is_published(self):
        await self.service._handle_state_set(IDENTITY, {"period": 0})

        payloads = [json.loads(c.kwargs["payload"]) for c in self.service._mqtt_client.publish.call_args_list]
        self.assertIn({"connected": False, "period": 0}, payloads)


if __name__ == "__main__":
    unittest.main()
