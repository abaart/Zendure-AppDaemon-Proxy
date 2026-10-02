# SPDX-License-Identifier: GPL-3.0-only
"""Schedule passive devices to sleep after their zero-power interval."""
from __future__ import annotations

import asyncio
from typing import Callable

from zendure_proxy_config import Config
from zendure_proxy_device_client import DeviceClient
from zendure_proxy_health import post_response_failed, record_post_results
from zendure_proxy_power import now
from zendure_proxy_state import ProxyState


def _protected_indices(state: ProxyState) -> set[int]:
    return set(state.devices_active_idx) | set(state.anti_pingpong_reserve_idx) | set(state.relay_saver_paused_idx)


def _standby_allowed(idx: int, state: ProxyState, cfg: Config) -> bool:
    if idx < 0 or idx >= len(state.devices) or state.device_count < 2:
        return False
    if idx in _protected_indices(state) or state.latest_power_cmd == 0:
        return False
    ts = now()
    if state.latest_get_ts <= 0 or ts - state.latest_get_ts > 10:
        return False
    for transition in (state.transition_start_ts, state.single_to_dual_transition_start_ts,
                       state.forced_dual_transition_start_ts):
        if transition > 0 and ts - transition < cfg.transition_timer + 10:
            return False
    charging = state.latest_power_cmd > 0
    if charging and not cfg.standby_charging:
        return False
    if not charging and not cfg.standby_discharging:
        return False
    if charging and state.device_count == 2 and len(state.devices) == 2:
        if abs(state.devices[0].electric_level - state.devices[1].electric_level) == cfg.device_change_diff:
            return False
    return bool(state.devices[idx].sn)


def _standby_delay(zero_ts: float, standby_timer: int) -> float:
    if zero_ts <= 0:
        return max(0.0, float(standby_timer))
    return max(0.0, standby_timer - (now() - zero_ts))


async def manage_standby(state: ProxyState, clients: list[DeviceClient], ac_mode: int,
                         per_device: list[int], cfg: Config, logger: Callable):
    protected = _protected_indices(state)
    for idx, dev in enumerate(state.devices):
        if idx >= len(clients):
            break
        commanded = per_device[idx] if idx < len(per_device) else dev.latest_power_cmd
        if commanded != 0 or idx in protected:
            if dev.standby_task is not None:
                dev.standby_task.cancel()
                dev.standby_task = None
            if dev.standby_device and dev.sn:
                result = await clients[idx].post({"sn": dev.sn, "properties": {"smartMode": 1}})
                record_post_results(state, cfg, [(idx, result)])
                if not post_response_failed(result):
                    dev.smart_mode = 1
                    dev.standby_device = False
            continue
        if not _standby_allowed(idx, state, cfg):
            if dev.standby_task is not None:
                dev.standby_task.cancel()
                dev.standby_task = None
            continue
        if dev.standby_device:
            continue
        if dev.standby_task is None or dev.standby_task.done():
            if dev.latest_power_cmd_zero_ts <= 0:
                dev.latest_power_cmd_zero_ts = now()
            dev.standby_task = asyncio.create_task(_delayed_standby(
                idx, state, clients, _standby_delay(dev.latest_power_cmd_zero_ts, cfg.standby_timer), logger, cfg=cfg))


async def _delayed_standby(idx: int, state: ProxyState, clients: list[DeviceClient], delay: float,
                           logger: Callable, *, cfg: Config | None = None):
    if delay > 0:
        await asyncio.sleep(delay)
    if idx < 0 or idx >= len(state.devices) or idx >= len(clients):
        return
    dev = state.devices[idx]
    if idx in _protected_indices(state) or dev.latest_power_cmd != 0 or not dev.sn:
        return
    if cfg is not None and not _standby_allowed(idx, state, cfg):
        return
    zero_event = dev.latest_power_cmd_zero_ts
    if dev.sn in state.standby_last_sent_by_sn and state.standby_last_sent_by_sn[dev.sn] == zero_event:
        return
    result = await clients[idx].post({"sn": dev.sn, "properties": {
        "smartMode": 0, "outputLimit": 0, "inputLimit": 0}})
    if cfg is not None:
        record_post_results(state, cfg, [(idx, result)])
    if post_response_failed(result):
        return
    dev.smart_mode = 0
    dev.standby_device = True
    state.standby_last_sent_by_sn[dev.sn] = zero_event
    for close_name in ("close_post_connection", "close_get_connection"):
        close = getattr(clients[idx], close_name, None)
        if close is not None:
            await close()
    logger(f"Device {idx + 1} {dev.sn} entered standby", level="INFO")
