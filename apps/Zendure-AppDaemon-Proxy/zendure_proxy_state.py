# SPDX-License-Identifier: GPL-3.0-only
"""Runtime records for physical devices and aggregate proxy requests."""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Optional


def _effective_cap(reported_cap: int, configured_cap: Optional[int]) -> int:
    """Apply a positive configured ceiling to a usable device capability."""
    reported = reported_cap if reported_cap > 0 else 800
    if configured_cap is None or configured_cap <= 0:
        return reported
    return min(reported, configured_cap)


@dataclass
class DeviceState:
    ip: str = ""
    sn: str = ""
    electric_level: int = 50
    soc_status: int = 0
    smart_mode: int = 0
    soc_limit: int = 0
    charge_max_limit: int = 800
    inverse_max_power: int = 800
    configured_charge_max_watts: Optional[int] = None
    configured_discharge_max_watts: Optional[int] = None
    gridoff_mode: int = 2
    latest_power_cmd: int = 0
    latest_power_cmd_zero_ts: float = 0.0
    latest_ac_mode_cmd: int = 0
    latest_ac_mode_change_ts: float = 0.0
    last_response: Optional[dict] = None
    last_successful_get_ts: float = 0.0
    last_successful_get_epoch: float = 0.0
    last_failed_get_ts: float = 0.0
    last_get_error: str = ""
    last_successful_post_ts: float = 0.0
    last_failed_post_ts: float = 0.0
    last_post_error: str = ""
    recovery_started_ts: float = 0.0
    excluded_since_ts: float = 0.0
    dead_since_ts: float = 0.0
    latest_get_included: bool = True
    standby_task: Optional[asyncio.Task] = None
    standby_device: bool = False

    @property
    def effective_charge_max_watts(self) -> int:
        return _effective_cap(self.charge_max_limit, self.configured_charge_max_watts)

    @property
    def effective_discharge_max_watts(self) -> int:
        return _effective_cap(self.inverse_max_power, self.configured_discharge_max_watts)


@dataclass
class ProxyState:
    device_count: int = 0
    devices: list[DeviceState] = field(default_factory=list)
    ac_mode: int = 0
    min_soc: int = 100
    soc_set: int = 1000
    max_power_in: int = 800
    max_power_out: int = 800
    device_active_count: int = 1
    devices_active_idx_previous: list[int] = field(default_factory=lambda: [0])
    devices_active_idx: list[int] = field(default_factory=lambda: [0])
    single_mode_active_device: int = 0
    latest_power_cmd: int = 0
    input_limit: int = 0
    output_limit: int = 0
    input_limit_effective: int = 0
    output_limit_effective: int = 0
    charge_max_limit_cmd: int = 0
    charge_max_limit_effective: int = 0
    inverse_max_power_cmd: int = 0
    inverse_max_power_effective: int = 0
    transition_start_ts: float = 0.0
    transition_original_device: int = 0
    single_to_dual_transition_start_ts: float = 0.0
    single_to_dual_transition_original_device: int = 0
    forced_dual_transition_start_ts: float = 0.0
    forced_dual_transition_original_device: int = 0
    dualmode_damper_enabled: bool = False
    dualmode_damper_active: bool = False
    dualmode_damper_start_ts: float = 0.0
    anti_pingpong_active: bool = False
    anti_pingpong_until_ts: float = 0.0
    anti_pingpong_last_sign: int = 0
    anti_pingpong_flip_times: list[float] = field(default_factory=list)
    anti_pingpong_power_samples: list[tuple[float, int]] = field(default_factory=list)
    anti_pingpong_service_idx: list[int] = field(default_factory=list)
    anti_pingpong_reserve_idx: list[int] = field(default_factory=list)
    anti_pingpong_paused_idx: list[int] = field(default_factory=list)
    anti_pingpong_reserve_power_watts: int = 0
    anti_pingpong_last_reason: str = ""
    anti_pingpong_grid_power_entity_resolved: str = ""
    anti_pingpong_grid_power_entity_source: str = ""
    anti_pingpong_grid_samples: list[tuple[float, float]] = field(default_factory=list)
    anti_pingpong_smart_last_eval_ts: float = 0.0
    anti_pingpong_smart_gain_kwh: float = 0.0
    anti_pingpong_smart_loss_kwh: float = 0.0
    anti_pingpong_smart_net_eur: float = 0.0
    anti_pingpong_smart_bad_minutes: int = 0
    relay_saver_paused_idx: list[int] = field(default_factory=list)
    relay_saver_until_ts_by_idx: dict[int, float] = field(default_factory=dict)
    relay_saver_sign_by_idx: dict[int, int] = field(default_factory=dict)
    relay_saver_last_reason: str = ""
    ac_mode_inconsistent: bool = False
    equal_mode: bool = False
    always_dual_mode: bool = False
    counter_get_received: int = 0
    counter_get_replies: int = 0
    counter_get_timeouts: int = 0
    counter_config_drop: int = 0
    counter_serial_missing_drop: int = 0
    counter_post_received: int = 0
    counter_post_replies: int = 0
    counter_missing: list[int] = field(default_factory=lambda: [0, 0, 0])
    startup_ts: float = 0.0
    last_ha_get_ts: float = 0.0
    last_upstream_get_ts: float = 0.0
    get_refresh_in_progress: bool = False
    latest_get_ts: float = 0.0
    latest_power_message_ts: float = 0.0
    latest_power_repeat_ts: float = 0.0
    last_post_payload: Optional[dict] = None
    standby_last_sent_by_sn: dict[str, float] = field(default_factory=dict)
    last_get_response: Optional[dict] = None

    def __post_init__(self) -> None:
        count = self.device_count if self.device_count > 0 else 3
        self.counter_missing = (list(self.counter_missing) + [0] * count)[:count]
