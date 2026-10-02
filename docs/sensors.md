# Sensors and history

Existing automations can continue to use the established sensor IDs and MQTT discovery identifiers. The proxy publishes numeric states as strings for AppDaemon compatibility and restores metrics counters from Home Assistant before publishing.

Changes to device health publish promptly. Temperature changes are limited to ten-minute publication; Wi-Fi RSSI changes to one minute. Serial numbers, device addresses and the proxy version have a daily heartbeat. Other state sensors have an hourly heartbeat. Queue-depth changes publish at ten seconds and other metric changes at one minute.

The optional `examples/zendure_recorder_policy.yaml` excludes selected diagnostic attributes and limits relay-state history to seven days. Merge the Recorder exclusions with your existing settings. The optional availability package updates sampled temperature sensors when device health changes.

`tools/prepare_gielz_temperature_group.py` edits a user-supplied package to split slower temperature polling from fast control values. Inspect the generated file before installation. The generated package can contain Gielz-origin material under its original terms; the GPL license on this tool does not change those terms.

The relay example measures time in each relay position and counts recorded transitions during the current local day. Recorder retention and restart gaps limit the completeness of those measurements. The network-watchdog example alerts only after its configured persistence and reference-training conditions.
