# GET and sensor implementation inputs

The replacement `zendure_proxy_get_handler.py` and `zendure_proxy_ha_sensors.py` were written independently with GPL-3.0-only SPDX headers.

The implementer read the following approved inputs in the new repository:

- `audit/IMPLEMENTATION_INPUTS.md`, `audit/interface-signatures.json`, and `audit/data-contract.json`.
- Synthetic tests: `tests/test_node_red_get_response_compat.py`, `tests/test_node_red_repeat_standby_serial_simulated.py`, `tests/test_appdaemon_proxy_release_gate.py`, `tests/conftest.py`, and `tests/node_red_expected.py`.
- Retained modules: `zendure_proxy_health.py`, `zendure_proxy_metrics.py`, `zendure_proxy_mqtt_discovery.py`, `zendure_proxy_publication.py`, and the `maximum_control_power_watts` helper in `zendure_proxy_anti_pingpong.py`.
- `audit/sensor-observable-fixtures.json`: four coordinator-supplied synthetic input/output fixtures containing sensor entity IDs, values and attributes.
- Coordinator messages describing aggregation rules, numbered slot padding, net power measurements, metadata fields, solar channel numbering, stable sensor identities, RSSI validity, and the target version `v0.2.0`.
- Other implementers' messages specifying state/configuration contracts, runtime cache ownership, and failures observed in synthetic tests.

The implementer did not read predecessor core modules, Node-RED flows, historical implementations, upstream source, or stored workspace memories. No predecessor repository files were written. No commit or publication occurred.

`execute_get` fetches devices concurrently, accepts replies with nonempty dictionary `properties`, records actual measurements and health, and preserves missing devices' identities through last-known slot reports. Aggregate measurements and battery packs use healthy eligible devices. Failed slots expose unavailable dynamic values. A successful reply advances `latest_get_ts` and saves a copied report; all-failed cache replies preserve the timestamp. Optional metrics methods are called only when provided.

The coordinator's later review specified runtime mode reporting: explicit POST overrides select the state value, including false; before an override the state or configuration can enable the mode. The implementer read the independently written successor POST module's `_runtime_mode_overrides` contract and added `_mode_enabled` to GET. `dualModeDamper`, `equalMode`, and `alwaysDualMode` report the effective selection. An explicitly disabled damper stops the damper exception to `smartMode` multiplication. `tests/test_get_runtime_mode_flags.py` verifies all three POST-to-GET mode switches for both configuration defaults and verifies damper smart-mode aggregation. That test and the GET aggregation suite passed 17 checks.

`build_proxy_ha_sensors` uses explicit protocol field mappings and descriptor tables. Numbered device sensors cover configured devices up to ten, with compatibility placeholders through slot three. Battery sensors retain the historical IDs for batteries 7 through 18; existing REST sensors supply batteries 1 through 6. Temperatures use `(raw - 2731) / 10`, rounded to one decimal. Battery source slots control availability and successful-read timestamps. Wi-Fi RSSI accepts numeric readings from -120 dBm to values below 0 dBm and marks other readings unavailable.

Verification completed:

- `PYTHONPATH=apps/Zendure-AppDaemon-Proxy python3 -m pytest -q tests/test_node_red_get_response_compat.py tests/test_node_red_repeat_standby_serial_simulated.py tests/test_appdaemon_proxy_release_gate.py`: 101 passed.
- A direct Python comparison verified every value and attribute in all four sensor fixtures, covering 89 historical IDs plus three new RSSI IDs for a three-device report.
- `python3 -m compileall -q apps/Zendure-AppDaemon-Proxy/zendure_proxy_get_handler.py apps/Zendure-AppDaemon-Proxy/zendure_proxy_ha_sensors.py`: passed.
- A synthetic direct-call check verified partial GET aggregation, failed-slot unavailability, preserved serial identities, healthy battery source mapping, unchanged input payloads, cache timestamp preservation, and numbered sensors for device counts 1 through 10.

Verification used synthetic clients and local Python execution. Live Zendure devices, Home Assistant, AppDaemon, MQTT delivery, and HACS installation were not accessed.
