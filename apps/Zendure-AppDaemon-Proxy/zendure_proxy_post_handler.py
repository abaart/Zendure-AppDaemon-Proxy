# SPDX-License-Identifier: GPL-3.0-only
"""Translate aggregate write requests into bounded physical-device commands."""
from __future__ import annotations

import asyncio
import copy

from zendure_proxy_power import (
    now, effective_min_soc_pct, soc_boundary_lockstep_active, weighted_capped,
    distribute_power, apply_damper, apply_transition, _direction_cap,
)
from zendure_proxy_anti_pingpong import (
    activation_mode, record_power_direction, threshold_active, select_anti_pingpong_split,
    clear_command_state, apply_mode_switch_delay, dominant_power_sign,
    minimum_control_power_watts,
)


def _int(value, default=0):
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _runtime_flag(state, cfg, name):
    overrides = getattr(state, '_runtime_mode_overrides', set())
    if name in overrides:
        return bool(getattr(state, name))
    return bool(getattr(state, name) or getattr(cfg, name, False))


def _full_power_payload(ac_mode, power):
    return {'acMode': ac_mode, 'inputLimit': max(0, power) if ac_mode == 1 else 0,
            'outputLimit': max(0, power) if ac_mode == 2 else 0}


def _power_ac_mode(props, current_ac_mode):
    return _int(props.get('acMode', current_ac_mode))


def _eligible(state, cfg):
    # Device health decides whether cached hardware can still receive commands.
    from zendure_proxy_health import eligible_device_indices
    return eligible_device_indices(state, cfg, current_ts=now())


def _direction_allowed(state, idx, mode):
    dev = state.devices[idx]
    return dev.soc_limit != mode and _direction_cap(dev, mode) > 0 and (mode != 2 or dev.electric_level > effective_min_soc_pct(state))


def _clear_transition_timers(state):
    for key in ('transition_start_ts', 'single_to_dual_transition_start_ts', 'forced_dual_transition_start_ts'):
        setattr(state, key, 0.0)


def _allocation(state, cfg, mode, power, eligible, previous_ts):
    size = len(state.devices)
    previous_active = list(state.devices_active_idx)
    previous_single = state.single_mode_active_device
    previous_mode = state.ac_mode
    result = [0] * size
    indices = [i for i in eligible if _direction_allowed(state, i, mode)]
    caps = [_direction_cap(state.devices[i], mode) for i in range(size)]
    force_all = _runtime_flag(state, cfg, 'equal_mode') or _runtime_flag(state, cfg, 'always_dual_mode')
    boundary = soc_boundary_lockstep_active(state, eligible)
    low_charge = mode == 1 and any(state.devices[i].electric_level <= effective_min_soc_pct(state) for i in indices)
    ranked = sorted(indices, key=lambda i: (state.devices[i].electric_level * (1 if mode == 1 else -1), i))
    if not indices:
        _clear_transition_timers(state)
        state.devices_active_idx_previous = list(state.devices_active_idx)
        state.devices_active_idx = []
        state.device_active_count = 0
        return result, boundary
    if low_charge and not force_all and ranked:
        lowest = state.devices[ranked[0]].electric_level
        indices = [i for i in ranked if state.devices[i].electric_level == lowest]
        ranked = list(indices)
    if force_all:
        active = sorted(indices)
    elif boundary:
        if not ranked:
            active = []
        elif low_charge:
            minimum = max(1, cfg.soc_boundary_min_device_power_watts)
            count = min(len(ranked), max(1, power // minimum))
            active = ranked[:count]
            if power < minimum and len(ranked) > 1:
                cursor = getattr(state, '_low_charge_cursor', 0) % len(ranked)
                active = [ranked[cursor]]
                state._low_charge_cursor = cursor + 1
        else:
            preferred = [i for i in ranked if mode != 1 or state.devices[i].electric_level <= 90]
            if not preferred:
                preferred = ranked[:]
            count = 1 if power <= 2 * cfg.soc_boundary_min_device_power_watts else min(len(preferred), max(1, power // cfg.soc_boundary_min_device_power_watts))
            active = preferred[:count]
            for idx in ranked:
                if sum(caps[i] for i in active) >= power:
                    break
                if idx not in active:
                    active.append(idx)
            if mode == 1 and len(active) == 1:
                shortfall = _fresh_shortfall(state, active[0], previous_ts)
                if shortfall > 0:
                    for idx in preferred:
                        if idx not in active:
                            active.append(idx)
                            break
    else:
        previous = [i for i in state.devices_active_idx if i in indices]
        if state.forced_dual_transition_start_ts:
            previous = [state.single_mode_active_device] if state.single_mode_active_device in indices else []
        count = max(1, len(previous))
        ordered = ranked[:]
        if previous and max(state.devices[i].electric_level for i in indices) - min(state.devices[i].electric_level for i in indices) < cfg.device_change_diff:
            ordered = previous + [i for i in ranked if i not in previous]
        if power <= (caps[ordered[0]] * cfg.single_mode_lower_pct / 100 if count > 1 else caps[ordered[0]] * cfg.single_mode_upper_pct / 100):
            count = 1
            if len(previous) > 1:
                ordered = ranked[:]
        else:
            count = min(count, len(ordered))
            while count < len(ordered) and sum(caps[i] for i in ordered[:count]) * cfg.single_mode_upper_pct / 100 < power:
                count += 1
        active = ordered[:count] if indices else []
    if active:
        active_caps = [caps[i] for i in active]
        headroom = [max(0.0, state.soc_set / 10 - state.devices[i].electric_level) if mode == 1 else max(0.0, state.devices[i].electric_level - effective_min_soc_pct(state)) for i in active]
        if _runtime_flag(state, cfg, 'equal_mode') or low_charge:
            shares = weighted_capped(power, [1.0] * len(active), active_caps)
        elif boundary and not force_all:
            if len(active) == 1:
                shares = [min(power, active_caps[0])]
            else:
                levels = [state.devices[i].electric_level for i in active]
                weights = [1 + max(levels) - level if mode == 1 else 1 + level - min(levels) for level in levels]
                base = [min(cfg.soc_boundary_min_device_power_watts, cap) for cap in active_caps]
                if sum(base) >= power:
                    shares = [0] * len(active)
                    remaining = power
                    for position, cap in enumerate(active_caps):
                        shares[position] = min(remaining, cap)
                        remaining -= shares[position]
                else:
                    extra = weighted_capped(power - sum(base), weights, [cap - minimum for cap, minimum in zip(active_caps, base)])
                    shares = [minimum + value for minimum, value in zip(base, extra)]
        else:
            shares = distribute_power(power, headroom, active_caps, cfg.balancing_factor)
        for i, value in zip(active, shares):
            result[i] = value
        if boundary and mode == 1 and not low_charge and not force_all:
            deficit = sum(_fresh_shortfall(state, i, previous_ts) for i in active)
            healthy = [i for i in active if _fresh_shortfall(state, i, previous_ts) <= 0]
            for idx in healthy:
                extra = min(deficit, caps[idx] - result[idx])
                result[idx] += extra
                deficit -= extra
    if (cfg.damper_enable or state.dualmode_damper_enabled) and not boundary and not force_all:
        idx = state.single_mode_active_device
        if idx in indices:
            result = apply_damper(result, state, power, caps[idx], now(), cfg.damper_amount, cfg.damper_timer)
            if state.dualmode_damper_active:
                active = [idx]
    if boundary or force_all or power <= 0:
        _clear_transition_timers(state)
    else:
        timestamp = now()
        prior_nonzero = state.latest_power_cmd * (1 if mode == 1 else -1) > 0
        origin = previous_active[0] if len(previous_active) == 1 else previous_single
        first_phase_fits = origin in indices and power * 0.95 <= caps[origin]
        existing_origin = state.forced_dual_transition_original_device if state.forced_dual_transition_start_ts else state.single_to_dual_transition_original_device
        if (state.forced_dual_transition_start_ts or state.single_to_dual_transition_start_ts) and (existing_origin not in indices or power * 0.95 > caps[existing_origin]):
            _clear_transition_timers(state)
        if prior_nonzero and previous_mode == mode and first_phase_fits and not (state.forced_dual_transition_start_ts or state.single_to_dual_transition_start_ts):
            if len(previous_active) == 1 and len(active) > 1 and origin in active and not state.single_to_dual_transition_start_ts:
                state.single_to_dual_transition_start_ts = timestamp
                state.single_to_dual_transition_original_device = origin
            elif len(previous_active) == 1 and len(active) == 1 and active[0] != origin and all(state.devices[i].soc_limit == 0 for i in eligible):
                state.forced_dual_transition_start_ts = timestamp
                state.forced_dual_transition_original_device = origin
                state.single_mode_active_device = active[0]
        result = apply_transition(result, state, timestamp, cfg.transition_timer)
        result = [min(value, caps[i]) if i in indices else 0 for i, value in enumerate(result)]
        if state.forced_dual_transition_start_ts:
            target = state.single_mode_active_device
            active = [state.forced_dual_transition_original_device, target]
    state.devices_active_idx_previous = list(state.devices_active_idx)
    state.devices_active_idx = active
    state.device_active_count = len(active)
    if len(active) == 1:
        state.single_mode_active_device = active[0]
    return result, boundary


def _fresh_shortfall(state, idx, previous_ts):
    dev = state.devices[idx]
    if previous_ts <= 0 or dev.last_successful_get_ts <= previous_ts or dev.latest_power_cmd <= 0:
        return 0
    measured = _int((dev.last_response or {}).get('properties', {}).get('packInputPower'), -1)
    return max(0, dev.latest_power_cmd - measured) if measured >= 0 else 0


def _apply_aggregate_limit_props(props, state, devs, eligible):
    for field, command_attr, effective_attr, direction in (
        ('chargeMaxLimit', 'charge_max_limit_cmd', 'charge_max_limit_effective', 1),
        ('inverseMaxPower', 'inverse_max_power_cmd', 'inverse_max_power_effective', 2),
    ):
        if field not in props:
            continue
        command = max(0, _int(props[field]))
        cap_values = [_direction_cap(devs[i], direction) for i in eligible]
        shares = weighted_capped(command - command % max(1, len(eligible)), [1.0] * len(eligible), cap_values)
        setattr(state, command_attr, command)
        setattr(state, effective_attr, sum(shares))
        props[field] = {i: share for i, share in zip(eligible, shares)}


def _relay_saver_payloads(state, cfg, signed, boundary, timestamp):
    state.relay_saver_paused_idx = []
    adjusted = {}
    if not cfg.relay_saver_enable or boundary:
        state.relay_saver_until_ts_by_idx.clear()
        state.relay_saver_sign_by_idx.clear()
        return adjusted
    for idx, desired in enumerate(signed):
        dev = state.devices[idx]
        previous = dev.latest_power_cmd
        sign = state.relay_saver_sign_by_idx.get(idx, 1 if previous > 0 else -1 if previous < 0 else 0)
        until = state.relay_saver_until_ts_by_idx.get(idx, 0)
        same = desired * sign > 0
        if same or (until and timestamp >= until):
            state.relay_saver_until_ts_by_idx.pop(idx, None)
            state.relay_saver_sign_by_idx.pop(idx, None)
            continue
        if not until and sign and abs(previous) >= cfg.relay_saver_min_drop_watts and desired * sign <= 0:
            until = timestamp + cfg.relay_saver_hold_seconds
            state.relay_saver_until_ts_by_idx[idx] = until
            state.relay_saver_sign_by_idx[idx] = sign
        if until > timestamp:
            mode = 1 if sign > 0 else 2
            power = minimum_control_power_watts(state, cfg.relay_saver_min_power_watts, mode, idx)
            if not _direction_allowed(state, idx, mode):
                power = 0
            power = min(power, _direction_cap(dev, mode))
            adjusted[idx] = _full_power_payload(mode, power)
            state.relay_saver_paused_idx.append(idx)
    if adjusted:
        state.relay_saver_last_reason = 'large_drop_to_zero'
    return adjusted


async def execute_post(payload, clients, state, cfg, logger, *, is_repeat=False):
    timestamp = now()
    properties = payload.get('properties')
    if not isinstance(properties, dict):
        return payload
    runtime_keys = {'equalMode': 'equal_mode', 'alwaysDualMode': 'always_dual_mode', 'dualModeDamper': 'dualmode_damper_enabled'}
    for key, attr in runtime_keys.items():
        if key in properties:
            setattr(state, attr, bool(_int(properties[key])))
            state._runtime_mode_overrides = getattr(state, '_runtime_mode_overrides', set()) | {attr}
            return payload
    props = copy.deepcopy(properties)
    eligible = _eligible(state, cfg)
    _apply_aggregate_limit_props(props, state, state.devices, eligible)
    for field, attr in (('minSoc', 'min_soc'), ('socSet', 'soc_set')):
        if field in props:
            setattr(state, attr, _int(props[field]))
    has_power = 'inputLimit' in props or 'outputLimit' in props
    mode = _power_ac_mode(props, state.ac_mode)
    explicit_mode = 'acMode' in props
    invalid = has_power and ((mode == 1 and _int(props.get('outputLimit')) > 0) or (mode == 2 and _int(props.get('inputLimit')) > 0) or mode not in (1, 2))
    previous_ts = state.latest_power_message_ts
    anti_payloads = None
    boundary = False
    per_device = [0] * len(state.devices)
    clear_command_state(state)
    if has_power:
        power = max(0, _int(props.get('inputLimit' if mode == 1 else 'outputLimit', 0))) if not invalid else 0
        signed_total = power if mode == 1 else -power
        if not invalid:
            per_device, boundary = _allocation(state, cfg, mode, power, eligible, previous_ts)
        record_power_direction(state, cfg, signed_total, is_repeat, timestamp)
        force_all = _runtime_flag(state, cfg, 'equal_mode') or _runtime_flag(state, cfg, 'always_dual_mode')
        anti_active = cfg.anti_pingpong_enable and not invalid and not force_all and (state.anti_pingpong_active if activation_mode(cfg) == 'smart' else threshold_active(state, cfg, signed_total, timestamp))
        if anti_active:
            split = select_anti_pingpong_split(state, cfg, mode, eligible, power, state.max_power_in if mode == 1 else state.max_power_out)
            state.anti_pingpong_last_reason = split.reason
            if split.active and all(_direction_allowed(state, i, mode) for i in split.service_idx):
                per_device = [0] * len(state.devices)
                shares = weighted_capped(split.service_power, [1.0] * len(split.service_idx), [_direction_cap(state.devices[i], mode) for i in split.service_idx])
                for i, value in zip(split.service_idx, shares):
                    per_device[i] = value
                state.anti_pingpong_service_idx = split.service_idx
                state.anti_pingpong_reserve_idx = split.reserve_idx
                state.anti_pingpong_reserve_power_watts = sum(split.reserve_power_by_idx.values())
                anti_payloads = {i: _full_power_payload(mode, per_device[i]) for i in eligible}
                for i, value in split.reserve_power_by_idx.items():
                    anti_payloads[i] = _full_power_payload(3 - mode, min(value, _direction_cap(state.devices[i], 3 - mode)))
                anti_payloads = apply_mode_switch_delay(state, cfg, anti_payloads, timestamp)
        elif cfg.anti_pingpong_enable and not invalid and not force_all:
            dominant = dominant_power_sign(state, cfg, timestamp)
            if dominant and dominant * signed_total < 0:
                anti_payloads = {}
                dominant_mode = 1 if dominant > 0 else 2
                for i in eligible:
                    dev = state.devices[i]
                    recent_opposite = [ts for ts, sample in state.anti_pingpong_power_samples if sample * dominant < 0]
                    if per_device[i] > 0 and recent_opposite and timestamp - min(recent_opposite) < cfg.anti_pingpong_mode_switch_delay_seconds:
                        hold = minimum_control_power_watts(state, cfg.anti_pingpong_reserve_power_watts, dominant_mode, i)
                        anti_payloads[i] = _full_power_payload(dominant_mode, hold if _direction_allowed(state, i, dominant_mode) else 0)
                if anti_payloads:
                    state.anti_pingpong_last_reason = 'dominant_charge_delay' if dominant > 0 else 'dominant_discharge_delay'
        signed = [p if mode == 1 else -p for p in per_device]
        if anti_payloads is not None:
            for i, command in anti_payloads.items():
                signed[i] = command.get('inputLimit', 0) - command.get('outputLimit', 0)
        relay = {} if anti_payloads else _relay_saver_payloads(state, cfg, signed, boundary, timestamp)
        if anti_payloads:
            state.relay_saver_paused_idx = []
        state.latest_power_cmd = signed_total
        state.input_limit = power if mode == 1 else 0
        state.output_limit = power if mode == 2 else 0
        if is_repeat:
            state.latest_power_repeat_ts = timestamp
        else:
            state.latest_power_message_ts = timestamp
            state.last_post_payload = copy.deepcopy(payload)
    else:
        relay = {}
    if explicit_mode and mode in (1, 2):
        state.ac_mode = mode
    sends = []
    for idx in eligible:
        if idx >= len(clients):
            continue
        dev = state.devices[idx]
        command = {key: value.get(idx, 0) if key in ('chargeMaxLimit', 'inverseMaxPower') and isinstance(value, dict) else value for key, value in props.items()}
        if has_power:
            for key in ('inputLimit', 'outputLimit'):
                if key in command:
                    command[key] = per_device[idx] if key == ('inputLimit' if mode == 1 else 'outputLimit') and not invalid else 0
            if state.ac_mode_inconsistent and not invalid:
                command['acMode'] = mode
            if anti_payloads and idx in anti_payloads:
                command.update(anti_payloads[idx])
            if idx in relay:
                command.update(relay[idx])
            for field, direction in (('inputLimit', 1), ('outputLimit', 2)):
                if field in command:
                    command[field] = min(max(0, _int(command[field])), _direction_cap(dev, direction))
            signed = _int(command.get('inputLimit')) - _int(command.get('outputLimit'))
            if signed < 0 and not _direction_allowed(state, idx, 2):
                command['outputLimit'] = 0
                signed = max(0, _int(command.get('inputLimit')))
            if signed and dev.standby_device:
                command['smartMode'] = 1
                command['acMode'] = mode
                dev.standby_device = False
                dev.smart_mode = 1
            if dev.standby_device and not signed:
                continue
            dev.latest_power_cmd = signed
            if signed:
                dev.latest_power_cmd_zero_ts = 0
            elif not dev.latest_power_cmd_zero_ts:
                dev.latest_power_cmd_zero_ts = timestamp
        elif dev.standby_device and _int(command.get('smartMode')) == 1:
            continue
        if 'smartMode' in command:
            dev.smart_mode = _int(command['smartMode'])
            if dev.smart_mode == 1 and idx not in state.devices_active_idx and not dev.latest_power_cmd_zero_ts:
                dev.latest_power_cmd_zero_ts = timestamp
        if 'acMode' in command and _int(command['acMode']) != dev.latest_ac_mode_cmd:
            dev.latest_ac_mode_cmd = _int(command['acMode'])
            dev.latest_ac_mode_change_ts = timestamp
        device_payload = {key: copy.deepcopy(value) for key, value in payload.items() if key not in ('sn', 'properties')}
        device_payload.update(sn=dev.sn, properties=command)
        sends.append((idx, clients[idx].post(device_payload)))
    replies = await asyncio.gather(*(call for _, call in sends), return_exceptions=True)
    from zendure_proxy_health import record_post_results, post_failure_response, public_post_response
    indexed = [(idx, post_failure_response(str(reply)) if isinstance(reply, BaseException) else post_failure_response('No response') if reply is None else reply)
               for (idx, _), reply in zip(sends, replies)]
    record_post_results(state, cfg, indexed, current_ts=timestamp)
    state.input_limit_effective = sum(max(0, d.latest_power_cmd) for d in state.devices)
    state.output_limit_effective = sum(max(0, -d.latest_power_cmd) for d in state.devices)
    if has_power and not invalid:
        state.ac_mode_inconsistent = False
    if has_power or _int(props.get('smartMode')) == 1:
        from zendure_proxy_standby import manage_standby
        await manage_standby(state, clients, mode if mode in (1, 2) else state.ac_mode,
                             [abs(d.latest_power_cmd) for d in state.devices], cfg, logger)
    return [public_post_response(reply) for _, reply in indexed]
