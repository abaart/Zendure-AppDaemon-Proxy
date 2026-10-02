# SPDX-License-Identifier: GPL-3.0-only
"""Read configured devices and produce a report with stable numbered slots."""
from __future__ import annotations

import asyncio
import copy
import time
import math
from typing import Any

from zendure_proxy_health import (cache_is_usable, eligible_device_indices,
    record_get_results, response_with_proxy_health, refresh_device_health)
from zendure_proxy_power import now, PROXY_VERSION
from zendure_proxy_anti_pingpong import maximum_control_power_watts


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _reported_power_cmd(props: dict) -> int:
    mode = _int(props.get("acMode"))
    return _int(props.get("inputLimit")) if mode == 1 else -_int(props.get("outputLimit")) if mode == 2 else 0


def _update_device_state(idx: int, data: dict, state) -> None:
    dev = state.devices[idx]
    dev.last_response = copy.deepcopy(data)
    dev.sn = data.get("sn") or dev.sn
    fields = {
        "electricLevel": "electric_level", "socStatus": "soc_status",
        "smartMode": "smart_mode", "socLimit": "soc_limit",
        "chargeMaxLimit": "charge_max_limit", "inverseMaxPower": "inverse_max_power",
        "gridOffMode": "gridoff_mode",
    }
    props = data.get("properties") or {}
    for key, attr in fields.items():
        if key in props:
            setattr(dev, attr, _int(props[key], getattr(dev, attr)))


def _record_relay_measurements(results, metrics) -> None:
    if metrics is None or not callable(getattr(metrics, "record_device_relay_measurement", None)):
        return
    for idx, result in enumerate(results):
        if result is not None:
            props = result.get("properties") or {}
            metrics.record_device_relay_measurement(idx, any(_int(props.get(key)) > 0 for key in ("outputPackPower", "packInputPower")))


def _record_last_known_fallbacks(results, state, metrics) -> None:
    if metrics is None or not callable(getattr(metrics, "record_device_get_last_known_fallback", None)):
        return
    for idx, result in enumerate(results):
        if result is None and idx < len(state.devices) and state.devices[idx].last_response is not None:
            metrics.record_device_get_last_known_fallback(idx)


def _recalculate_aggregate_device_limits(state, cfg, *, current_ts: float) -> None:
    eligible = eligible_device_indices(state, cfg, current_ts=current_ts)
    devices = [state.devices[idx] for idx in eligible]
    if devices:
        state.max_power_in = min(dev.effective_charge_max_watts for dev in devices)
        state.max_power_out = min(dev.effective_discharge_max_watts for dev in devices)
        props = [dev.last_response.get("properties", {}) for dev in devices if dev.last_response]
        if props:
            state.min_soc = max(_int(p.get("minSoc"), state.min_soc) for p in props)
            state.soc_set = min(_int(p.get("socSet"), state.soc_set) for p in props)
            modes = {_int(p.get("acMode")) for p in props}
            state.ac_mode_inconsistent = len(modes) > 1
            if not any(dev.latest_ac_mode_cmd for dev in devices):
                state.ac_mode = _int(props[0].get("acMode"))


async def execute_get(clients, state, cfg, logger, metrics=None):
    responses = await asyncio.gather(*(client.get() for client in clients), return_exceptions=True)
    results = [result if isinstance(result, dict) and isinstance(result.get("properties"), dict) and result["properties"] else None for result in responses]
    ts = now()
    for idx, result in enumerate(results):
        if idx >= len(state.devices):
            break
        if result is not None:
            _update_device_state(idx, result, state)
        else:
            if idx < len(state.counter_missing):
                state.counter_missing[idx] += 1
            logger(f"Zendure {idx + 1} GET returned no usable report", level="WARNING")
    _record_relay_measurements(results, metrics)
    _record_last_known_fallbacks(results, state, metrics)
    record_get_results(state, cfg, results, current_ts=ts)
    _recalculate_aggregate_device_limits(state, cfg, current_ts=ts)
    if not any(result is not None for result in results):
        if cache_is_usable(state, cfg, current_ts=ts):
            return response_with_proxy_health(state.last_get_response, state, cfg,
                served_from_cache=True, reason="upstream_failed", refresh_in_progress=False, current_ts=ts)
        return None
    reason = "fresh" if all(result is not None for result in results) else "upstream_partial"
    state.latest_get_ts = ts
    response = build_combined_response(results, state, cfg, reason=reason)
    state.last_get_response = copy.deepcopy(response)
    return response


def _slot_response(idx, results, state):
    if idx < len(results) and isinstance(results[idx], dict):
        return results[idx]
    return (state.devices[idx].last_response or {}) if idx < len(state.devices) else {}


def _idx_mask(indices: list[int]) -> int:
    return sum(1 << idx for idx in set(indices) if idx >= 0)


def _active_device_mask(state, *, latest_power_cmd, device_power_cmds, soc_limit, output_pack_power, pack_input_power):
    available = {idx for idx, dev in enumerate(state.devices[:state.device_count]) if dev.latest_get_included}
    actual = output_pack_power or pack_input_power
    if not latest_power_cmd or (soc_limit and not actual):
        return 0
    commanded = {idx for idx, cmd in enumerate(device_power_cmds) if cmd} & available
    if state.latest_power_cmd and state.device_active_count > 1:
        commanded |= set(state.devices_active_idx) & available
    if not commanded:
        commanded = set(state.devices_active_idx) & available
    if not commanded and state.single_mode_active_device in available:
        commanded.add(state.single_mode_active_device)
    return _idx_mask(list(commanded))


def _transition_recent(state, cfg):
    ts = now()
    return any(start > 0 and ts - start < cfg.transition_timer for start in (
        state.transition_start_ts, state.single_to_dual_transition_start_ts,
        state.forced_dual_transition_start_ts))


def _mode_enabled(state, cfg, state_name, config_name=None):
    selected = bool(getattr(state, state_name, False))
    if state_name in getattr(state, "_runtime_mode_overrides", ()):
        return selected
    return selected or bool(getattr(cfg, config_name or state_name, False))


def _relay_saver_remaining_seconds(state, cfg, current_ts):
    return max(0, math.ceil(max((state.relay_saver_until_ts_by_idx.get(idx, current_ts) for idx in state.relay_saver_paused_idx), default=current_ts) - current_ts)) if cfg.relay_saver_enable else 0


def _effective_relay_saver_min_power(state, cfg):
    return maximum_control_power_watts(state, cfg.relay_saver_min_power_watts)


def build_combined_response(results, state, cfg, *, reason="fresh"):
    count = state.device_count
    refresh_device_health(state, cfg)
    records = [_slot_response(idx, results, state) for idx in range(count)]
    eligible = [idx for idx in range(count) if idx < len(state.devices) and state.devices[idx].latest_get_included]
    usable = [records[idx].get("properties", {}) for idx in eligible if records[idx]]
    props = {}
    total_keys = {"chargeMaxLimit", "inverseMaxPower", "inputLimit", "outputLimit", "outputPackPower", "packInputPower", "gridInputPower", "outputHomePower", "solarInputPower", "gridOffPower", "packNum", "solarPower1", "solarPower2", "solarPower3", "solarPower4"}
    average_keys = {"BatVolt", "remainOutTime", "electricLevel", "gridReverse"}
    minimum_keys = {"socStatus", "rssi"}
    ambiguous_keys = {"pass", "batCalTime"}
    keys = set().union(*(p.keys() for p in usable)) if usable else set()
    for key in keys:
        vals = [_int(p.get(key)) for p in usable]
        if key in total_keys:
            props[key] = sum(vals)
        elif key in average_keys:
            props[key] = math.floor(sum(vals) / len(vals))
        elif key in minimum_keys:
            props[key] = min(vals)
        elif key in ambiguous_keys:
            props[key] = vals[0] if len(set(vals)) == 1 else -1
        elif key == "gridOffMode":
            props[key] = 0 if 0 in vals else 1 if 1 in vals else 2
        elif key == "smartMode":
            damper_active = state.dualmode_damper_active and _mode_enabled(state, cfg, "dualmode_damper_enabled", "damper_enable")
            temporary = _transition_recent(state, cfg) or any(dev.standby_device for dev in state.devices) or damper_active or state.anti_pingpong_active
            props[key] = max(vals) if temporary else math.prod(vals)
        elif key == "socLimit":
            props[key] = vals[0] if len(set(vals)) == 1 else 0
        elif key == "socSet":
            props[key] = min(vals)
        elif key == "acMode":
            props[key] = vals[0]
        else:
            props[key] = max(vals)
    for key in ("gridReverse", "pass", "batCalTime", "pvStatus", "acStatus", "dcStatus"):
        if not all(key in p for p in usable):
            props.pop(key, None)
    for key, attr in (("chargeMaxLimit", "effective_charge_max_watts"), ("inverseMaxPower", "effective_discharge_max_watts")):
        props[key] = sum(getattr(state.devices[idx], attr) for idx in eligible)
    for key, command, effective in (("inputLimit", "input_limit", "input_limit_effective"), ("outputLimit", "output_limit", "output_limit_effective"), ("chargeMaxLimit", "charge_max_limit_cmd", "charge_max_limit_effective"), ("inverseMaxPower", "inverse_max_power_cmd", "inverse_max_power_effective")):
        if props.get(key) == getattr(state, effective) and getattr(state, command) != getattr(state, effective):
            props[key] = getattr(state, command)
    pack_net = props.get("packInputPower", 0) - props.get("outputPackPower", 0)
    grid_net = props.get("gridInputPower", 0) - props.get("outputHomePower", 0)
    props["packInputPower"], props["outputPackPower"] = max(0, pack_net), max(0, -pack_net)
    props["gridInputPower"], props["outputHomePower"] = max(0, grid_net), max(0, -grid_net)
    if props.get("acMode") == 2 and 0 < props["outputPackPower"] < 100:
        props["outputPackPower"] = 0
    commands = []
    for idx, record in enumerate(records):
        p = record.get("properties") or {}
        dev = state.devices[idx]
        commands.append(dev.latest_power_cmd if state.latest_power_cmd or state.latest_power_message_ts else _reported_power_cmd(p))
    latest = state.latest_power_cmd if state.latest_power_cmd or state.latest_power_message_ts else sum(commands)
    props.update({
        "proxyVersion": PROXY_VERSION,
        "latestPowerCmd": latest, "dualModeDamper": int(_mode_enabled(state, cfg, "dualmode_damper_enabled", "damper_enable")),
        "equalMode": int(_mode_enabled(state, cfg, "equal_mode")), "alwaysDualMode": int(_mode_enabled(state, cfg, "always_dual_mode")),
        "activeDevice": _active_device_mask(state, latest_power_cmd=latest, device_power_cmds=commands,
            soc_limit=props.get("socLimit", 0), output_pack_power=props.get("outputPackPower", 0), pack_input_power=props.get("packInputPower", 0)),
        "antiPingpong": int(cfg.anti_pingpong_enable),
        "antiPingpongActive": int(state.anti_pingpong_active),
        "antiPingpongActivationMode": cfg.anti_pingpong_activation_mode,
        "antiPingpongServiceDevice": _idx_mask(state.anti_pingpong_service_idx),
        "antiPingpongPausedDevice": _idx_mask(state.anti_pingpong_paused_idx),
        "antiPingpongReservePower": state.anti_pingpong_reserve_power_watts,
        "antiPingpongServiceBoost": state.anti_pingpong_reserve_power_watts,
        "antiPingpongModeSwitchPauseSeconds": cfg.anti_pingpong_mode_switch_delay_seconds,
        "antiPingpongGridPowerEntitySource": state.anti_pingpong_grid_power_entity_source,
        "antiPingpongReason": state.anti_pingpong_last_reason,
        "antiPingpongReserveDevice": _idx_mask(state.anti_pingpong_reserve_idx),
        "antiPingpongDelayedDevice": _idx_mask(state.anti_pingpong_paused_idx),
        "antiPingpongModeSwitchDelaySeconds": cfg.anti_pingpong_mode_switch_delay_seconds,
        "antiPingpongModeSwitchDominanceWindowSeconds": cfg.anti_pingpong_mode_switch_dominance_window_seconds,
        "antiPingpongGridPowerEntity": state.anti_pingpong_grid_power_entity_resolved,
        "antiPingpongSmartGainKwh": round(state.anti_pingpong_smart_gain_kwh, 6),
        "antiPingpongSmartLossKwh": round(state.anti_pingpong_smart_loss_kwh, 6),
        "antiPingpongSmartNetEur": round(state.anti_pingpong_smart_net_eur, 6),
        "relaySaver": int(cfg.relay_saver_enable),
        "relaySaverActive": int(_relay_saver_remaining_seconds(state, cfg, now()) > 0),
        "relaySaverDelayedDevice": _idx_mask(state.relay_saver_paused_idx),
        "relaySaverMinDropWatts": cfg.relay_saver_min_drop_watts,
        "relaySaverHoldSeconds": cfg.relay_saver_hold_seconds,
        "relaySaverReason": state.relay_saver_last_reason,
        "device_active_count": state.device_active_count,
        "relaySaverMinPower": _effective_relay_saver_min_power(state, cfg),
        "relaySaverRemainingSeconds": _relay_saver_remaining_seconds(state, cfg, now()),
    })
    if state.anti_pingpong_active:
        props["inputLimit"] = state.input_limit
        props["outputLimit"] = state.output_limit
    output = {"sn": f"{len(eligible)}x Zendure via PROXY", "product": next((records[idx].get("product", "") for idx in eligible if records[idx]), ""),
        "properties": props, "packData": [], "packDeviceSlots": [],
        "inverterTemperatureDeviceSlots": [], "timestamp": int(time.time()), "proxyVersion": PROXY_VERSION}
    for idx in range(max(3, count)):
        slot = idx + 1
        record = records[idx] if idx < count else {}
        p = record.get("properties") or {}
        dev = state.devices[idx] if idx < len(state.devices) else None
        output[f"sn_{slot}"] = record.get("sn", "") or (dev.sn if dev else "")
        output[f"product_{slot}"] = record.get("product", "")
        for key in keys | {"socLimit", "gridOffMode", "outputPackPower", "packInputPower", "gridInputPower", "outputHomePower", "electricLevel", "smartMode", "rssi", "inputLimit", "outputLimit", "acMode", "hyperTmp", "batCalTime", "socStatus", "chargeMaxLimit", "inverseMaxPower"}:
            props[f"{key}_{slot}"] = _int(p.get(key), -1 if key == "smartMode" else 0)
        props[f"latestPowerCmd_{slot}"] = commands[idx] if idx < len(commands) else 0
        props[f"ipAddress_{slot}"] = dev.ip if dev else ""
        props[f"sn_{slot}"] = output[f"sn_{slot}"]
        if dev:
            props[f"effectiveChargeMax_{slot}"] = dev.effective_charge_max_watts
            props[f"effectiveInverseMaxPower_{slot}"] = dev.effective_discharge_max_watts
            props[f"configuredChargeMax_{slot}"] = dev.configured_charge_max_watts
            props[f"configuredInverseMaxPower_{slot}"] = dev.configured_discharge_max_watts
        if cfg.solar_power_info:
            for channel in range(1, 5):
                props[f"solarPower{idx * 6 + channel}"] = _int(p.get(f"solarPower{channel}"))
        if idx in eligible and record:
            packs = record.get("packData") or []
            output["packData"].extend(copy.deepcopy(packs))
            output["packDeviceSlots"].extend([slot] * len(packs))
            if "hyperTmp" in p:
                output["inverterTemperatureDeviceSlots"].append(slot)
    return response_with_proxy_health(output, state, cfg, served_from_cache=False,
        reason=reason, refresh_in_progress=False)
