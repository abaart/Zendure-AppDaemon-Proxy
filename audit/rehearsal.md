# Isolated HA, AppDaemon and HACS rehearsal

The successor was tested with three synthetic HTTP devices in a new isolated configuration. Home Assistant 2026.9.4 ran in a task-owned Docker container; AppDaemon 4.5.13 ran as a host process with `production_mode: true`. All exposed test ports bound to loopback. Existing containers and the user's live Home Assistant configuration were unchanged.

The exact tested release archive SHA-256 is `21c8c333c74fad0731d267dad4bf7e1aa83ec49ec2ceeeb85e2ed7037c231f67`. The archive contains sixteen Python modules, `LICENSE`, and `NOTICE` at its root. Actual GET requests returned HTTP 200, `v0.2.0`, and `3x Zendure via PROXY`. Actual POST-to-GET checks enabled and disabled `equalMode`, `alwaysDualMode`, and `dualModeDamper`; all six requested values appeared in fresh reports. The one-second GET rate-limit cache was respected during verification.

Home Assistant received the synthetic health, SoC, power and RSSI sensors. The native `dashboard.yaml` Overview and Diagnostics views displayed resolved entities. The logs, metrics and diagnostics AppDaemon routes returned HTTP 200. A separate restart with `metrics_enabled: false` returned a successful report without runtime errors. After restoring the test configuration and restarting AppDaemon, the Home Assistant GET counter restored its previous value of 72 and the report returned HTTP 200. Synthetic release tests separately exercise AppDaemon methods with both direct and awaitable results; the final coordinator run passed 243 pytest checks and 98 unittest checks.

Official HACS 2.0.5 ran inside the test Home Assistant instance. HACS registered `abaart/Zendure-AppDaemon-Proxy` as category `appdaemon`, repository ID `1402331836`, with destination `/config/appdaemon/apps/Zendure-AppDaemon-Proxy`. The initial operational checks installed the built archive locally. The actual HACS download from the GitHub `v0.2.0` release remains pending publication; local installation does not establish GitHub release-download success.

The HACS test entry reused the existing local GitHub authentication token for GitHub reads instead of requesting a new OAuth grant. The token and generated sandbox owner credentials remain in restricted local ignored test files and were omitted from audit artifacts. HACS used its normal GitHub clients. No credentials were sent to another service.

The preceding `v0.1.31` archive was treated as an opaque operational artifact and extracted solely inside the ignored test directory. Actual AppDaemon processes served both versions with one controller folder inside `apps`. Test-owned directories were moved and preserved outside `apps` during each switch. The measured old-to-new switch took 1.064 seconds; new-to-old rollback took 4.318 seconds. Both measurements use monotonic time from the last successful source report to the first validated target report. The measurements cover host AppDaemon processes; production HA add-on restart duration was not measured.

Official HACS `HacsAppdaemonRepository.validate_repository` was executed against an ephemeral reference-only default-branch object. The common metadata stage was stubbed and the `apps` lookup returned a simulated GitHub 404. The actual AppDaemon content validation rejected the branch with `Repository structure for main is not compliant`. Retaining the predecessor's `apps` and HACS metadata with a README migration notice preserves AppDaemon discovery compatibility. The predecessor GitHub repository was unchanged.

The actual Home Assistant template REST API rendered all six relay-history example sensors' shared live start/end expressions. Controlled local-day boundary checks replaced the clock expressions with timezone-aware datetime inputs at 23:59:59, 00:00:00 and 00:00:01. The resulting intervals were 86399, 0 and 1 seconds; every start was at or before its end. The example uses `type: count`, which counts matching recorded intervals and can count an ongoing interval. The count does not measure an exact physical relay-edge total. No production Recorder history was queried.

Screenshots and machine-readable evidence are retained locally under ignored `build/sandbox/hacs-6540a49fe8/`:

- `native-ha-desktop.jpg` and `native-ha-mobile.jpg`: actual native Overview cards.
- `metrics-desktop-final.jpg` and `metrics-mobile-final.jpg`: actual AppDaemon metrics page.
- `final-asset-validation.json`, `final-asset-report.json`, `final-asset-states.json`, `timed-switches.json`, `relay-template-validation.json`, and `restart-counter-validation.json`: operational results.

At the 390-pixel phone viewport, both native HA and metrics pages had document width and scroll width of 390 pixels. Metrics table wrappers were 348 pixels wide; the wider incoming and outgoing tables scrolled inside the wrappers. Temporary browser viewport overrides were reset after screenshots.
