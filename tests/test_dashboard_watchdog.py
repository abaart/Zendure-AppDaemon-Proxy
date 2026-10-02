# SPDX-License-Identifier: GPL-3.0-only
"""Render the native dashboard card against optional watchdog states."""
import math
from pathlib import Path
import unittest

from jinja2 import Environment, StrictUndefined
import yaml

ROOT = Path(__file__).resolve().parents[1]


class DashboardWatchdogTests(unittest.TestCase):
    def setUp(self):
        dashboard = yaml.safe_load((ROOT / 'apps/Zendure-AppDaemon-Proxy/dashboard.yaml').read_text())
        self.content = dashboard['views'][0]['cards'][0]['content']
        self.monitors = {}
        self.states = {}
        env = Environment(undefined=StrictUndefined)
        env.globals.update(state_attr=lambda entity, _: self.monitors.get(entity),
                           states=lambda entity: self.states.get(entity, 'unknown'),
                           now=lambda: 1000000, as_timestamp=lambda value: value,
                           is_number=self.is_number)
        self.template = env.from_string(self.content)

    @staticmethod
    def is_number(value):
        try:
            return math.isfinite(float(value))
        except (TypeError, ValueError):
            return False

    def monitor(self, slot=1, state='OK', **changes):
        entity = f'sensor.zendure_{slot}_network_monitor'
        self.states[entity] = state
        self.monitors[entity] = dict(checked_at=999990, source_running=True,
            baseline_ready=True, average_rssi=-60, connection_alert=False,
            wifi_alert=False, errors_last_hour=0, connection_hours=24,
            rssi=-60, wifi_reference=None, rssi_minutes=10, **{})
        self.monitors[entity].update(changes)

    def render(self):
        return self.template.render()

    def test_optional_package_missing_has_no_incident_or_learning_claim(self):
        text = self.render()
        self.assertEqual(text.count('Optional network monitor unavailable.'), 3)
        self.assertNotIn('Persistent connection incident', text)
        self.assertNotIn('Wi-Fi reference learning', text)
        self.assertIn('[History](./history)', text)
        self.assertIn('[Diagnostics](./metrics)', text)

    def test_healthy_reference_ready_and_learning_are_distinct(self):
        self.monitor()
        self.monitor(2, state='Learning', baseline_ready=False, average_rssi=None)
        text = self.render()
        self.assertIn('No persistent network incident reported.', text)
        self.assertIn('Wi-Fi reference ready; 24-hour average -60.0 dBm.', text)
        self.assertEqual(text.count('Wi-Fi reference learning:'), 1)
        self.assertNotIn('Persistent Wi-Fi incident', text)

    def test_connection_incident_reports_custom_threshold_and_error_count(self):
        self.monitor(state='Connection', connection_alert=True, errors_last_hour=173, connection_hours=12)
        text = self.render()
        self.assertIn('173 errors/hour after at least 12.0 hours', text)
        self.assertIn('Check the access point, Wi-Fi range and Zendure device.', text)
        self.assertNotIn('Persistent Wi-Fi incident', text)

    def test_wifi_uses_frozen_reference_instead_of_recent_average(self):
        self.monitor(state='Wi-Fi', wifi_alert=True, rssi=-78, wifi_reference=-59.5, average_rssi=-72, rssi_minutes=15)
        text = self.render()
        self.assertIn('current -78.0 dBm; frozen pre-incident reference -59.5 dBm', text)
        self.assertIn('at least 15.0 minutes', text)
        self.assertNotIn('Persistent connection incident', text)

    def test_combined_causes_and_multiple_devices_are_all_visible(self):
        self.monitor(state='Connection and Wi-Fi', connection_alert=True, errors_last_hour=200,
                     wifi_alert=True, rssi=-80, wifi_reference=-60)
        self.monitor(2, state='Connection', connection_alert=True, errors_last_hour=150)
        self.monitor(3, state='Learning', baseline_ready=False)
        text = self.render()
        self.assertEqual(text.count('Persistent connection incident'), 2)
        self.assertEqual(text.count('Persistent Wi-Fi incident'), 1)
        self.assertIn('**Device 3**', text)
        self.assertEqual(text.count('Wi-Fi reference learning:'), 1)

    def test_paused_stale_and_unavailable_monitors_do_not_raise_false_alarms(self):
        self.monitor(source_running=False, baseline_ready=False, connection_alert=True)
        self.monitor(2, checked_at=999000, wifi_alert=True, wifi_reference=-60)
        self.monitor(3, state='unavailable', connection_alert=True)
        text = self.render()
        self.assertEqual(text.count('Monitoring paused;'), 2)
        self.assertNotIn('Persistent connection incident', text)
        self.assertNotIn('Persistent Wi-Fi incident', text)
        self.assertNotIn('Wi-Fi reference learning:', text)

    def test_malformed_monitor_and_invalid_measurements_render_without_alarm(self):
        for invalid in (None, 'broken', [], 12):
            with self.subTest(monitor=invalid):
                self.states['sensor.zendure_1_network_monitor'] = 'Connection'
                self.monitors['sensor.zendure_1_network_monitor'] = invalid
                self.assertIn('Optional network monitor unavailable.', self.render())
        for rssi in (None, 'unknown', float('nan'), -121, 0):
            with self.subTest(rssi=rssi):
                self.monitor(state='Connection and Wi-Fi', connection_alert=True,
                             errors_last_hour='broken', wifi_alert=True, rssi=rssi, wifi_reference=-60)
                text = self.render()
                self.assertIn('Monitor details unavailable;', text)
                self.assertNotIn('Persistent connection incident', text)
                self.assertNotIn('Persistent Wi-Fi incident', text)
        self.monitor(connection_alert=True, errors_last_hour=-1)
        self.assertNotIn('Persistent connection incident', self.render())
        self.monitor(connection_alert='true', wifi_alert='true')
        self.assertNotIn('Persistent connection incident', self.render())
        self.assertNotIn('Persistent Wi-Fi incident', self.render())


if __name__ == '__main__':
    unittest.main()
