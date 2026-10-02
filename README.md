# Zendure AppDaemon Proxy

For Home Assistant users controlling several Zendure batteries as one device, this AppDaemon app combines reports and divides power requests across one to ten devices. Existing automation can keep using the proxy endpoints and sensor identifiers.

Keep the current production proxy while the control and recovery findings in the [feature comparison](audit/feature-parity-review.md) remain open. The published `v0.2.0` passes its existing tests; the follow-up comparison found additional cases that must pass before a live migration.

![Zendure illustration](illustation.png)

## Installation through HACS

1. Set `production_mode: true` under the top-level `appdaemon:` section of AppDaemon's global `appdaemon.yaml`.
2. Add `https://github.com/abaart/Zendure-AppDaemon-Proxy` to HACS as an **AppDaemon** custom repository.
3. Install the release. The Python files belong directly in `/config/appdaemon/apps/Zendure-AppDaemon-Proxy/`.
4. Add the top-level `zendure_proxy:` block from [examples/apps.yaml](examples/apps.yaml) to AppDaemon's `apps.yaml`, with the IP addresses of your devices.
5. Restart the AppDaemon add-on. Repeat the restart after every HACS update.

HACS replaces app files while installing. `production_mode: true` lets the running AppDaemon instance continue until the deliberate restart.

## Configuration and endpoints

The entry point remains `module: zendure_proxy`, `class: ZendureProxy`. Numbered `ip_zendure_1` through `ip_zendure_10` settings remain supported; the `devices` list also accepts per-device charge and discharge limits. The app keeps entity IDs, MQTT topics and discovery identifiers stable.

The default HTTP server listens on port `8120`:

- `GET /properties/report` and `GET /endpoint/properties/report` return a combined report.
- `POST /properties/write` and `POST /endpoint/properties/write` accept existing write payloads.
- AppDaemon API endpoints are `/api/appdaemon/zendure_proxy_report` and `/api/appdaemon/zendure_proxy_write` on port `5050`.

From Home Assistant containers, use `a0d7b954-appdaemon:8120/endpoint` for the community AppDaemon add-on. The exact host depends on the add-on installation.

## Operation

The proxy serializes requests to each physical battery and shares pending report requests. The latest queued write for each property key set replaces earlier pending writes with the same keys. Cached reports and per-device health tracking support temporary connection failures. Standby, balancing, reserve mode and relay saver follow the documented compatibility tests.

A discharge request cannot recruit a battery at or below its protected minimum SoC to cover a capacity shortfall. The remaining batteries provide the available safe power.

Proxy sensors publish changed states at the approved intervals and send periodic heartbeats. Metrics counters restore from Home Assistant at startup. MQTT discovery uses the same device identifier and sensor `unique_id` values as the predecessor. MQTT needs an available broker, Home Assistant integration and AppDaemon MQTT plugin; `set_state` supplies fallback sensors.

[Sensor publication and Recorder](docs/sensors.md) explains the optional examples. [Migration](docs/migration.md) describes a switch with a backup and rollback. [Build and verify](docs/build.md) describes the release archive.

## License and provenance

Source code is released under **GPL-3.0-only**. See [LICENSE](LICENSE), [NOTICE](NOTICE) and the per-file [provenance manifest](audit/provenance.json). The illustration was AI-generated according to the repository owner's confirmation.

The replacement core was written using functional contracts and synthetic tests. The implementation input records describe the separation from predecessor source code. The historical repository remains available at [Zendure-zenSDK-proxy](https://github.com/abaart/Zendure-zenSDK-proxy).
