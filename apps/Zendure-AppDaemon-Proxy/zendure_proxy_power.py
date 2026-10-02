# SPDX-License-Identifier: GPL-3.0-only
"""Power allocation and monotonic transition timers for the proxy."""
from __future__ import annotations

import time

PROXY_VERSION = "v0.2.0"


def now() -> float:
    return time.monotonic()


def epoch() -> float:
    return time.time()


def _cap_list(max_power: int | list[int], n: int) -> list[int]:
    values = [max_power] * n if isinstance(max_power, (int, float)) else list(max_power)
    return [max(0, int(values[i])) if i < len(values) else 0 for i in range(n)]


def weighted_capped(total: int, weights: list[float], capacities: list[int], *, conserve: bool = True) -> list[int]:
    """Share watts by weight, freeze saturated slots, and allocate integer remainders."""
    target = min(max(0, int(total)), sum(capacities))
    floats = [0.0] * len(weights)
    pending = {i for i, w in enumerate(weights) if w > 0 and capacities[i] > 0}
    remaining = float(target)
    while pending and remaining > 0:
        denominator = sum(weights[i] for i in pending)
        shares = {i: remaining * weights[i] / denominator for i in pending}
        saturated = {i for i in pending if shares[i] >= capacities[i]}
        if not saturated:
            for i, share in shares.items():
                floats[i] = share
            break
        for i in saturated:
            floats[i] = float(capacities[i])
            remaining -= capacities[i]
        pending -= saturated
    result = [int(value) for value in floats]
    if conserve:
        missing = min(target, int(round(sum(floats)))) - sum(result)
        order = sorted(range(len(weights)), key=lambda i: (-(floats[i] - result[i]), -weights[i], i))
        for i in order:
            if missing <= 0:
                break
            if result[i] < capacities[i] and weights[i] > 0:
                result[i] += 1
                missing -= 1
    return result


def distribute_power(total: int, avail: list[float], max_power: int | list[int], balancing_factor: int = 5) -> list[int]:
    caps = _cap_list(max_power, len(avail))
    weights = [max(0.0, float(value)) ** max(1, balancing_factor) * cap for value, cap in zip(avail, caps)]
    target = min(max(0, total), sum(caps))
    denominator = sum(weights)
    shares = [target * weight / denominator if denominator else 0.0 for weight in weights]
    free = set(range(len(shares)))
    while free:
        clipped = {i for i in free if shares[i] > caps[i]}
        if not clipped:
            break
        for i in clipped:
            shares[i] = float(caps[i])
        free -= clipped
        residual = target - sum(shares[i] for i in range(len(shares)) if i not in free)
        subtotal = sum(shares[i] for i in free)
        scale = residual / subtotal if subtotal else 0.0
        for i in free:
            shares[i] *= scale
    return [min(caps[i], int(value)) for i, value in enumerate(shares)]


def split_equal_power(total: int, max_power: int | list[int]) -> list[int]:
    caps = [max_power, max_power] if isinstance(max_power, int) else max_power
    return weighted_capped(total, [1.0] * len(caps), _cap_list(caps, len(caps)))


def effective_min_soc_pct(state) -> float:
    return max(10.0, float(state.min_soc) / 10.0)


def soc_boundary_lockstep_active(state, eligible_indices: list[int] | None = None) -> bool:
    indices = range(len(state.devices)) if eligible_indices is None else eligible_indices
    return any(state.devices[i].electric_level <= effective_min_soc_pct(state) or state.devices[i].electric_level > 90 for i in indices)


def _direction_cap(dev, ac_mode: int) -> int:
    return dev.effective_charge_max_watts if ac_mode == 1 else dev.effective_discharge_max_watts


def _device_has_direction_headroom(state, idx: int, ac_mode: int) -> bool:
    dev = state.devices[idx]
    return dev.soc_limit != ac_mode and _direction_cap(dev, ac_mode) > 0 and (ac_mode != 2 or dev.electric_level > effective_min_soc_pct(state))


def _rank_for_direction(state, ac_mode: int, indices: list[int]) -> list[int]:
    return sorted(indices, key=lambda i: ((1 if ac_mode == 1 else -1) * state.devices[i].electric_level, i))


def calc_active_count(state, ac_mode: int, total_power: int, upper: float, lower: float, force_all: bool, device_change_diff: int = 5, eligible_indices: list[int] | None = None, soc_boundary_min_device_power_watts: int = 100, soc_boundary_active: bool = False, soc_boundary_measured_capacities: list[int] | None = None) -> int:
    indices = list(range(len(state.devices))) if eligible_indices is None else list(eligible_indices)
    if not indices:
        return 0
    if force_all:
        return len(indices)
    if soc_boundary_active and total_power <= 2 * soc_boundary_min_device_power_watts:
        return 1
    caps = soc_boundary_measured_capacities or [_direction_cap(state.devices[i], ac_mode) for i in indices]
    if soc_boundary_active:
        return min(len(indices), max(1, total_power // max(1, soc_boundary_min_device_power_watts)))
    threshold = lower if state.device_active_count > 1 else upper
    count = 1
    while count < len(indices) and total_power > sum(caps[:count]) * (threshold / max(1, caps[0])):
        count += 1
    return count


def apply_transition(per_device: list[int], state, now_ts: float, timer: int) -> list[int]:
    for kind in ('forced_dual', 'single_to_dual', 'transition'):
        start_key = f'{kind}_transition_start_ts' if kind != 'transition' else 'transition_start_ts'
        origin_key = f'{kind}_transition_original_device' if kind != 'transition' else 'transition_original_device'
        start = getattr(state, start_key, 0)
        if not start:
            continue
        elapsed = now_ts - start
        if timer <= 0 or elapsed >= timer:
            setattr(state, start_key, 0.0)
            continue
        origin = getattr(state, origin_key, 0)
        if origin < 0 or origin >= len(per_device):
            continue
        total = sum(per_device)
        if kind == 'forced_dual':
            fraction = 0.95 if elapsed < timer * 0.5 else 0.75 if elapsed < timer * 0.75 else 0.5 if elapsed < timer * 0.875 else 0.25
        else:
            fraction = 0.95 if elapsed < timer * 0.75 else 0.75
        result = [0] * len(per_device)
        result[origin] = int(total * fraction)
        other = [i for i in range(len(per_device)) if i != origin and per_device[i] > 0]
        if not other:
            other = [state.single_mode_active_device] if state.single_mode_active_device != origin else []
        if other:
            shares = weighted_capped(total - result[origin], [1.0] * len(other), [total] * len(other))
            for i, value in zip(other, shares):
                result[i] = value
        return result
    return per_device


def apply_damper(per_device, state, total_power, upper, now_ts, damper_amount, damper_timer):
    idx = state.single_mode_active_device
    if abs(state.latest_power_cmd) and 0 <= idx < len(per_device) and upper < total_power <= upper + damper_amount:
        if not state.dualmode_damper_start_ts:
            state.dualmode_damper_start_ts = now_ts
        if now_ts - state.dualmode_damper_start_ts < damper_timer:
            state.dualmode_damper_active = True
            return [int(upper) if i == idx else 0 for i in range(len(per_device))]
    state.dualmode_damper_active = False
    state.dualmode_damper_start_ts = 0.0
    return per_device
