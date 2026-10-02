# Feature comparison and live-migration readiness

Three read-only audit workers compared predecessor `abaart/Zendure-zenSDK-proxy` at `c0c4153cd68d99ec2cbeef77103cbd5e23ca954a` with successor runtime at `d11a651b079cf2eb06a69f2a8fcafb1d8cefb2cc`. A fourth worker independently repaired the successor watchdog dashboard using the retained owner-authored watchdog package's monitor attributes. The dashboard implementer did not inspect predecessor source or dashboard templates.

The existing successor suite passed 243 pytest cases and 98 unittest cases before the dashboard repair. Of the latest predecessor's 200 named test functions, 199 retain identical bodies; one discharge test has a deliberately stronger safety expectation. Passing those tests did not establish behavior for the additional cases below. The unittest suite is a subset of pytest, rather than additional independent coverage.

**Readiness: resolve the high-priority control, queue, configuration and startup findings before a production migration.** The published `v0.2.0` archive remains unchanged. No physical device commands, user HA installation changes, predecessor file changes, history rewrites, or removal operations were performed during this comparison. The requested repair covers the watchdog dashboard; the runtime findings remain open.

## Control and standby findings

| ID | Priority | Verified trigger and predecessor/successor behavior | Successor function / status |
|---|---|---|---|
| C1 | High | A device has `soc_status=1`. Charging SoCs `[40,60]` at 300 W: predecessor commands `[0,300]`; successor `[300,0]`. Discharging SoCs `[60,40]` has the same selection reversal. | Power/POST direction-eligible selection omits calibration exclusion. Open. |
| C2 | High | `equal_mode=True`, `soc_set=600`, SoCs `[60,70]`, charging 300 W: predecessor `[0,0]`; successor `[150,150]`. | Equal/forced allocation can charge at or above the requested SoC target. Open. |
| C3 | High | An excluded/degraded passive slot has `excluded_since_ts=990`, clock 1000, zero command and fresh GET. Predecessor sends no standby POST; successor sends `smartMode=0` plus zero power. | `_standby_allowed` omits health eligibility. Open. |
| C4 | High | Anti-pingpong is active and a passive device is in `anti_pingpong_paused_idx`. Predecessor protects the device; successor can send standby. | `_protected_indices` omits paused indices. Open. |
| C5 | Medium | Relay-saver minimum drop 900 W, previous command −700 W and new command +300 W: predecessor holds −40 W briefly; successor sends +300 W immediately. | Relay threshold uses previous magnitude rather than magnitude of the command change. Open. |
| C6 | Medium | A 400 W forced device switch at 55%, 73%, and 86% of its transition timer: predecessor `[380,20]`, `[200,200]`, `[100,300]`; successor `[300,100]`, `[300,100]`, `[200,200]`. | Forced-switch transition windows differ. Open. |

The calibration selection comparison covers the reproduced normal selection. The predecessor could also select a calibrating device in some boundary combinations, so reproducing predecessor behavior alone does not establish universal control safety.

Preserve the intentional discharge correction: a low/high pair at 5% and 50%, requesting 1200 W with 800 W caps, previously received `[400,800]`; the successor sends `[0,800]`. Protected low batteries must remain excluded when healthy capacity is insufficient.

## Requests, configuration and startup findings

| ID | Priority | Verified trigger and predecessor/successor behavior | Successor function / status |
|---|---|---|---|
| R1 | High | A report and a queued stop command share a batch, with repeat eligible. Predecessor runs GET then zero POST; successor runs GET, repeats stale 1000 W, then zero POST. | `_process_batch` repeats while a newer POST is pending. Open. |
| R2 | High | GET raises with an independent POST in the same batch. Predecessor still executes POST; successor fails all batch futures and skips POST. | `_processor` / `_process_batch` fail independent writes together with the report. Open. |
| R3 | High | A configured device cap is string `"800.0"` or `"500.9"`. Predecessor accepts 800/500 W; successor rejects the cap and uses hardware capacity. | `_parse_positive_int` can discard an existing ceiling, permitting a larger hardware limit. Open. |
| R4 | High | A valid numbered IP is combined with a placeholder `devices[].ip`. Predecessor keeps the valid IP; successor removes the slot, potentially shifting later device identities. | `_load_device_slots` replaces the valid IP before placeholder filtering. Open. |
| R5 | Medium | `anti_pingpong_activation_mode: " SMART "`. Predecessor normalizes to `smart`; successor retains the raw value and skips smart timer registration. | `load_config` and `_initialize_runtime`; parsing reproduced, timer consequence statically checked. Open. |
| R6 | Medium | Gielz compatibility endpoint receives empty path: predecessor GET reports and POST writes; successor returns 404. Successor also accepts GET with write path and POST with report path. | `_api_gielz_compat` changes default dispatch and method checks. Open. |
| R7 | High | One HA counter read raises, another returns 37. Predecessor logs the failure and restores 37; successor raises and restores no counters. | `_restore_metrics_counters_from_ha` propagates the exception before endpoint registration. Open. |
| R8 | Medium | A queued GET future raises despite a fresh cache. Predecessor returns HTTP 200 cached data; successor returns HTTP 502. | Runtime GET exception fallback differs. Open. |

### Behavioral choices and additional compatibility differences

- **One in-flight request:** with `separate_get_post_connections: true`, the predecessor runs one GET and one POST concurrently. The successor uses separate sessions but one dispatcher. A blocked GET delays POST until GET finishes, potentially up to the device timeout. The successor follows the plan's one-in-flight requirement, while changing predecessor latency semantics. Resolve the conflicting expectations explicitly; do not silently restore parallel device writes. Duplicate configured IPs create separate clients in both implementations, so neither guarantees physical-IP serialization in that case.
- **POST group ordering:** queued `inputLimit=10`, `outputLimit=20`, `inputLimit=30` executes as latest input then output in the predecessor, but output then latest input in the successor. Deduplication moves a group to its latest occurrence. Final direction can differ. Select and document the required ordering.
- **Deduplicated responses:** predecessor skipped callers receive an immediate acknowledgement. Successor callers wait for the final actual response. A successor resilience test explicitly verifies the latter policy.
- **Error responses:** successor HTTP 503/502/504 responses replace several predecessor unconditional HTTP 200 acknowledgements. Those stricter responses are already recorded in `audit/inputs-runtime.md`.
- **Serial bootstrap:** successor waits for bootstrap before starting its queued POST response timer. Bootstrap holds the upstream lock, so total response time can exceed that timer; maximal timing was not simulated.
- **Other config overlays:** replacing a list IP retains unspecified numbered caps in the successor; the predecessor replaces the slot record. Cap-only list entries are accepted by the successor and ignored by the predecessor. General string stripping and unusual boolean parsing also differ.
- **HTTP success:** predecessor accepts upstream HTTP 200; successor accepts status below 400 with a dictionary body. Schema-containing device addresses and proxy-loop checks differ.
- **MQTT listener failure:** predecessor catches a reconnect-listener registration failure; successor can abort initialization. This consequence was checked statically, without a broker failure probe.

## Report and sensor findings

| ID | Priority | Verified trigger and predecessor/successor behavior | Successor function / status |
|---|---|---|---|
| S1 | High | Healthy slot modes `[1,2]`, active index `[1]`, slot 1 input limit 100 and slot 2 output limit 300. Predecessor reports `acMode=2`, `latestPowerCmd=-300`; successor `acMode=1`, `latestPowerCmd=-200`. | `build_combined_response` chooses first healthy mode and combines inactive commands. Open. |
| S2 | Medium | Unknown properties include firmware `"one"` / `"two"` and arbitrary values 12 / 99. Predecessor forwards `"one"` and 12; successor returns 0 and 99. | Generic integer/MAX aggregation destroys unknown nonnumeric values and changes passthrough semantics. Open. |
| S3 | Medium | A report omits `hyperTmp`, or a two-device report pads slot 3. Successor publishes −273.1°C; predecessor 0°C. Padded Offgrid/SoC-limit labels also change. | Numbered defaults and `build_proxy_ha_sensors` convert missing data as a real temperature. Open. |
| S4 | Medium | A failed slot has an expired cached `solarPower1=21`. Predecessor numbered solar field is zero; successor leaks cached 21. | Numbered solar projection does not apply eligible-slot filtering. Open. |
| S5 | Medium | Optional calibration data is absent. Predecessor always supplies aggregate `batCalTime=0`; successor omits the field. Successor also omits `properties.ts` and some padded cap fields. | Combined response compatibility defaults are incomplete. Open. |

`_recalculate_aggregate_device_limits` changes stored `max_power_in/out` for caps `[800,2400]` from 2400 to 800. No reduced allocation was demonstrated: successor allocation uses per-device limits and retained `_service_capacity` ignores its `max_power` argument. This is a state-contract difference requiring investigation, rather than a proven 800 W output restriction.

Other differences include unconfigured/recovering health labels, idle-mode icon, unknown versus unavailable missing-pack states, active-device formatting, malformed battery-order handling, and omitted publication metadata such as `proxy_version`. Stable MQTT topics, `unique_id`, `default_entity_id`, device identifier, publication intervals/heartbeats and metrics counter definitions remain preserved. MQTT human-readable product name and software-version metadata changed deliberately.

## Examples, deliberate omissions and dashboard repair

The Gielz transformation tool and temperature-availability automation retain their functions. The new relay-history example preserves six count entities and uses the current local-day window; three computed daily-total example entities are absent. That example difference needs a documented migration decision. Node-RED flows, simulator, copied REST/socket YAML, predecessor pictures and funding configuration were deliberately excluded by the GPL plan.

The watchdog automation remains retained owner-authored material. The dashboard repair adds persistent connection error details, Wi-Fi current/frozen-reference measurements and duration, combined incidents across devices, learning status and learned average. Missing, stale, paused and malformed optional monitor data receive explicit availability messages. Numeric RSSI cards and existing sensor IDs remain intact. Template tests exercise the actual new card content; independent implementation inputs are recorded in `audit/inputs-watchdog-dashboard.md`.

## Evidence and limits

Control and runtime audits used old/new modules loaded in isolated memory, synthetic state and fake AppDaemon/HTTP boundaries. Report audit evidence is retained locally in ignored `build/parity/sensors.json`, with six old/new report scenarios and a metrics-restore probe; the file contains generated inputs/outputs rather than copied implementation source. The dashboard used the separately running, task-owned HA test instance, not the user's HA.

The audit does not exhaust every state sequence, timing race, unusual configuration or device firmware response. It does not test real-device traffic, long physical HTTP timeouts, actual MQTT delivery, every reconnect/shutdown race, or production Supervisor restart timing. The comparison identifies concrete uncovered differences; it does not establish complete feature parity.

The completed dashboard repair passed compile/import, the full 250-case pytest suite and 105-case unittest suite. Seven new card tests cover missing, healthy, learning, individual and combined incidents, invalid values and stale sources. Actual isolated HA template rendering and desktop/390-pixel phone screenshots confirmed visible incidents, learning status, existing RSSI cards and working History/Diagnostics navigation. The minimal test HA lacks the History integration, so historical graph data was not verified. All runtime findings above remain open despite the green suite.
