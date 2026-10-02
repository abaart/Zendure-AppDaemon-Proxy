# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import sys
import unittest
from datetime import datetime
from zoneinfo import ZoneInfo
import yaml
from jinja2 import Environment

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

class ArtifactTests(unittest.TestCase):
    def test_dashboard_uses_native_cards_and_stable_sensor_identities(self):
        data = yaml.safe_load((ROOT / 'apps/Zendure-AppDaemon-Proxy/dashboard.yaml').read_text())
        self.assertEqual(data['views'][0]['path'], '0')
        self.assertIn('metrics', [view['path'] for view in data['views']])
        self.assertEqual(data['views'][0]['cards'][2]['columns'], 1)
        text = (ROOT / 'apps/Zendure-AppDaemon-Proxy/dashboard.yaml').read_text()
        for slot in range(1, 4):
            self.assertIn(f'sensor.zendure_{slot}_health', text)
            self.assertIn(f'sensor.zendure_{slot}_wifi_rssi', text)

    def test_relay_history_interval_has_current_local_day_boundaries(self):
        data = yaml.safe_load((ROOT / 'examples/per-device-relay-history-stats.yaml').read_text())
        self.assertEqual(len(data['sensor']), 6)
        env = Environment()
        for sample in [datetime(2026, 10, 25, 0, 0), datetime(2026, 10, 25, 23, 59), datetime(2026, 10, 26, 0, 0)]:
            clock = sample.replace(tzinfo=ZoneInfo('Europe/Amsterdam'))
            context = {'today_at': lambda _: clock.replace(hour=0, minute=0, second=0), 'now': lambda: clock, 'as_timestamp': lambda value: value.timestamp()}
            for sensor in data['sensor']:
                start = float(env.from_string(sensor['start']).render(**context))
                end = float(env.from_string(sensor['end']).render(**context))
                self.assertLessEqual(start, end)
                self.assertEqual(datetime.fromtimestamp(start, clock.tzinfo).date(), clock.date())
                self.assertNotIn('23:59', sensor['start'])

    def test_hacs_zip_configuration_and_license_check_enabled(self):
        import json
        config = json.loads((ROOT / 'hacs.json').read_text())
        self.assertTrue(config['zip_release'])
        self.assertEqual(config['filename'], 'zendure-zensdk-proxy-appdaemon.zip')
        workflow = (ROOT / '.github/workflows/validate-hacs.yml').read_text()
        self.assertNotIn('ignore:', workflow)
        self.assertIn('python -m pytest -q', workflow)
        self.assertIn('unittest discover', workflow)
