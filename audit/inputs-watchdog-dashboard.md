# Watchdog dashboard inputs and verification

The Overview watchdog status was written independently for the successor's native Home Assistant dashboard. Inputs were the successor `AGENTS.md`, `apps/Zendure-AppDaemon-Proxy/dashboard.yaml`, `examples/zendure_network_watchdog.yaml`, `docs/sensors.md`, and the successor's synthetic watchdog and artifact tests. The coordinator supplied the requested visible behavior. Predecessor Python modules, Node-RED flows, predecessor dashboard source, upstream proxy code and repository history were not implementation inputs.

The monitor package provides one `monitor` mapping on each `sensor.zendure_N_network_monitor`, for slots 1 through 3. The dashboard reads `connection_alert`, `errors_last_hour`, `connection_hours`, `wifi_alert`, `rssi`, `wifi_reference`, `rssi_minutes`, `baseline_ready`, `average_rssi`, `source_running`, and `checked_at`. Alert flags must be booleans; measurements and durations must be finite and valid. Missing mappings and unavailable entities display an optional-monitor message. Paused sources and checks older than five minutes display a wait-for-data message. Wi-Fi learning appears only with a running, recent monitor whose reference is not ready. The existing numeric RSSI cards and sensor IDs remain unchanged.

The card reports each device's persistent connection and Wi-Fi incidents separately, preserving both causes and multiple affected devices. Wi-Fi incident text uses the frozen pre-incident `wifi_reference`; the learned 24-hour average is identified separately. Guidance names the access point, Wi-Fi range and Zendure device. `./history` and `./metrics` links open the dashboard's existing views. An actual HA browser check found that bare relative links were sanitized to an empty destination; the explicit `./` links were then verified by clicking both destinations.

Verification commands:

- `.venv/bin/python -m pytest -q tests/test_dashboard_watchdog.py tests/test_network_watchdog.py tests/test_release_artifacts.py`: 17 passed.
- `.venv/bin/python -m unittest discover -s tests -p test_dashboard_watchdog.py`: 7 passed.

The new dashboard tests render the actual YAML template with strict undefined handling. Cases cover absent monitors, healthy and learning devices, connection-only and Wi-Fi-only incidents, frozen-reference selection, combined causes across multiple devices, paused/stale/unavailable sources, malformed mappings, nonnumeric and out-of-range RSSI, negative error counts and nonboolean alert flags.

The existing isolated HA 2026.9.4 instance rendered the actual template through its authenticated template REST API for missing, healthy, combined incidents, multiple devices and learning cases. Synthetic monitor states were written only to the test HA instance. The previous test dashboard and three prior monitor states were preserved under ignored `build/sandbox/hacs-6540a49fe8/watchdog-dashboard/`. Existing test credentials were refreshed locally without disclosure.

The actual native dashboard displayed Device 1's combined connection/Wi-Fi incident, Device 2's connection incident, and Device 3's reference-learning status. Numeric RSSI cards remained visible. Desktop and 390-by-844 phone screenshots are `desktop-incidents.jpg` and `mobile-incidents.jpg` in the same ignored test directory. The phone document width and scroll width were both 390 pixels. History and Diagnostics navigation succeeded; history graphs were unavailable because the isolated HA configuration has no History integration. Temporary viewport overrides were cleared after verification.

Changes are limited to the successor dashboard, focused tests, sensor documentation and this input record. No runtime module, user live HA state, predecessor repository, commit, push, tag or release was changed by the implementer. The isolated synthetic monitor states remain available for review.
