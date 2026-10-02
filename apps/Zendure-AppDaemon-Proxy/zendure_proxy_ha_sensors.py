# SPDX-License-Identifier: GPL-3.0-only
"""Translate protocol reports into Home Assistant sensor values and attributes."""
from __future__ import annotations

from typing import Any


def _int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError, OverflowError):
        return default


def _map_int(value, mapping):
    if value in ("unavailable", "unknown"):
        return value
    return mapping.get(_int(value, -1), "Onbekend")


def _valid_rssi(value):
    try:
        number = float(value)
        return number if -120 <= number < 0 else "unavailable"
    except (TypeError, ValueError, OverflowError):
        return "unavailable"


def _directed_power(grid_input, home_output):
    if grid_input == "unavailable" or home_output == "unavailable":
        return "unavailable"
    incoming, outgoing = _int(grid_input), _int(home_output)
    return incoming if incoming > 0 else -outgoing


def _power_mode(power):
    if power == "unavailable":
        return power
    number = _int(power)
    return "Opladen" if number > 0 else "Ontladen" if number < 0 else "Geen vermogen"


def _power_mode_icon(state):
    return {"Opladen": "mdi:battery-plus-variant", "Ontladen": "mdi:battery-minus-variant"}.get(state, "mdi:battery-outline")


def _storage_mode(value):
    return _map_int(value, {0: "Opslaan in Flash", 1: "Opslaan in RAM"})


def _deep_standby_state(value):
    return _map_int(value, {0: "Aan", 1: "Uit"})


def _deep_standby_icon(state):
    return {"Aan": "mdi:sleep", "Uit": "mdi:sleep-off"}.get(state, "mdi:help-circle-outline")


def _relay_state(value):
    return _map_int(value, {0: "Standby", 1: "Oplaadstand", 2: "Ontlaadstand"})


def _zendure_temp(raw):
    if raw is None or raw == "unknown":
        return "unknown"
    if raw == "unavailable":
        return raw
    try:
        return round((float(raw) - 2731) / 10, 1)
    except (TypeError, ValueError, OverflowError):
        return "unknown"


def _on_off(value):
    return _map_int(value, {0: "Uit", 1: "Aan"})


def _active_device(value, configured_count=3):
    mask = _int(value)
    if mask == 0:
        return "Geen"
    if mask == (1 << configured_count) - 1:
        return "Alle"
    return " en ".join(f"Zendure {slot}" for slot in range(1, configured_count + 1) if mask & (1 << (slot - 1))) or "Onbekend"


def _health_slots(health, key):
    return {_int(item.get("slot")) for item in health.get(key, []) if isinstance(item, dict)}


def _health_item(health, slot):
    for key in ("deadDevices", "recoveringDevices", "degradedDevices", "excludedDevices", "unhealthyDevices"):
        for item in health.get(key, []):
            if isinstance(item, dict) and _int(item.get("slot")) == slot:
                return item
    return {}


def _soc_limit_icon(state):
    return {"Normale werking": "mdi:battery-medium", "Laadlimiet bereikt": "mdi:battery-high", "Ontlaadlimiet bereikt": "mdi:battery-low"}.get(state, "mdi:battery-outline")


def _offgrid_icon(state):
    return "mdi:power-plug-off" if state == "Uitgeschakeld" else "mdi:power-plug"


def _battery_order(raw):
    if not isinstance(raw, str) or not raw.strip() or raw.strip() in {"unknown", "unavailable"}:
        return None
    numbers = [_int(token, 0) - 1 for token in raw.split(";")]
    return numbers if all(number >= 0 for number in numbers) else None


def _pack_index(battery_order, battery):
    return battery_order[battery - 1] if battery_order and battery <= len(battery_order) else battery - 1


def _pack_at(pack_data, battery_order, battery):
    index = _pack_index(battery_order, battery)
    return pack_data[index] if 0 <= index < len(pack_data) and isinstance(pack_data[index], dict) else {}


def build_proxy_ha_sensors(response: dict, battery_order_raw: Any = None):
    props = response.get("properties") or {}
    health = response.get("proxyHealth") or {}
    count = max(1, _int(health.get("configuredCount"), 3))
    times = health.get("lastSuccessfulGetAtBySlot") or {}
    timestamps = [value for value in times.values() if isinstance(value, (int, float)) and value > 0]
    global_time = min(timestamps) if timestamps else None
    sensors = {}
    battery_attrs = {"device_class": "battery", "unit_of_measurement": "%", "state_class": "measurement"}
    power_attrs = {"unit_of_measurement": "W", "state_class": "measurement", "device_class": "power"}
    temp_attrs = {"unit_of_measurement": "°C", "state_class": "measurement", "device_class": "temperature", "icon": "mdi:thermometer"}

    def add(entity_id, value, name, timestamp=global_time, **attrs):
        sensors[entity_id] = (value, {"friendly_name": name, **attrs, "proxy_last_successful_get_at": timestamp})

    excluded = _health_slots(health, "excludedDevices")
    dead = _health_slots(health, "deadDevices")
    recovering = _health_slots(health, "recoveringDevices")
    unhealthy = _health_slots(health, "unhealthyDevices")
    for slot in range(1, max(3, count) + 1):
        prefix = f"sensor.zendure_{slot}_"
        title = f"Zendure {slot}"
        stamp = times.get(str(slot))
        p = lambda key, default=None: props.get(f"{key}_{slot}", default)
        item = _health_item(health, slot)
        state = "Dead" if slot in dead else "Recovering" if slot in recovering else "Degraded" if slot in excluded | unhealthy else "Healthy" if slot <= count else "Not configured"
        add(prefix + "health", state, title + " Health", timestamp=stamp, icon="mdi:heart-pulse",
            serial_number=response.get(f"sn_{slot}") or p("sn", "unknown"), ip_address=p("ipAddress", ""),
            last_successful_get_age_seconds=item.get("lastSuccessfulGetAgeSeconds"),
            excluded_from_power=slot in excluded, recovery_seconds_remaining=item.get("recoverySecondsRemaining", 0.0),
            charge_max_watts=p("effectiveChargeMax", p("chargeMaxLimit")),
            discharge_max_watts=p("effectiveInverseMaxPower", p("inverseMaxPower")),
            configured_charge_max_watts=p("configuredChargeMax"), configured_discharge_max_watts=p("configuredInverseMaxPower"))
        unavailable = slot in excluded | dead | recovering
        dynamic = lambda value: "unavailable" if unavailable else value
        smart = dynamic(p("smartMode", -1))
        power = dynamic(_directed_power(p("gridInputPower", 0), p("outputHomePower", 0)))
        mode = _power_mode(power)
        soc = _map_int(dynamic(p("socLimit")), {0: "Normale werking", 1: "Laadlimiet bereikt", 2: "Ontlaadlimiet bereikt"})
        offgrid = _map_int(dynamic(p("gridOffMode")), {0: "Normaal", 1: "Eco", 2: "Uitgeschakeld"})
        standby = _deep_standby_state(smart)
        entries = [
            ("laadpercentage", dynamic(p("electricLevel", "unknown")), "Laadpercentage", battery_attrs),
            ("wifi_rssi", dynamic(_valid_rssi(p("rssi"))), "Wi-Fi RSSI", {"unit_of_measurement": "dBm", "device_class": "signal_strength", "state_class": "measurement", "icon": "mdi:wifi"}),
            ("vermogen_aansturing", power, "Vermogen Aansturing", power_attrs),
            ("modus", mode, "Vermogensmodus", {"icon": _power_mode_icon(mode), "mode_basis": "gridInputPower/outputHomePower", "deep_standby_entity_id": prefix + "deep_standby"}),
            ("relais_stand", _relay_state(dynamic(p("acMode", 0))), "Relais Stand", {"icon": "mdi:swap-vertical-bold"}),
            ("kalibratie_bezig", _map_int(dynamic(p("socStatus")), {0: "Nee", 1: "Kalibreren"}), "Kalibratie bezig", {"icon": "mdi:battery-heart-variant"}),
            ("opslagmodus", _storage_mode(smart), "Opslagmodus", {"icon": "mdi:floppy", "smart_mode": smart}),
            ("deep_standby", standby, "Deep Standby", {"icon": _deep_standby_icon(standby), "smart_mode": smart, "source_entity_id": prefix + "opslagmodus"}),
            ("soc_limiet_status", soc, "SOC-limiet Status", {"icon": _soc_limit_icon(soc)}),
            ("omvormer_temperatuur", _zendure_temp(dynamic(p("hyperTmp"))), "Omvormer Temperatuur", temp_attrs),
            ("offgrid_modus", offgrid, "Offgrid Modus", {"icon": _offgrid_icon(offgrid)}),
            ("serienummer", response.get(f"sn_{slot}") or p("sn", "unknown"), "Serienummer", {"icon": "mdi:identifier"}),
            ("ip_adres", p("ipAddress", "unknown"), "IP Adres", {"icon": "mdi:ip"}),
        ]
        for suffix, value, label, attrs in entries:
            add(prefix + suffix, value, title + " " + label, timestamp=stamp, **attrs)
        add(f"sensor.vermogensopdracht_zendure_{slot}", dynamic(p("latestPowerCmd", 0)), f"Vermogensopdracht Zendure {slot}", timestamp=stamp, **power_attrs)
    pool = "Degraded" if excluded or unhealthy or dead or recovering or _int(health.get("unhealthyCount")) else "Healthy"
    pool_attrs = {f"{name}_count": health.get(name + "Count", 0) for name in ("configured", "healthy", "unhealthy", "excluded", "recovering", "degraded", "dead")}
    pool_attrs.update(unhealthy_serial_numbers=[item.get("serialNumber", "unknown") for item in health.get("unhealthyDevices", [])], proxy_last_successful_get_at_by_slot=times)
    add("sensor.proxy_zendure_pool_healthy", pool, "Proxy Zendure Pool Healthy", icon="mdi:battery-heart", **pool_attrs)
    add("sensor.vermogensopdracht", props.get("latestPowerCmd", 0), "Vermogensopdracht", **power_attrs)
    add("sensor.zendure_actief_device", _active_device(props.get("activeDevice"), count), "Zendure Actief Device", icon="mdi:battery")
    # Each descriptor identifies one published protocol field, conversion and display unit.
    descriptors = [
        ("anti_pingpong_status", "antiPingpongActive", "Reserve Mode Status", "switch", "mdi:swap-horizontal-bold"),
        ("anti_pingpong_activatie_modus", "antiPingpongActivationMode", "Reserve Mode Activatie Modus", "text", "mdi:tune"),
        ("anti_pingpong_p1_sensor", "antiPingpongGridPowerEntity", "Reserve Mode P1 Sensor", "text", "mdi:flash"),
        ("anti_pingpong_p1_sensor_bron", "antiPingpongGridPowerEntitySource", "Reserve Mode P1 Sensor Bron", "text", "mdi:source-branch"),
        ("anti_pingpong_reserve_device", "antiPingpongReserveDevice", "Reserve Mode Reserve Device", "mask", "mdi:battery-clock"),
        ("anti_pingpong_gepauzeerd_device", "antiPingpongDelayedDevice", "Reserve Mode Vertraagd Device", "mask", "mdi:timer-pause"),
        ("anti_pingpong_reserve_power", "antiPingpongReservePower", "Reserve Mode Reserve Power", "power", ""),
        ("anti_pingpong_service_boost", "antiPingpongServiceBoost", "Reserve Mode Service Boost", "power", ""),
        ("anti_pingpong_smart_winst_kwh", "antiPingpongSmartGainKwh", "Reserve Mode Smart Winst", "energy", ""),
        ("anti_pingpong_smart_verlies_kwh", "antiPingpongSmartLossKwh", "Reserve Mode Smart Verlies", "energy", ""),
        ("anti_pingpong_smart_netto_euro", "antiPingpongSmartNetEur", "Reserve Mode Smart Netto Euro", "money", "mdi:currency-eur"),
        ("relay_saver_status", "relaySaverActive", "Relay Saver Status", "switch", "mdi:electric-switch"),
        ("relay_saver_vertraagd_device", "relaySaverDelayedDevice", "Relay Saver Vertraagd Device", "mask", "mdi:timer-pause"),
        ("relay_saver_minimumvermogen", "relaySaverMinPower", "Relay Saver Minimumvermogen", "power", ""),
        ("relay_saver_drempel", "relaySaverMinDropWatts", "Relay Saver Drempel", "power", ""),
        ("relay_saver_resterende_seconden", "relaySaverRemainingSeconds", "Relay Saver Resterende Seconden", "seconds", "mdi:timer-sand"),
        ("dual_mode_demper_status", "dualModeDamper", "Dual Mode Demper Status", "switch", "mdi:speedometer-medium"),
        ("synchroon_laden_status", "equalMode", "Synchroon Laden Status", "switch", "mdi:battery-sync"),
        ("beide_actief_status", "alwaysDualMode", "Beide Actief Status", "switch", "mdi:format-columns"),
        ("zendure_proxy_versie", "proxyVersion", "Zendure Proxy Versie", "version", "mdi:call-split"),
    ]
    for entity, key, label, kind, icon in descriptors:
        default = "threshold" if key == "antiPingpongActivationMode" else "" if kind == "text" else "unknown" if kind == "version" else 0
        value = props.get(key, response.get(key, default))
        attrs = {"icon": icon} if icon else {}
        if kind == "switch": value = _on_off(value)
        if kind == "mask": value = _active_device(value, count)
        if kind == "power": attrs.update(power_attrs)
        if kind == "energy": attrs.update(unit_of_measurement="kWh", state_class="measurement", device_class="energy")
        if kind == "money": attrs.update(unit_of_measurement="€", state_class="measurement")
        if kind == "seconds": attrs.update(unit_of_measurement="s", state_class="measurement")
        add("sensor." + entity, value, label, **attrs)
    packs = response.get("packData") or []
    sources = response.get("packDeviceSlots") or []
    order = _battery_order(battery_order_raw)
    for battery in range(7, 19):
        pack = _pack_at(packs, order, battery)
        index = _pack_index(order, battery)
        source = sources[index] if 0 <= index < len(sources) else None
        attrs = {"proxy_source_slot": source} if source is not None else {}
        stamp = times.get(str(source)) if source is not None else global_time
        unavailable = source in excluded | dead | recovering
        for suffix, key, label, unit in (("laadpercentage", "socLevel", "Laadpercentage", battery_attrs), ("temperatuur", "maxTemp", "Temperatuur", temp_attrs)):
            value = "unavailable" if unavailable else pack.get(key, "unknown")
            if key == "maxTemp": value = _zendure_temp(value)
            add(f"sensor.zendure_2400_ac_batterij_{battery}_{suffix}", value, f"Zendure 2400 AC Batterij {battery} {label}", timestamp=stamp, **unit, **attrs)
    return sensors
