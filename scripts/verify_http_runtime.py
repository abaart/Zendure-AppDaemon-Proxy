# SPDX-License-Identifier: GPL-3.0-only
"""Exercise genuine aiohttp sockets with a mocked AppDaemon method boundary.

Run with .venv/bin/python scripts/verify_http_runtime.py. Servers bind only to
127.0.0.1; the helper never imports the synthetic suite's fake conftest modules.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from importlib.metadata import version
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "apps" / "Zendure-AppDaemon-Proxy"))

import aiohttp
from aiohttp import web
import appdaemon.plugins.hass.hassapi as hassapi
from zendure_proxy import ZendureProxy
from zendure_proxy_power import now


async def wait_until(predicate, timeout=2):
    async def wait():
        while not predicate():
            await asyncio.sleep(0.001)
    await asyncio.wait_for(wait(), timeout)


def listener_url(runner):
    site = next(iter(runner.sites))
    address = site._server.sockets[0].getsockname()
    assert address[0] == "127.0.0.1"
    return f"http://127.0.0.1:{address[1]}"


class MockDevice:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.gets = 0
        self.posts = []
        self.fail_get = False
        self.fail_post = False
        self.invalid_report = None
        self.get_gate = asyncio.Event()
        self.get_gate.set()
        self.properties = {
            "electricLevel": 50, "socLimit": 0, "smartMode": 1,
            "socStatus": 0, "acMode": 1, "inputLimit": 0, "outputLimit": 0,
            "chargeMaxLimit": 800, "inverseMaxPower": 800,
            "minSoc": 100, "socSet": 1000, "gridOffMode": 2,
            "packInputPower": 0, "outputPackPower": 0,
            "gridInputPower": 0, "outputHomePower": 0, "hyperTmp": 2931,
        }

    async def handle(self, request):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        try:
            if request.method == "GET":
                self.gets += 1
                await self.get_gate.wait()
                await asyncio.sleep(0.01)
                if self.fail_get:
                    return web.json_response({"error": "synthetic GET failure"}, status=500)
                if self.invalid_report is not None:
                    return web.json_response(self.invalid_report)
                return web.json_response({"sn": "MOCK-SN", "product": "Synthetic inverter",
                    "properties": {**self.properties, "freshCounter": self.gets},
                    "packData": [{"socLevel": 50, "maxTemp": 2931}]})
            payload = await request.json()
            self.posts.append(deepcopy(payload))
            await asyncio.sleep(0.01)
            if self.fail_post:
                return web.json_response({"error": "synthetic POST failure"}, status=503)
            self.properties.update(payload.get("properties", {}))
            return web.json_response({"ack": "pong"})
        finally:
            self.active -= 1


class AppDaemonBoundary:
    """Replace methods, while retaining the actual Hass base class import."""
    def __init__(self, awaitable):
        self.awaitable = awaitable
        self.endpoints = {}
        self.routes = {}
        self.timers = {}
        self.cancelled_timers = []
        self.deregistered_endpoints = []
        self.deregistered_routes = []
        self.states = {"sensor.zendure_proxy_incoming_get_total": {
            "state": "7", "attributes": {"zendure_proxy_managed": True}}}
        self.logs = []
        self.sequence = 0

    def result(self, value):
        if not self.awaitable:
            return value
        async def resolve():
            await asyncio.sleep(0)
            return value
        return asyncio.create_task(resolve())

    def register(self, destination, callback, name):
        self.sequence += 1
        handle = f"{name}-{self.sequence}"
        destination[handle] = (callback, name)
        return self.result(handle)

    def get_state(self, entity=None, attribute=None, **kwargs):
        state = self.states.get(entity)
        return self.result(deepcopy(state) if attribute == "all" else state.get("state") if state else None)

    def set_state(self, entity, state=None, attributes=None, **kwargs):
        self.states[entity] = {"state": state, "attributes": deepcopy(attributes or {})}
        return self.result(deepcopy(self.states[entity]))

    def attach(self, proxy):
        proxy.log = lambda message, level="INFO", **kwargs: self.logs.append((message, level))
        proxy.get_state = self.get_state
        proxy.set_state = self.set_state
        proxy.get_plugin_api = lambda name: self.result(None)
        proxy.register_endpoint = lambda callback, name: self.register(self.endpoints, callback, name)
        proxy.register_route = lambda callback, name: self.register(self.routes, callback, name)
        proxy.run_every = lambda callback, start, interval: self.register(self.timers, callback, str(interval))
        proxy.cancel_timer = lambda handle: self.result(self.cancelled_timers.append(handle))
        proxy.deregister_endpoint = lambda handle: self.result(self.deregistered_endpoints.append(handle))
        proxy.deregister_route = lambda handle: self.result(self.deregistered_routes.append(handle))


async def exercise(awaitable):
    device = MockDevice()
    upstream = web.Application()
    upstream.router.add_get("/properties/report", device.handle)
    upstream.router.add_post("/properties/write", device.handle)
    runner = web.AppRunner(upstream)
    await runner.setup()
    await web.TCPSite(runner, "127.0.0.1", 0).start()
    upstream_url = listener_url(runner)
    boundary = AppDaemonBoundary(awaitable)
    proxy = ZendureProxy.__new__(ZendureProxy)
    assert isinstance(proxy, hassapi.Hass)
    boundary.attach(proxy)
    proxy.args = {
        "ip_zendure_1": upstream_url,
        "server_host": "127.0.0.1", "server_port": 0,
        "log_file_enabled": False, "metrics_ha_sensors_enabled": True,
        "proxy_ha_sensors_enabled": True,
        "proxy_ha_sensors_mqtt_discovery_enabled": False,
        "zendure_request_timeout": 1, "ha_get_response_timeout": 1,
        "get_rate_limit_window": 0, "get_cache_max_age": 30,
        "get_recovery_window": 0, "idle_connection_close_seconds": 0,
    }
    initialized = False
    terminated = False
    try:
        await proxy.initialize()
        initialized = True
        await asyncio.wait_for(proxy._bootstrap_task, timeout=2)
        assert proxy._state.devices[0].sn == "MOCK-SN"
        url = listener_url(proxy._runner)
        assert len(boundary.endpoints) == 3 and len(boundary.routes) == 3
        assert len(boundary.timers) == 4
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5)) as session:
            async def report(path="/properties/report"):
                async with session.get(url + path) as response:
                    return response.status, await response.json()
            async def write(payload, path="/properties/write"):
                async with session.post(url + path, json=payload) as response:
                    return response.status, await response.json()

            # Late GET callers join the same upstream refresh before completion.
            device.get_gate.clear()
            before_gets = device.gets
            batch = [asyncio.create_task(report("/endpoint/properties/report" if index % 2 else "/properties/report")) for index in range(8)]
            await wait_until(lambda: proxy._state.counter_get_received == 8 and device.gets == before_gets + 1)
            device.get_gate.set()
            responses = await asyncio.gather(*batch)
            assert all(status == 200 for status, _ in responses)
            assert device.gets == before_gets + 1
            assert {data["properties"]["freshCounter"] for _, data in responses} == {before_gets + 1}
            assert proxy._state.devices[0].sn == "MOCK-SN"
            assert "sensor.zendure_1_serienummer" in boundary.states
            assert boundary.states["sensor.zendure_1_serienummer"]["state"] == "MOCK-SN"

            for path in ("/properties/write", "/endpoint/properties/write"):
                status, result = await write({"properties": {"acMode": 1, "inputLimit": 200}}, path)
                assert status == 200 and result == [{"ack": "pong"}]
                assert device.posts[-1]["sn"] == "MOCK-SN"
                assert device.posts[-1]["properties"]["inputLimit"] == 200
            status, _ = await write([])
            assert status == 400
            async with session.post(url + "/properties/write", data="{", headers={"Content-Type": "application/json"}) as response:
                assert response.status == 400

            api_report = next(callback for callback, name in boundary.endpoints.values() if name == "zendure_proxy_report")
            api_write = next(callback for callback, name in boundary.endpoints.values() if name == "zendure_proxy_write")
            api_compat = next(callback for callback, name in boundary.endpoints.values() if name == "zendure_proxy")
            data, status = await api_report({}, {})
            assert status == 200 and data["sn_1"] == "MOCK-SN"
            data, status = await api_write({"properties": {"socSet": 950}}, {})
            assert status == 200 and data == [{"ack": "pong"}]
            data, status = await api_compat({"path": "endpoint/properties/report"}, {})
            assert status == 200 and data["sn_1"] == "MOCK-SN"
            data, status = await api_compat({"path": "properties/write", "properties": {"socSet": 1000}}, {})
            assert status == 200 and data == [{"ack": "pong"}]

            # Separate HTTP session pools still share one device request worker.
            device.get_gate.clear()
            before_gets = device.gets
            direct_get = asyncio.create_task(proxy._clients[0].get())
            await wait_until(lambda: device.gets == before_gets + 1)
            before_posts = len(device.posts)
            direct_post = asyncio.create_task(proxy._clients[0].post({"sn": "MOCK-SN", "properties": {"inputLimit": 200}}))
            await asyncio.sleep(0.02)
            assert len(device.posts) == before_posts
            device.get_gate.set()
            await asyncio.gather(direct_get, direct_post)
            assert device.max_active == 1

            previous_report = deepcopy(proxy._state.devices[0].last_response)
            for invalid_report in ({}, {"error": "synthetic malformed report"}):
                device.invalid_report = invalid_report
                status, cached = await report()
                assert status == 200 and cached["proxyHealth"]["servedFromCache"] is True
                assert proxy._state.devices[0].last_response == previous_report
            device.invalid_report = None

            device.fail_get = True
            status, cached = await report()
            assert status == 200 and cached["proxyHealth"]["servedFromCache"] is True
            assert cached["proxyHealth"]["reason"] == "upstream_failed"
            proxy._state.latest_get_ts = now() - proxy._cfg.get_cache_max_age - 1
            status, expired = await report("/endpoint/properties/report")
            assert status == 504 and "error" in expired
            device.fail_get = False
            status, recovered = await report()
            assert status == 200 and recovered["proxyHealth"]["servedFromCache"] is False

            device.fail_post = True
            status, response = await write({"properties": {"socSet": 950}})
            assert status == 200 and response == [{"ack": "pong"}]
            assert proxy._state.devices[0].last_post_error == "POST HTTP 503"
            assert all("_zendureProxyPostFailed" not in item for item in response)
            assert any("POST HTTP 503" in message for message, _ in boundary.logs)
            device.fail_post = False
            status, recovered = await report()
            assert status == 200

            # Termination cancels callers while a real upstream socket is waiting.
            device.get_gate.clear()
            before_gets = device.gets
            pending = [asyncio.create_task(report()) for _ in range(2)]
            await wait_until(lambda: device.gets == before_gets + 1)
            sessions = [session for session in proxy._clients[0]._sessions.values() if session]
            await asyncio.wait_for(proxy.terminate(), timeout=3)
            terminated = True
            outcome = await asyncio.gather(*pending, return_exceptions=True)
            assert all(isinstance(item, BaseException) for item in outcome)
            assert all(session.closed for session in sessions)
            assert proxy._processor_task.done() and proxy._clients[0]._worker_task.done()
            assert await proxy._queue.depths() == (0, 0)
            assert set(boundary.cancelled_timers) == set(boundary.timers)
            assert set(boundary.deregistered_endpoints) == set(boundary.endpoints)
            assert set(boundary.deregistered_routes) == set(boundary.routes)
            assert not proxy._runner.sites
            try:
                await report()
            except aiohttp.ClientConnectionError:
                pass
            else:
                raise AssertionError("Proxy listener remained open after terminate")
        return {"appdaemon_methods": "awaitable" if awaitable else "direct", "get_requests": device.gets,
                "post_requests": len(device.posts), "max_device_requests_in_flight": device.max_active,
                "registered_endpoints": len(boundary.endpoints), "published_entities": len(boundary.states),
                "terminated": terminated}
    finally:
        device.get_gate.set()
        if initialized and not terminated:
            await proxy.terminate()
        await runner.cleanup()


async def exercise_failed_initialize(awaitable):
    occupied = web.AppRunner(web.Application())
    await occupied.setup()
    await web.TCPSite(occupied, "127.0.0.1", 0).start()
    port = next(iter(occupied.sites))._server.sockets[0].getsockname()[1]
    boundary = AppDaemonBoundary(awaitable)
    proxy = ZendureProxy.__new__(ZendureProxy)
    boundary.attach(proxy)
    proxy.args = {
        "ip_zendure_1": "127.0.0.1:1", "server_host": "127.0.0.1", "server_port": port,
        "log_file_enabled": False, "proxy_ha_sensors_mqtt_discovery_enabled": False,
        "idle_connection_close_seconds": 0,
    }
    try:
        try:
            await proxy.initialize()
        except OSError:
            pass
        else:
            raise AssertionError("Occupied listener port must fail initialize")
        assert all(client._worker_task.done() for client in proxy._clients)
        assert all(session is None for client in proxy._clients for session in client._sessions.values())
        assert proxy._queue._closed and await proxy._queue.depths() == (0, 0)
        assert set(boundary.deregistered_endpoints) == set(boundary.endpoints)
        assert set(boundary.deregistered_routes) == set(boundary.routes)
        assert not proxy._runner.sites
        return {"appdaemon_methods": "awaitable" if awaitable else "direct",
                "occupied_port_cleanup": "passed"}
    finally:
        await proxy.terminate()
        await occupied.cleanup()


async def main():
    assert getattr(aiohttp, "__file__", None) and getattr(hassapi, "__file__", None), "Real packages required"
    cases = [await exercise(False), await exercise(True)]
    failed_initialize = [await exercise_failed_initialize(False), await exercise_failed_initialize(True)]
    remaining = [task for task in asyncio.all_tasks() if task is not asyncio.current_task() and not task.done()]
    await wait_until(lambda: all(task.done() for task in remaining), timeout=1)
    print(json.dumps({"status": "passed", "aiohttp": version("aiohttp"), "appdaemon": version("appdaemon"),
        "scope": "loopback HTTP and mocked AppDaemon methods; no full Home Assistant or HACS install",
        "cases": cases, "failed_initialize": failed_initialize}, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
