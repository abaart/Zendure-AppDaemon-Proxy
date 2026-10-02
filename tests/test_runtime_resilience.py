# SPDX-License-Identifier: GPL-3.0-only
"""Exercise lifecycle, request batching and failed refresh recovery."""
import asyncio
from copy import deepcopy
import types
from pathlib import Path
import unittest
from unittest.mock import patch

# Install the synthetic AppDaemon boundary used by compatibility tests.
from test_appdaemon_proxy_release_gate import ZendureProxy, Config, ProxyState, DeviceState
from zendure_proxy_metrics import MetricsRegistry
from zendure_proxy_queue import RequestQueue


async def noop(*args, **kwargs):
    return None


class RuntimeResilienceTests(unittest.IsolatedAsyncioTestCase):
    def proxy(self):
        app = ZendureProxy.__new__(ZendureProxy)
        app._cfg = Config(device_ips=["ip1"])
        app._state = ProxyState(device_count=1, devices=[DeviceState(ip="ip1", sn="SN1")])
        app._metrics = MetricsRegistry(1)
        app._queue = RequestQueue()
        app._clients = []
        app._proxy_log = lambda *args, **kwargs: None
        app._publish_report_sensors = noop
        app._standby_check = noop
        return app

    async def test_failed_refresh_preserves_original_cache_timestamp(self):
        app = self.proxy()
        app._state.latest_get_ts = 100
        app._state.last_get_response = {"properties": {"electricLevel": 50}, "packData": []}
        cached = deepcopy(app._state.last_get_response)
        cached["proxyHealth"] = {"servedFromCache": True, "reason": "upstream_failed"}
        with patch("zendure_proxy.execute_get", return_value=cached), patch("zendure_proxy.now", return_value=120):
            await app._refresh_proxy_ha_sensors()
        self.assertEqual(app._state.latest_get_ts, 100)
        self.assertEqual(app._state.last_upstream_get_ts, 120)
        self.assertNotIn("proxyHealth", app._state.last_get_response)
        self.assertFalse(app._state.get_refresh_in_progress)

    async def test_processor_answers_failed_batch_and_accepts_next_request(self):
        app = self.proxy()
        report = {"properties": {"sn_1": "SN1"}, "packData": []}
        first = await app._queue.enqueue_get()
        with patch("zendure_proxy.execute_get", side_effect=[RuntimeError("synthetic transport fault"), report]):
            processor = asyncio.create_task(app._processor())
            with self.assertRaisesRegex(RuntimeError, "synthetic transport fault"):
                await asyncio.wait_for(first, 1)
            second = await app._queue.enqueue_get()
            self.assertEqual(await asyncio.wait_for(second, 1), report)
            self.assertFalse(processor.done())
            processor.cancel()
            await processor

    async def test_deduplicated_post_replies_to_every_waiter_with_latest_payload(self):
        app = self.proxy()
        first = await app._queue.enqueue_post({"properties": {"inputLimit": 10}})
        second = await app._queue.enqueue_post({"properties": {"inputLimit": 20}})
        with patch("zendure_proxy.execute_post", return_value={"ack": "ok"}) as write:
            processor = asyncio.create_task(app._processor())
            self.assertEqual(await asyncio.wait_for(first, 1), {"ack": "ok"})
            self.assertEqual(await asyncio.wait_for(second, 1), {"ack": "ok"})
            self.assertEqual(write.await_count, 1)
            self.assertEqual(write.call_args.args[0], {"properties": {"inputLimit": 20}})
            processor.cancel()
            await processor

    async def test_termination_closes_all_runtime_resources_with_task_apis(self):
        app = self.proxy()
        calls = []
        def asynchronous_call(kind, value):
            calls.append((kind, value))
            return asyncio.create_task(asyncio.sleep(0))
        app.cancel_timer = lambda value: asynchronous_call("timer", value)
        app.deregister_endpoint = lambda value: asynchronous_call("endpoint", value)
        app.deregister_route = lambda value: asynchronous_call("route", value)
        app._timer_handles = ["heartbeat", "metrics"]
        app._endpoint_handles = ["report", "write", "gielz"]
        app._route_handles = ["logs", "metrics", "diagnostics"]
        app._mqtt_api = types.SimpleNamespace(cancel_listen_event=lambda value: asynchronous_call("listener", value))
        app._mqtt_connection_handle = "mqtt"
        app._runner = types.SimpleNamespace(cleanup=lambda: asynchronous_call("runner", "http"))
        app._clients = [types.SimpleNamespace(close=lambda: asynchronous_call("client", "ip1"))]
        app._file_logger = types.SimpleNamespace(close=lambda: calls.append(("file", "logs")))
        app._state.devices[0].standby_task = asyncio.create_task(asyncio.sleep(30))
        app._processor_task = asyncio.create_task(app._processor())
        pending = await app._queue.enqueue_post({"ping": "pong"})
        await app.terminate()
        self.assertTrue(pending.cancelled())
        self.assertTrue(app._processor_task.done())
        self.assertTrue(app._state.devices[0].standby_task.cancelled())
        self.assertEqual(await app._queue.depths(), (0, 0))
        self.assertEqual(set(calls), {("timer", "heartbeat"), ("timer", "metrics"),
            ("endpoint", "report"), ("endpoint", "write"), ("endpoint", "gielz"),
            ("route", "logs"), ("route", "metrics"), ("route", "diagnostics"),
            ("listener", "mqtt"), ("runner", "http"), ("client", "ip1"), ("file", "logs")})

    async def test_processor_cancellation_cancels_inflight_waiter(self):
        app = self.proxy()
        entered = asyncio.Event()
        async def blocked_get(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        future = await app._queue.enqueue_get()
        with patch("zendure_proxy.execute_get", side_effect=blocked_get):
            processor = asyncio.create_task(app._processor())
            await entered.wait()
            processor.cancel()
            await processor
        self.assertTrue(future.cancelled())
        self.assertFalse(app._state.get_refresh_in_progress)

    def test_power_repeat_waits_thirty_seconds_and_requires_two_eligible_devices(self):
        from zendure_proxy import should_repeat_last_power
        app = self.proxy()
        app._state.devices.append(DeviceState(ip="ip2", sn="SN2"))
        app._state.device_count = 2
        app._state.latest_get_ts = 100
        app._state.latest_power_cmd = 100
        app._state.ac_mode = 1
        app._state.last_post_payload = {"properties": {"acMode": 1, "inputLimit": 100}}
        for age in (20, 25, 29):
            app._state.latest_power_message_ts = 100 - age
            self.assertFalse(should_repeat_last_power(app._state, app._cfg, current_ts=100))
        app._state.latest_power_message_ts = 70
        self.assertTrue(should_repeat_last_power(app._state, app._cfg, current_ts=100))
        app._state.devices[1].excluded_since_ts = 90
        self.assertFalse(should_repeat_last_power(app._state, app._cfg, current_ts=100))
        app._state.devices[1].excluded_since_ts = 0
        app._state.last_post_payload["properties"]["inputLimit"] = -100
        self.assertFalse(should_repeat_last_power(app._state, app._cfg, current_ts=100))

    async def test_existing_rest_device_statuses_follow_immediate_health_transition(self):
        app = self.proxy()
        writes = {}
        app.get_state = lambda entity_id, **kwargs: {"state": "old", "attributes": {"source": "REST"}} if entity_id.startswith("sensor.") else None
        app.set_state = lambda entity_id, **kwargs: writes.update({entity_id: kwargs})
        healthy = {"properties": {"smartMode_1": 1, "acMode_1": 1, "gridInputPower_1": 100,
            "electricLevel_1": 50, "socLimit_1": 0, "gridOffMode_1": 2, "socStatus_1": 0,
            "sn_1": "SN1", "ipAddress_1": "ip1", "rssi_1": -60}, "packData": [],
            "proxyHealth": {"configuredCount": 1}}
        with patch("zendure_proxy.now", return_value=100):
            await app._publish_health_transition_sensors(healthy)
        writes.clear()
        dead = deepcopy(healthy)
        item = {"slot": 1, "serialNumber": "SN1"}
        dead["proxyHealth"].update(excludedDevices=[item], unhealthyDevices=[item], deadDevices=[item])
        with patch("zendure_proxy.now", return_value=101):
            await app._publish_health_transition_sensors(dead)
        for suffix in ("relais_stand", "kalibratie_bezig", "opslagmodus", "deep_standby", "soc_limiet_status", "offgrid_modus", "wifi_rssi"):
            value = writes[f"sensor.zendure_1_{suffix}"]
            self.assertEqual(value["state"], "unavailable")
            self.assertFalse(value["replace"])
            self.assertNotIn("zendure_proxy_managed", value["attributes"])

    async def test_endpoint_cleanup_failure_still_closes_server_clients_and_logger(self):
        app = self.proxy()
        closed = []
        app._endpoint_handles = ["stale", "working"]
        def deregister(handle):
            if handle == "stale":
                raise RuntimeError("Synthetic stale endpoint")
            closed.append(handle)
        app.deregister_endpoint = deregister
        app._runner = types.SimpleNamespace(cleanup=lambda: closed.append("http"))
        app._clients = [types.SimpleNamespace(close=lambda: closed.append("client"))]
        app._file_logger = types.SimpleNamespace(close=lambda: closed.append("file"))
        await app.terminate()
        self.assertEqual(closed, ["working", "http", "client", "file"])

    async def test_failed_server_start_releases_registered_endpoints_and_clients(self):
        app = self.proxy()
        closed = []
        app.args = {"ip_zendure_1": "ip1", "log_file_enabled": False,
            "proxy_ha_sensors_mqtt_discovery_enabled": False, "log_dashboard_enabled": False,
            "metrics_dashboard_enabled": False, "diagnostics_dashboard_enabled": False}
        app._create_file_logger = lambda: None
        app._restore_metrics_counters_from_ha = noop
        app.register_endpoint = lambda callback, name: name
        app.deregister_endpoint = lambda name: closed.append(name)
        async def start_server():
            app._runner = types.SimpleNamespace(cleanup=lambda: closed.append("http"))
            raise OSError("Synthetic port busy")
        app._start_server = start_server
        client = types.SimpleNamespace(close=lambda: closed.append("client"))
        with patch("zendure_proxy.DeviceClient", return_value=client):
            with self.assertRaisesRegex(OSError, "Synthetic port busy"):
                await app.initialize()
        self.assertEqual(set(closed), {"zendure_proxy_report", "zendure_proxy_write", "zendure_proxy", "http", "client"})
        self.assertEqual(await app._queue.depths(), (0, 0))

    async def test_disabled_metrics_skip_registration_restoration_and_publication(self):
        app = self.proxy()
        routes, timers = [], []
        app.args = {"ip_zendure_1": "ip1", "metrics_enabled": False, "damper_enable": True,
            "log_file_enabled": False, "proxy_ha_sensors_enabled": False,
            "proxy_ha_sensors_mqtt_discovery_enabled": False}
        app.get_state = lambda *args, **kwargs: self.fail("Disabled metrics must not restore counters")
        app.set_state = lambda *args, **kwargs: self.fail("Disabled metrics must not publish counters")
        app.register_endpoint = lambda *args: "endpoint"
        app.register_route = lambda callback, name: routes.append(name)
        app.run_every = lambda callback, start, interval: timers.append(callback)
        app._start_server = noop
        app._init_serial_numbers = noop
        app._create_file_logger = lambda: None
        app.deregister_endpoint = lambda *args: None
        client = types.SimpleNamespace(close=noop)
        with patch("zendure_proxy.DeviceClient", return_value=client):
            await app.initialize()
        self.assertNotIn("zendure_proxy_metrics", routes)
        self.assertNotIn(app._publish_metrics_sensors, timers)
        self.assertTrue(app._state.dualmode_damper_enabled)
        await app._restore_metrics_counters_from_ha()
        await app._publish_metrics_sensors()
        await app._publish_metrics_sensor_values()
        await app._publish_sensor_heartbeats()
        await app.terminate()

    async def test_initial_serial_bootstrap_runs_without_blocking_start_and_is_cancelled(self):
        app = self.proxy()
        started = asyncio.Event()
        completed = asyncio.Event()
        app.args = {"ip_zendure_1": "ip1", "log_file_enabled": False,
            "metrics_enabled": False, "proxy_ha_sensors_enabled": False,
            "proxy_ha_sensors_mqtt_discovery_enabled": False}
        async def blocked_bootstrap():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                completed.set()
        app._init_serial_numbers = blocked_bootstrap
        app._start_server = noop
        app._create_file_logger = lambda: None
        app.register_endpoint = lambda *args: "endpoint"
        app.register_route = lambda *args: "route"
        app.run_every = lambda *args: "timer"
        app.deregister_endpoint = lambda *args: None
        client = types.SimpleNamespace(close=noop)
        with patch("zendure_proxy.DeviceClient", return_value=client):
            await asyncio.wait_for(app.initialize(), 1)
        await asyncio.wait_for(started.wait(), 1)
        self.assertFalse(app._bootstrap_task.done())
        await app.terminate()
        self.assertTrue(completed.is_set())
        self.assertTrue(app._bootstrap_task.cancelled())

    def test_default_log_file_uses_appdaemon_config_directory(self):
        app = self.proxy()
        app.config_dir = Path("/private/tmp/zendure-runtime-logging-check")
        with patch("zendure_proxy.ProxyFileLogger") as logger:
            app._create_file_logger()
        self.assertEqual(logger.call_args.args[0], "/private/tmp/zendure-runtime-logging-check/logs/zendure_proxy.log")
