# SPDX-License-Identifier: GPL-3.0-only
"""Read AppDaemon arguments into explicit proxy configuration records."""
from __future__ import annotations

from dataclasses import dataclass, field, fields

MAX_DEVICE_COUNT = 10


@dataclass
class DevicePowerLimit:
    charge_max_watts: int | None = None
    discharge_max_watts: int | None = None


@dataclass
class Config:
    device_ips: list = field(default_factory=list)
    device_power_limits: list[DevicePowerLimit] = field(default_factory=list)
    config_warnings: list[str] = field(default_factory=list)
    server_port: int = 8120
    server_host: str = "0.0.0.0"
    zendure_request_timeout: float = 60.0
    separate_get_post_connections: bool = True
    idle_connection_close_seconds: float = 600.0
    ha_get_response_timeout: float = 8.0
    get_cache_max_age: float = 300.0
    get_rate_limit_window: float = 1.0
    get_recovery_window: float = 30.0
    degraded_power_hold_seconds: float = 1800.0
    single_mode_upper_pct: int = 100
    single_mode_lower_pct: int = 40
    device_change_diff: int = 5
    standby_timer: int = 300
    standby_charging: bool = True
    standby_discharging: bool = True
    transition_timer: int = 40
    balancing_factor: int = 5
    soc_boundary_min_device_power_watts: int = 100
    soc_boundary_low_power_change_diff: int = 1
    damper_enable: bool = False
    damper_timer: int = 120
    damper_amount: int = 200
    always_dual_mode: bool = False
    equal_mode: bool = False
    anti_pingpong_enable: bool = False
    anti_pingpong_activation_mode: str = "threshold"
    anti_pingpong_window_seconds: int = 180
    anti_pingpong_min_flips: int = 3
    anti_pingpong_hold_seconds: int = 300
    anti_pingpong_min_power_watts: int = 100
    anti_pingpong_reserve_count: int = 1
    anti_pingpong_reserve_power_watts: int = 40
    anti_pingpong_reserve_soc_margin_percent: int = 5
    anti_pingpong_mode_switch_delay_seconds: int = 30
    anti_pingpong_mode_switch_pause_seconds: int = 30
    anti_pingpong_mode_switch_dominance_window_seconds: int = 120
    anti_pingpong_grid_power_entity: str = ""
    anti_pingpong_grid_power_autodiscover: bool = True
    anti_pingpong_grid_power_import_positive: bool = True
    anti_pingpong_smart_window_seconds: int = 300
    anti_pingpong_smart_sample_interval_seconds: int = 1
    anti_pingpong_smart_evaluate_interval_seconds: int = 60
    anti_pingpong_smart_response_time_seconds: float = 3.0
    anti_pingpong_low_power_roundtrip_efficiency: float = 0.4
    anti_pingpong_energy_price_per_kwh: float = 0.3
    anti_pingpong_smart_disable_bad_minutes: int = 2
    relay_saver_enable: bool = False
    relay_saver_min_drop_watts: int = 900
    relay_saver_min_power_watts: int = 40
    relay_saver_hold_seconds: int = 30
    solar_power_info: bool = False
    manual_mode_repeat: bool = True
    log_file_enabled: bool = True
    log_file_path: str = ""
    log_file_max_bytes: int = 1000000
    log_file_backup_count: int = 5
    log_dashboard_enabled: bool = True
    log_dashboard_route: str = "zendure_proxy_logs"
    log_dashboard_lines: int = 300
    metrics_enabled: bool = True
    metrics_dashboard_enabled: bool = True
    metrics_dashboard_route: str = "zendure_proxy_metrics"
    metrics_dashboard_refresh: int = 10
    metrics_ha_sensors_enabled: bool = True
    metrics_ha_sensors_interval: int = 30
    proxy_ha_sensors_enabled: bool = True
    proxy_ha_sensors_skip_existing: bool = True
    proxy_ha_sensors_mqtt_discovery_enabled: bool = True
    proxy_ha_sensors_mqtt_discovery_prefix: str = "homeassistant"
    proxy_ha_sensors_mqtt_state_prefix: str = "zendure_proxy"
    proxy_ha_sensors_mqtt_retain: bool = True
    debug_payload_capture_enabled: bool = False
    diagnostics_dashboard_enabled: bool = True
    diagnostics_dashboard_route: str = "zendure_proxy_diagnostics"


_ALIASES = {
    "single_mode_upper_pct": "single_mode_upperlimit_percent",
    "single_mode_lower_pct": "single_mode_lowerlimit_percent",
    "device_change_diff": "single_mode_change_device_diff",
    "standby_timer": "single_mode_delayed_standby_timer",
    "standby_charging": "single_mode_standby_charging_enable",
    "standby_discharging": "single_mode_standby_discharging_enable",
    "transition_timer": "singlemode_transition_timer",
    "damper_enable": "dualmode_damper_enable",
    "damper_timer": "dualmode_damper_timer",
    "damper_amount": "dualmode_damper_amount",
    "anti_pingpong_mode_switch_delay_seconds": "anti_pingpong_mode_switch_pause_seconds",
}


def _bool(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"true", "yes", "on", "1"}
    return bool(value)


def is_placeholder_device_ip(value: str) -> bool:
    host = str(value or "").strip().lower()
    if not host:
        return True
    octets = host.split(".")
    return len(octets) == 4 and any(part in {"x", "y", "z", "xx", "xxx"} for part in octets)


def load_config(args: dict) -> Config:
    cfg = Config()
    for spec in fields(cfg):
        name = spec.name
        default = getattr(cfg, name)
        if isinstance(default, list):
            continue
        key = name if name in args else _ALIASES.get(name, name)
        if key not in args:
            continue
        value = args[key]
        try:
            converted = _bool(value) if isinstance(default, bool) else type(default)(value)
        except (ValueError, TypeError, OverflowError):
            cfg.config_warnings.append(f"{key}: invalid value; using {default!r}")
            continue
        setattr(cfg, name, converted)
    cfg.anti_pingpong_mode_switch_pause_seconds = cfg.anti_pingpong_mode_switch_delay_seconds
    cfg.device_ips, cfg.device_power_limits, device_warnings = _load_device_slots(args)
    cfg.config_warnings.extend(device_warnings)
    return cfg


def _load_device_slots(args: dict):
    warnings: list[str] = []
    slots = []
    for number in range(1, MAX_DEVICE_COUNT + 1):
        label = f"zendure_{number}"
        slots.append({
            "ip": args.get(f"ip_zendure_{number}", ""),
            "charge_max_watts": _cap_from_args(args, f"{label}_charge_max_watts", warnings),
            "discharge_max_watts": _cap_from_args(args, f"{label}_discharge_max_watts", warnings),
        })
    supplied = args.get("devices", [])
    if not isinstance(supplied, (list, tuple)):
        warnings.append("devices: expected a list of device mappings")
        supplied = []
    if len(supplied) > MAX_DEVICE_COUNT:
        warnings.append("devices: max device count is 10; extra entries ignored")
    for index, item in enumerate(supplied[:MAX_DEVICE_COUNT]):
        if not isinstance(item, dict):
            warnings.append(f"devices[{index}]: expected a device mapping")
            continue
        if "ip" in item:
            slots[index]["ip"] = item["ip"]
        for name in ("charge_max_watts", "discharge_max_watts"):
            if name in item:
                slots[index][name] = _cap_from_mapping(item, name, f"devices[{index}].{name}", warnings)
    ips: list[str] = []
    limits: list[DevicePowerLimit] = []
    for item in slots:
        host = str(item["ip"] or "").strip()
        if is_placeholder_device_ip(host):
            continue
        ips.append(host)
        limits.append(DevicePowerLimit(item["charge_max_watts"], item["discharge_max_watts"]))
    return ips, limits, warnings


def _cap_from_args(args: dict, key: str, warnings: list[str]):
    return _parse_positive_int(args.get(key), key, warnings)


def _cap_from_mapping(item: dict, key: str, label: str, warnings: list[str]):
    return _parse_positive_int(item.get(key), label, warnings)


def _parse_positive_int(value, label: str, warnings: list[str]):
    if value is None or value == "":
        return None
    try:
        number = int(value)
        if number > 0 and not isinstance(value, bool):
            return number
    except (ValueError, TypeError, OverflowError):
        pass
    warnings.append(f"{label}: expected a positive integer; device capability will be used")
    return None
