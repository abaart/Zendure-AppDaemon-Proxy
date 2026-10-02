# SPDX-License-Identifier: GPL-3.0-only
"""AppDaemon application coordinating requests, sensors and diagnostics."""
from __future__ import annotations

import asyncio
import contextlib
from copy import deepcopy
import html
import inspect
import json
from pathlib import Path
import re
import time
from typing import Any

from aiohttp import web
import appdaemon.plugins.hass.hassapi as hass

from zendure_proxy_config import Config, load_config
from zendure_proxy_state import DeviceState, ProxyState
from zendure_proxy_device_client import DeviceClient
from zendure_proxy_queue import RequestQueue
from zendure_proxy_get_handler import execute_get
from zendure_proxy_post_handler import execute_post
from zendure_proxy_standby import manage_standby
from zendure_proxy_power import now
from zendure_proxy_health import cache_is_usable, response_with_proxy_health, eligible_device_indices
from zendure_proxy_anti_pingpong import smart_sample_grid_power, smart_evaluate_window, reserve_discharge_capacity_watts
from zendure_proxy_ha_sensors import build_proxy_ha_sensors
from zendure_proxy_publication import SensorPublications
from zendure_proxy_metrics import MetricsRegistry, render_metrics_dashboard
from zendure_proxy_logging import ProxyFileLogger, render_log_dashboard
from zendure_proxy_mqtt_discovery import mqtt_sensor_config, mqtt_sensor_topics

VERSION = "0.2.0"


def _int(value, default=0):
    try:
        return int(float(str(value)))
    except (ValueError, TypeError, OverflowError):
        return default


def _health_slots(response, key):
    return frozenset(_int(item.get("slot")) for item in response.get("proxyHealth", {}).get(key, []))


def _health_transition_states(response):
    count = _int(response.get("proxyHealth", {}).get("configuredCount"))
    states = {slot: "Healthy" for slot in range(1, count + 1)}
    for key, label in [("recoveringDevices", "Recovering"), ("degradedDevices", "Degraded"), ("deadDevices", "Dead")]:
        for slot in _health_slots(response, key):
            states[slot] = label
    return states


def _changed_health_slots(previous_states, current_states):
    return frozenset(slot for slot, value in current_states.items() if previous_states.get(slot) != value)


def _health_item_for_slot(response, slot):
    for key in ("unhealthyDevices", "excludedDevices", "recoveringDevices", "degradedDevices", "deadDevices"):
        for item in response.get("proxyHealth", {}).get(key, []):
            if _int(item.get("slot")) == slot:
                return item
    return {}


def _health_transition_entity_ids(slots):
    result = {"sensor.proxy_zendure_pool_healthy", "sensor.vermogensopdracht", "sensor.zendure_actief_device"}
    for slot in slots:
        result.update({f"sensor.zendure_{slot}_{suffix}" for suffix in ("health", "laadpercentage", "vermogen_aansturing", "modus", "relais_stand", "kalibratie_bezig", "opslagmodus", "deep_standby", "soc_limiet_status", "omvormer_temperatuur", "offgrid_modus", "serienummer", "ip_adres", "wifi_rssi")})
        result.add(f"sensor.vermogensopdracht_zendure_{slot}")
    return result


def _repeat_payload_has_explicit_zero_power(props):
    return any(key in props and _int(props[key]) == 0 for key in ("inputLimit", "outputLimit"))


def _repeat_payload_power_cmd(props, current_ac_mode):
    mode = _int(props.get("acMode", current_ac_mode))
    return _int(props.get("inputLimit")) if mode == 1 else -_int(props.get("outputLimit")) if mode == 2 else 0


def should_repeat_last_power(state, cfg, *, current_ts=None):
    ts = now() if current_ts is None else current_ts
    props = (state.last_post_payload or {}).get("properties", {})
    command = state.latest_power_cmd
    eligible = eligible_device_indices(state, cfg, current_ts=ts)
    return bool(cfg.manual_mode_repeat and len(eligible) >= 2 and command and props
                and state.latest_power_message_ts > 0 and ts - state.latest_power_message_ts >= 30
                and ts - state.latest_power_repeat_ts >= 20
                and state.latest_get_ts > 0 and ts - state.latest_get_ts <= 10
                and all(_int(props[key]) >= 0 for key in ("inputLimit", "outputLimit") if key in props)
                and not _repeat_payload_has_explicit_zero_power(props)
                and _repeat_payload_power_cmd(props, state.ac_mode) != 0
                and any(dev.soc_limit != (1 if command > 0 else 2) for dev in (state.devices[index] for index in eligible)))


class ZendureProxy(hass.Hass):
    async def initialize(self):
        try:
            await self._initialize_runtime()
        except BaseException:
            with contextlib.suppress(Exception):
                await self.terminate()
            raise

    async def _initialize_runtime(self):
        self._cfg = load_config(self.args)
        devices = []
        for index, ip in enumerate(self._cfg.device_ips):
            dev = DeviceState(ip=ip)
            if index < len(self._cfg.device_power_limits):
                limit = self._cfg.device_power_limits[index]
                dev.configured_charge_max_watts = limit.charge_max_watts
                dev.configured_discharge_max_watts = limit.discharge_max_watts
            devices.append(dev)
        self._state = ProxyState(device_count=len(devices), devices=devices, startup_ts=now(),
                                 equal_mode=self._cfg.equal_mode, always_dual_mode=self._cfg.always_dual_mode)
        self._metrics = MetricsRegistry(len(devices))
        self._queue = RequestQueue()
        self._upstream_lock = asyncio.Lock()
        self._clients = [DeviceClient(ip, self._proxy_log, metrics=self._metrics, device_idx=index,
            request_timeout=self._cfg.zendure_request_timeout,
            separate_get_post_connections=self._cfg.separate_get_post_connections,
            idle_connection_close_seconds=self._cfg.idle_connection_close_seconds)
            for index, ip in enumerate(self._cfg.device_ips)]
        self._file_logger = self._create_file_logger()
        self._mqtt_api = await self._resolve_appdaemon_result(self._get_mqtt_api())
        self._ensure_publication_state()
        await self._restore_metrics_counters_from_ha()
        self._endpoint_handles = []
        for name, callback in (("zendure_proxy_report", self._api_report), ("zendure_proxy_write", self._api_write), ("zendure_proxy", self._api_gielz_compat)):
            handle = await self._resolve_appdaemon_result(self.register_endpoint(callback, name))
            self._endpoint_handles.append(handle)
        self._report_endpoint_handle, self._write_endpoint_handle = self._endpoint_handles[:2]
        self._route_handles = []
        for enabled, callback, route in ((self._cfg.log_dashboard_enabled, self._logs_dashboard, self._cfg.log_dashboard_route),
            (self._cfg.metrics_dashboard_enabled, self._metrics_dashboard, self._cfg.metrics_dashboard_route),
            (self._cfg.diagnostics_dashboard_enabled, self._diagnostics_dashboard, self._cfg.diagnostics_dashboard_route)):
            if enabled:
                self._route_handles.append(await self._resolve_appdaemon_result(self.register_route(callback, route)))
        await self._start_server()
        self._processor_task = asyncio.create_task(self._processor())
        self._timer_handles = []
        callbacks = [(self._standby_check, 10), (self._publish_sensor_heartbeats, 60)]
        if self._cfg.metrics_ha_sensors_enabled:
            callbacks.append((self._publish_metrics_sensors, max(1, min(self._cfg.metrics_ha_sensors_interval, 10))))
        if self._cfg.proxy_ha_sensors_enabled:
            callbacks.append((self._refresh_proxy_ha_sensors, 300))
        if self._cfg.anti_pingpong_enable and self._cfg.anti_pingpong_activation_mode == "smart":
            await self._resolve_anti_pingpong_grid_power_entity()
            callbacks.extend([(self._anti_pingpong_sample_grid_power, self._cfg.anti_pingpong_smart_sample_interval_seconds),
                (self._anti_pingpong_evaluate_smart, self._cfg.anti_pingpong_smart_evaluate_interval_seconds)])
        for callback, interval in callbacks:
            self._timer_handles.append(await self._resolve_appdaemon_result(self.run_every(callback, "now", interval)))
        if self._mqtt_api and hasattr(self._mqtt_api, "listen_event"):
            self._mqtt_connection_handle = await self._resolve_appdaemon_result(self._mqtt_api.listen_event(
                self._mqtt_connection_changed, "MQTT_MESSAGE", state="Connected", topic=None))
        for warning in self._cfg.config_warnings:
            self._proxy_log(warning, level="WARNING")

    async def terminate(self):
        task = getattr(self, "_processor_task", None)
        if task:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        queue = getattr(self, "_queue", None)
        if queue and hasattr(queue, "close"):
            await self._cleanup_runtime_resource("queue", queue.close)
        for dev in getattr(getattr(self, "_state", None), "devices", []):
            if dev.standby_task:
                dev.standby_task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await dev.standby_task
        for handle in getattr(self, "_timer_handles", []):
            if hasattr(self, "cancel_timer"):
                await self._cleanup_runtime_resource("timer", self.cancel_timer, handle)
        for handle in getattr(self, "_endpoint_handles", [getattr(self, "_report_endpoint_handle", None), getattr(self, "_write_endpoint_handle", None)]):
            if handle is not None:
                await self._cleanup_runtime_resource("endpoint", self.deregister_endpoint, handle)
        for handle in getattr(self, "_route_handles", []):
            if hasattr(self, "deregister_route"):
                await self._cleanup_runtime_resource("route", self.deregister_route, handle)
        if getattr(self, "_mqtt_connection_handle", None) and hasattr(self._mqtt_api, "cancel_listen_event"):
            await self._cleanup_runtime_resource("MQTT listener", self._mqtt_api.cancel_listen_event, self._mqtt_connection_handle)
        if getattr(self, "_runner", None):
            await self._cleanup_runtime_resource("HTTP server", self._runner.cleanup)
        await asyncio.gather(*(self._cleanup_runtime_resource("device client", client.close) for client in getattr(self, "_clients", [])))
        if getattr(self, "_file_logger", None):
            await self._cleanup_runtime_resource("file logger", self._file_logger.close)

    async def _cleanup_runtime_resource(self, label, callback, *args):
        try:
            await self._resolve_appdaemon_result(callback(*args))
        except Exception as exc:
            self._proxy_log(f"Runtime resource close failed: resource={label} error={exc}", level="WARNING")

    def _create_file_logger(self):
        if not self._cfg.log_file_enabled:
            return None
        location = self._cfg.log_file_path or str(Path(getattr(self, "config_dir", "/config/appdaemon")) / "logs" / "zendure_proxy.log")
        return ProxyFileLogger(location, self._cfg.log_file_max_bytes, self._cfg.log_file_backup_count)

    def _proxy_log(self, message, level="INFO", **kwargs):
        self.log(message, level=level, **kwargs)
        if getattr(self, "_file_logger", None):
            self._file_logger.log(message, level)

    def _debug_capture_payload(self, message_type, direction, payload):
        if getattr(self._cfg, "debug_payload_capture_enabled", False) and getattr(self, "_file_logger", None):
            self._file_logger.log(json.dumps({"debug_message_type": message_type, "debug_direction": direction, "payload": payload}, default=str), "DEBUG")

    @staticmethod
    async def _resolve_appdaemon_result(value):
        return await value if inspect.isawaitable(value) else value

    @staticmethod
    def _ha_sensor_state(value):
        return "unknown" if value is None else str(value)

    @staticmethod
    def _ha_attribute_is_true(marker):
        return marker is True or isinstance(marker, str) and marker.lower() == "true"

    async def _get_entity_state(self, entity_id):
        result = await self._resolve_appdaemon_result(self.get_state(entity_id, attribute="all"))
        return result if isinstance(result, dict) else None

    def _entity_is_proxy_managed(self, entity_id, state):
        attrs = (state or {}).get("attributes", {})
        return entity_id in self._proxy_ha_sensor_owned_entities or self._ha_attribute_is_true(attrs.get("zendure_proxy_managed"))

    def _ensure_publication_state(self):
        for name, factory in (("_sensor_publications", SensorPublications), ("_sensor_publication_lock", asyncio.Lock),
             ("_proxy_ha_sensor_owned_entities", set), ("_mqtt_configurations", dict), ("_restored_entities", set)):
            if not hasattr(self, name):
                setattr(self, name, factory())

    async def _publish_proxy_ha_sensors(self, response, *, entity_ids=None, force_existing_entities=False, heartbeat_lead_seconds=0):
        if not getattr(self._cfg, "proxy_ha_sensors_enabled", True):
            return
        self._ensure_publication_state()
        async with self._sensor_publication_lock:
            await self._publish_proxy_sensor_values(response, entity_ids=entity_ids,
                force_existing_entities=force_existing_entities, heartbeat_lead_seconds=heartbeat_lead_seconds)

    async def _publish_proxy_sensor_values(self, response, *, entity_ids, force_existing_entities, heartbeat_lead_seconds):
        order = await self._resolve_appdaemon_result(self.get_state("input_text.zendure_2400_ac_batterij_volgorde"))
        sensors = build_proxy_ha_sensors(response, battery_order_raw=order)
        ready = await self._mqtt_ready()
        for entity_id, (value, attributes) in sensors.items():
            if entity_ids is not None and entity_id not in entity_ids:
                continue
            try:
                existing = await self._get_entity_state(entity_id)
                owned = self._entity_is_proxy_managed(entity_id, existing)
                restored = self._ha_attribute_is_true((existing or {}).get("attributes", {}).get("restored")) or self._ha_attribute_is_true((existing or {}).get("attributes", {}).get("proxy_restored_entity")) or entity_id in self._restored_entities
                external = bool(existing) and not owned and not restored
                if external and getattr(self._cfg, "proxy_ha_sensors_skip_existing", True) and not force_existing_entities:
                    continue
                state = self._ha_sensor_state(value)
                attrs = dict(attributes)
                attrs["proxy_updated_at"] = str(int(time.time()))
                if not external:
                    attrs["zendure_proxy_managed"] = True
                if restored:
                    attrs["proxy_restored_entity"] = True
                ts = now()
                if not self._sensor_publications.due(entity_id, state, attrs, ts, heartbeat_lead_seconds=heartbeat_lead_seconds):
                    continue
                if ready and not restored and not external:
                    await self._publish_proxy_mqtt_sensor(entity_id, state, attrs, response, attrs["proxy_updated_at"])
                else:
                    await self._resolve_appdaemon_result(self.set_state(entity_id, state=state, attributes=attrs,
                        replace=not external, check_existence=False))
                self._sensor_publications.record(entity_id, state, attrs, ts)
                if not external:
                    self._proxy_ha_sensor_owned_entities.add(entity_id)
                if restored:
                    self._restored_entities.add(entity_id)
            except Exception as exc:
                self._proxy_log(f"Proxy sensor publish failed: entity_id={entity_id} error={exc}", level="WARNING")

    async def _publish_report_sensors(self, response, *, force_health_sensor_refresh=False):
        await self._publish_proxy_ha_sensors(response)
        await self._publish_health_transition_sensors(response, force_all=force_health_sensor_refresh)

    async def _publish_health_transition_sensors(self, response, *, force_all=False):
        if "proxyHealth" not in response:
            return
        current = _health_transition_states(response)
        previous = getattr(self, "_health_states", None)
        changed = frozenset(current) if previous is None else _changed_health_slots(previous, current)
        if changed:
            await self._publish_proxy_ha_sensors(response, entity_ids=_health_transition_entity_ids(changed), force_existing_entities=True)
            self._log_health_transitions(response, changed, current, previous)
        self._health_states = current

    def _log_health_transitions(self, response, changed_slots, current_states, previous_states):
        for slot in sorted(changed_slots):
            old = (previous_states or {}).get(slot, "Healthy")
            label = current_states[slot]
            if old == label:
                continue
            item = _health_item_for_slot(response, slot)
            props = response.get("properties", {})
            action = "recovered" if label == "Healthy" else label.lower()
            self._proxy_log(f"Zendure pool {action}: slot={slot} serial={item.get('serialNumber', props.get(f'sn_{slot}', ''))} previous_health_state={old} last_get_error={item.get('lastGetError', '')}", level="INFO" if label == "Healthy" else "WARNING")

    async def _publish_proxy_mqtt_sensor(self, entity_id, state, attributes, response, updated_at):
        self._ensure_publication_state()
        prefix = getattr(self._cfg, "proxy_ha_sensors_mqtt_discovery_prefix", "homeassistant")
        state_prefix = getattr(self._cfg, "proxy_ha_sensors_mqtt_state_prefix", "zendure_proxy")
        config = mqtt_sensor_config(entity_id, attributes, prefix, state_prefix)
        config["device"]["sw_version"] = response.get("proxyVersion", VERSION)
        topics = mqtt_sensor_topics(entity_id, prefix, state_prefix)
        retain = getattr(self._cfg, "proxy_ha_sensors_mqtt_retain", True)
        serialized = json.dumps(config, sort_keys=True)
        if self._mqtt_configurations.get(entity_id) != serialized:
            await self._resolve_appdaemon_result(self._mqtt_api.mqtt_publish(topics[0], serialized, retain=retain))
            self._mqtt_configurations[entity_id] = serialized
        await self._resolve_appdaemon_result(self._mqtt_api.mqtt_publish(topics[1], self._ha_sensor_state(state), retain=retain))
        await self._resolve_appdaemon_result(self._mqtt_api.mqtt_publish(topics[2], json.dumps({**attributes, "proxy_updated_at": updated_at}, default=str), retain=retain))

    async def _get_mqtt_api(self):
        if not getattr(self._cfg, "proxy_ha_sensors_mqtt_discovery_enabled", True):
            return None
        try:
            return await self._resolve_appdaemon_result(self.get_plugin_api("MQTT"))
        except Exception:
            return None

    async def _mqtt_ready(self):
        mqtt = getattr(self, "_mqtt_api", None)
        if not mqtt or not getattr(self._cfg, "proxy_ha_sensors_mqtt_discovery_enabled", True):
            return False
        connected = True
        try:
            if hasattr(mqtt, "is_client_connected"):
                connected = bool(await self._resolve_appdaemon_result(mqtt.is_client_connected()))
        except Exception:
            connected = False
        if connected and getattr(self, "_mqtt_connected", None) is False:
            await self._mqtt_connection_changed(None, {}, {})
        self._mqtt_connected = connected
        return connected

    async def _mqtt_connection_changed(self, _event, _data, _kwargs):
        self._ensure_publication_state()
        for entity_id in list(self._mqtt_configurations):
            self._sensor_publications.published.pop(entity_id, None)
        self._mqtt_configurations.clear()
        self._mqtt_connected = True

    async def _publish_metrics_sensors(self, _kwargs=None):
        if getattr(self._cfg, "metrics_ha_sensors_enabled", True):
            self._ensure_publication_state()
            async with self._sensor_publication_lock:
                await self._publish_metrics_sensor_values()

    async def _publish_metrics_sensor_values(self, *, heartbeat_lead_seconds=0):
        for entity_id, (value, attrs) in self._metrics.flat_ha_sensors().items():
            state = self._ha_sensor_state(value)
            ts = now()
            if self._sensor_publications.due(entity_id, state, attrs, ts, heartbeat_lead_seconds=heartbeat_lead_seconds):
                try:
                    await self._resolve_appdaemon_result(self.set_state(entity_id, state=state, attributes=attrs, replace=True, check_existence=False))
                    self._sensor_publications.record(entity_id, state, attrs, ts)
                except Exception as exc:
                    self._proxy_log(f"Metrics sensor publish failed: entity_id={entity_id} error={exc}", level="WARNING")

    async def _restore_metrics_counters_from_ha(self):
        states = {}
        for entity_id in self._metrics.counter_sensor_entity_ids():
            states[entity_id] = await self._resolve_appdaemon_result(self.get_state(entity_id))
        self._metrics.restore_counters_from_sensors(states)

    async def _publish_sensor_heartbeats(self, _kwargs=None):
        response = getattr(self._state, "last_get_response", None)
        if response:
            await self._publish_proxy_ha_sensors(response, heartbeat_lead_seconds=60)
        if getattr(self._cfg, "metrics_ha_sensors_enabled", True):
            self._ensure_publication_state()
            async with self._sensor_publication_lock:
                await self._publish_metrics_sensor_values(heartbeat_lead_seconds=60)

    async def _refresh_proxy_ha_sensors(self, _kwargs=None):
        if getattr(self, "_proxy_ha_sensor_refresh_in_progress", False):
            return
        self._proxy_ha_sensor_refresh_in_progress = True
        try:
            self._ensure_upstream_lock()
            async with self._upstream_lock:
                response = await self._fetch_report()
            if response:
                await self._publish_report_sensors(response)
                self._mark_passive_zero_timestamps()
                await self._standby_check()
        finally:
            self._proxy_ha_sensor_refresh_in_progress = False

    def _ensure_upstream_lock(self):
        if not hasattr(self, "_upstream_lock"):
            self._upstream_lock = asyncio.Lock()

    async def _fetch_report(self):
        self._state.get_refresh_in_progress = True
        try:
            response = await execute_get(self._clients, self._state, self._cfg, self._proxy_log, metrics=getattr(self, "_metrics", None))
            self._state.last_upstream_get_ts = now()
            if response and not response.get("proxyHealth", {}).get("servedFromCache", False):
                self._state.latest_get_ts = now()
                self._state.last_get_response = deepcopy(response)
            return response
        finally:
            self._state.get_refresh_in_progress = False

    async def _start_server(self):
        application = web.Application()
        for prefix in ("", "/endpoint"):
            application.router.add_get(prefix + "/properties/report", self._handle_get)
            application.router.add_post(prefix + "/properties/write", self._handle_post)
        self._runner = web.AppRunner(application)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self._cfg.server_host, self._cfg.server_port)
        await site.start()

    async def _handle_get(self, request):
        self._remember_local_proxy_url(request)
        data, status = await self._execute_report_request()
        return web.json_response(data, status=status)

    async def _handle_post(self, request):
        self._remember_local_proxy_url(request)
        try:
            payload = await request.json()
        except (ValueError, TypeError):
            return web.json_response({"error": "Invalid JSON payload"}, status=400)
        data, status = await self._execute_write_request(payload)
        return web.json_response(data, status=status)

    async def _api_report(self, _args, _kwargs):
        return await self._execute_report_request()

    async def _api_write(self, json_obj, _kwargs):
        return await self._execute_write_request(json_obj)

    @staticmethod
    def _gielz_compat_path(json_obj, request):
        query = getattr(request, "query", {})
        return str(query.get("path", json_obj.get("path", "")) if isinstance(json_obj, dict) else query.get("path", "")).strip("/")

    async def _api_gielz_compat(self, json_obj, kwargs):
        request = kwargs.get("request")
        path = self._gielz_compat_path(json_obj, request)
        if path in ("properties/report", "endpoint/properties/report"):
            return await self._execute_report_request()
        if path in ("properties/write", "endpoint/properties/write"):
            return await self._execute_write_request(json_obj)
        return {"error": f"Unsupported Zendure API path: {path}"}, 404

    def _remember_local_proxy_url(self, request):
        value = str(getattr(request, "url", ""))
        for client in self._clients:
            client.set_local_proxy_url(value)

    async def _execute_report_request(self):
        start = now()
        self._metrics.start_incoming("GET")
        self._state.counter_get_received += 1
        self._state.last_ha_get_ts = start
        status, timeout = 200, False
        try:
            if cache_is_usable(self._state, self._cfg, current_ts=start) and self._state.last_upstream_get_ts > 0 and start - self._state.last_upstream_get_ts <= self._cfg.get_rate_limit_window:
                response = self._cached_report("rate_limited", start)
                self._metrics.record_incoming_get_rate_limited_cache()
            else:
                future = await self._queue.enqueue_get()
                await self._record_incoming_depths()
                try:
                    response = await asyncio.wait_for(asyncio.shield(future), self._cfg.ha_get_response_timeout)
                except asyncio.TimeoutError:
                    future.cancel()
                    timeout = True
                    self._state.counter_get_timeouts += 1
                    response = self._cached_report("ha_get_timeout", now())
                except Exception as exc:
                    status = 502
                    self._proxy_log(f"GET processing failed: {exc}", level="WARNING")
                    return {"error": "Zendure GET processing failed"}, status
                if response is None:
                    status = 504
                    return {"error": "Cached GET response expired"}, status
            await self._publish_report_sensors(response)
            self._debug_capture_payload("GET", "To Home Assistant", response)
            self._state.counter_get_replies += 1
            return response, status
        finally:
            self._metrics.finish_incoming("GET", (now() - start) * 1000, status, timeout)

    def _cached_report(self, reason, ts):
        if not cache_is_usable(self._state, self._cfg, current_ts=ts):
            return None
        return response_with_proxy_health(self._state.last_get_response, self._state, self._cfg,
            served_from_cache=True, reason=reason, refresh_in_progress=self._state.get_refresh_in_progress, current_ts=ts)

    async def _execute_write_request(self, payload):
        if not isinstance(payload, dict):
            return {"error": "JSON payload must be an object"}, 400
        start = now()
        self._metrics.start_incoming("POST")
        self._state.counter_post_received += 1
        status, timeout = 200, False
        try:
            if not self._state.devices:
                self._state.counter_config_drop += 1
                status = 503
                return {"error": "No Zendure devices configured"}, status
            await self._ensure_serial_numbers()
            if any(not dev.sn for dev in self._state.devices):
                self._state.counter_serial_missing_drop += 1
                status = 503
                return {"error": "Zendure serial number unavailable"}, status
            future = await self._queue.enqueue_post(payload)
            await self._record_incoming_depths()
            try:
                response = await asyncio.wait_for(asyncio.shield(future), max(self._cfg.ha_get_response_timeout, self._cfg.zendure_request_timeout))
            except asyncio.TimeoutError:
                future.cancel()
                status, timeout = 504, True
                return {"error": "Zendure POST response timeout"}, status
            except Exception as exc:
                status = 502
                self._proxy_log(f"POST processing failed: {exc}", level="WARNING")
                return {"error": "Zendure POST processing failed"}, status
            self._state.counter_post_replies += 1
            return response, status
        finally:
            self._metrics.finish_incoming("POST", (now() - start) * 1000, status, timeout)

    async def _record_incoming_depths(self):
        gets, posts = await self._queue.depths()
        self._metrics.set_incoming_queue_depth(gets, posts)

    def _mark_passive_zero_timestamps(self):
        for index, device in enumerate(self._state.devices):
            if index not in self._state.devices_active_idx and device.latest_power_cmd == 0 and device.latest_power_cmd_zero_ts == 0:
                device.latest_power_cmd_zero_ts = now()

    async def _standby_check(self, _kwargs=None):
        await manage_standby(self._state, self._clients, self._state.ac_mode,
            [dev.latest_power_cmd for dev in self._state.devices], self._cfg, self._proxy_log)

    async def _processor(self):
        self._ensure_upstream_lock()
        self._processor_waiters = []
        try:
            while True:
                gets, posts = await self._queue.drain()
                self._processor_waiters = list(gets) + [future for _, future, _ in posts] + [future for _, _, skipped in posts for future in skipped]
                try:
                    await self._process_batch(gets, posts)
                except asyncio.CancelledError:
                    for future in self._processor_waiters:
                        if not future.done():
                            future.cancel()
                    raise
                except Exception as exc:
                    self._proxy_log(f"Request batch failed: {exc}", level="ERROR")
                    for future in self._processor_waiters:
                        if not future.done():
                            future.set_exception(RuntimeError(str(exc)))
                finally:
                    self._processor_waiters = []
                await self._record_incoming_depths()
        except asyncio.CancelledError:
            for future in self._processor_waiters:
                if not future.done():
                    future.cancel()
            return

    async def _process_batch(self, gets, posts):
        self._metrics.record_queue_batch(get_count=len(gets), post_group_count=len(posts),
            coalesced_gets=max(0, len(gets)-1), deduplicated_posts=sum(len(group[2]) for group in posts),
            deduplicated_groups=sum(bool(group[2]) for group in posts))
        coalesced = max(0, len(gets) - 1)
        duplicates = sum(len(group[2]) for group in posts)
        if coalesced:
            self._proxy_log(f"GET coalescing: combined {len(gets)} requests", level="WARNING")
        if duplicates:
            self._proxy_log(f"POST deduplication: replaced {duplicates} queued requests", level="WARNING")
        async with self._upstream_lock:
            if gets:
                response = await self._fetch_report()
                late = await self._queue.drain_gets_nowait()
                gets.extend(late)
                self._processor_waiters.extend(late)
                for future in gets:
                    if not future.done():
                        future.set_result(response)
                if response:
                    await self._publish_report_sensors(response)
                    self._mark_passive_zero_timestamps()
                    await self._standby_check()
                    if should_repeat_last_power(self._state, self._cfg):
                        await execute_post(deepcopy(self._state.last_post_payload), self._clients,
                            self._state, self._cfg, self._proxy_log, is_repeat=True)
            for payload, future, skipped in posts:
                response = await execute_post(payload, self._clients, self._state, self._cfg, self._proxy_log)
                for waiter in [future, *skipped]:
                    if not waiter.done():
                        waiter.set_result(response)

    async def _init_serial_numbers(self):
        await self._ensure_serial_numbers()

    async def _ensure_serial_numbers(self):
        self._ensure_upstream_lock()
        async with self._upstream_lock:
            pending = [(index, client) for index, client in enumerate(self._clients) if not self._state.devices[index].sn]
            if pending:
                results = await asyncio.gather(*(asyncio.wait_for(client.get(), getattr(getattr(self, "_cfg", None), "zendure_request_timeout", 60.0)) for _, client in pending), return_exceptions=True)
                for (index, _), result in zip(pending, results):
                    if isinstance(result, dict) and result.get("sn"):
                        self._state.devices[index].sn = str(result["sn"])
                        self._state.devices[index].last_response = result

    @staticmethod
    def _valid_ha_entity_id(value):
        return isinstance(value, str) and bool(re.fullmatch(r"[a-z_][a-z0-9_]*\.[a-z0-9_]+", value))

    async def _ha_entity_exists(self, entity_id):
        return await self._resolve_appdaemon_result(self.get_state(entity_id)) is not None

    async def _resolve_anti_pingpong_grid_power_entity(self):
        candidates = []
        configured = self._cfg.anti_pingpong_grid_power_entity
        if configured:
            candidates.append((configured, "configuration"))
        if self._cfg.anti_pingpong_grid_power_autodiscover:
            configured_input = await self._resolve_appdaemon_result(self.get_state("input_text.afwijkende_p1_sensor"))
            if isinstance(configured_input, str):
                candidates.append((configured_input.strip(), "input_text.afwijkende_p1_sensor"))
            candidates.append(("sensor.homewizard_p1_vermogen", "sensor.homewizard_p1_vermogen"))
        for entity, source in candidates:
            if self._valid_ha_entity_id(entity) and await self._ha_entity_exists(entity):
                self._state.anti_pingpong_grid_power_entity_resolved = entity
                self._state.anti_pingpong_grid_power_entity_source = source
                return entity
        self._state.anti_pingpong_grid_power_entity_resolved = ""
        self._state.anti_pingpong_grid_power_entity_source = ""
        return ""

    async def _anti_pingpong_sample_grid_power(self, _kwargs=None):
        entity = self._state.anti_pingpong_grid_power_entity_resolved or await self._resolve_anti_pingpong_grid_power_entity()
        if entity:
            value = await self._resolve_appdaemon_result(self.get_state(entity))
            try:
                value = float(value)
            except (TypeError, ValueError):
                return
            smart_sample_grid_power(self._state, self._cfg, value if self._cfg.anti_pingpong_grid_power_import_positive else -value, now())

    async def _anti_pingpong_evaluate_smart(self, _kwargs=None):
        eligible = eligible_device_indices(self._state, self._cfg)
        capacity = reserve_discharge_capacity_watts(self._state, self._cfg, eligible)
        smart_evaluate_window(self._state, self._cfg, capacity, now())

    async def _logs_dashboard(self, request, _kwargs):
        logger = getattr(self, "_file_logger", None)
        text = logger.read_tail(self._cfg.log_dashboard_lines) if logger else "File logging disabled"
        if getattr(request, "query", {}).get("download"):
            return web.Response(text=logger.read_all() if logger else text, content_type="text/plain")
        return web.Response(text=render_log_dashboard("Zendure AppDaemon Proxy", text, "?download=1"), content_type="text/html")

    async def _metrics_dashboard(self, _request, _kwargs):
        return web.Response(text=render_metrics_dashboard("Zendure AppDaemon Proxy", self._metrics.snapshot(), self._cfg.metrics_dashboard_refresh), content_type="text/html")

    def _diagnostics_snapshot(self):
        return {"version": VERSION, "devices": [{"ip": dev.ip, "sn": dev.sn, "powerCommand": dev.latest_power_cmd} for dev in self._state.devices],
                "warnings": self._diagnostics_warnings(), "counters": {name: value for name, value in vars(self._state).items() if name.startswith("counter_")}}

    def _render_diagnostics_dashboard(self):
        data = html.escape(json.dumps(self._diagnostics_snapshot(), indent=2, default=str))
        return f'<!doctype html><html><head><meta name="viewport" content="width=device-width"></head><body><h1>Zendure AppDaemon Proxy diagnostics</h1><pre>{data}</pre><form method="post"><button name="action" value="reset">Reset request counters</button></form></body></html>'

    async def _diagnostics_dashboard(self, request, _kwargs):
        if getattr(request, "method", "GET") == "POST":
            form = await request.post()
            if form.get("action") == "reset":
                self._reset_node_red_counters()
        return web.Response(text=self._render_diagnostics_dashboard(), content_type="text/html")

    def _diagnostics_warnings(self):
        warnings = []
        for label, values in [("chargeMaxLimit", [dev.charge_max_limit for dev in self._state.devices]),
             ("inverseMaxPower", [dev.inverse_max_power for dev in self._state.devices]),
             ("minSoc", [(dev.last_response or {}).get("properties", {}).get("minSoc") for dev in self._state.devices]),
             ("socSet", [(dev.last_response or {}).get("properties", {}).get("socSet") for dev in self._state.devices])]:
            if len(set(values)) > 1:
                warnings.append(f"{label} differs between devices: {values}")
        if now() - self._state.latest_get_ts > 10:
            warnings.append("No recent GET within 10 seconds")
        return warnings

    def _log_diagnostics_warnings(self):
        for warning in self._diagnostics_warnings():
            self._proxy_log(warning, level="WARNING")

    def _reset_node_red_counters(self):
        for name in vars(self._state):
            if name.startswith("counter_"):
                setattr(self._state, name, [0] * self._state.device_count if name == "counter_missing" else 0)
