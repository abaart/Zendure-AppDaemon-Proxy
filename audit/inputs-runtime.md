# Runtime implementation inputs

`apps/Zendure-AppDaemon-Proxy/zendure_proxy.py` was written independently for version `0.2.0` under `GPL-3.0-only`.

The implementer read:

- `audit/IMPLEMENTATION_INPUTS.md` for implementation separation.
- `audit/interface-signatures.json` for runtime, queue, device-client and standby function signatures.
- `audit/data-contract.json` for configuration defaults and state fields.
- `tests/test_appdaemon_proxy_release_gate.py` and `tests/test_node_red_repeat_standby_serial_simulated.py` for synthetic request, publication, direct/awaitable AppDaemon API, reconnect, serial retry, cache, repeat and standby behavior.
- The separately audited retained modules `zendure_proxy_publication.py`, `zendure_proxy_health.py`, `zendure_proxy_anti_pingpong.py`, `zendure_proxy_metrics.py`, `zendure_proxy_logging.py` and `zendure_proxy_mqtt_discovery.py` for their integration interfaces.
- Newly written replacement `Config`, state, queue, GET and sensor modules for integration contracts. Other implementers supplied queue batching tuples and GET cache ownership details.

The coordinator supplied additional functional facts: the Gielz compatibility endpoint is named `zendure_proxy`; metrics timer uses the smaller of the configured interval and ten seconds; sensor refresh runs every 300 seconds, heartbeats every 60 seconds and standby checks every ten seconds; repeat requires an original command at least 30 seconds old, a repeat at least 20 seconds old and two eligible devices; immediate health publication covers every per-device status and identity sensor plus aggregate command/active-device sensors.

The runtime implementer did not read predecessor Python core modules, Node-RED flows, upstream implementations, original repository history or an implementation translation supplied by another agent. The coordinator inspected predecessor source during provenance work; the separation record is evidence of the implementation inputs, not legal certification.

## Added verification

`tests/test_runtime_resilience.py` checks resource shutdown using awaitable AppDaemon APIs, in-flight and queued caller cancellation, continued request processing after an unexpected GET failure, latest-payload POST deduplication with individual caller responses, preservation of the cache measurement timestamp after an upstream failure, repeat timing and eligibility, immediate health transitions on existing REST sensor entities, continuation of shutdown after one resource cleanup fails, and resource cleanup after server initialization fails.

These checks exercise synthetic boundaries. The tests do not establish successful communication with live Zendure hardware or a running Home Assistant instance.

## Unspecified public details

The synthetic input contract does not specify every malformed-write response string, the HTML design of diagnostics, or serial-bootstrap error wording. The replacement runtime uses explicit JSON errors with HTTP 400 for invalid input, 503 for missing configured devices or serial numbers, 504 for an expired GET cache or POST timeout, and 502 for an unexpected processor failure. The documented report/write URLs, API endpoint names and compatibility sensor identifiers remain fixed.

## Additional runtime settings and startup facts

The coordinator supplied the functional requirements that `metrics_enabled: false` prevents metrics counter restoration, HA publication, metrics route registration and metrics timers; collection may still occur inside `MetricsRegistry`. Initialization starts `_init_serial_numbers()` in a separately tracked background task after the processor starts, and termination cancels and awaits the bootstrap task. Initial `dualmode_damper_enabled` comes from `Config.damper_enable`.

The implementer inspected the installed AppDaemon 4.5.13 `ADAPI.config_dir` property, which returns `self.AD.config_dir` as a `Path`. With a synthetic `AD` object and patched file-logger constructor, the real imported `Hass` subclass selected `<config_dir>/logs/zendure_proxy.log`. The check created no log file and did not connect to Home Assistant. Added synthetic tests verify the same directory derivation, disabled metrics behavior, damper initialization and nonblocking bootstrap cancellation.
