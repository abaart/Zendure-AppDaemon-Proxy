# Sensors and history

Existing automations can continue to use the established sensor IDs and MQTT discovery identifiers. The proxy publishes numeric states as strings for AppDaemon compatibility and restores metrics counters from Home Assistant before publishing.

Changes to device health publish promptly. Temperature changes are limited to ten-minute publication; Wi-Fi RSSI changes to one minute. Serial numbers, device addresses and the proxy version have a daily heartbeat. Other state sensors have an hourly heartbeat. Queue-depth changes publish at ten seconds and other metric changes at one minute.

The optional `examples/zendure_recorder_policy.yaml` excludes selected diagnostic attributes and limits relay-state history to seven days. Merge the Recorder exclusions with your existing settings. The optional availability package updates sampled temperature sensors when device health changes.

`tools/prepare_gielz_temperature_group.py` edits a user-supplied package to split slower temperature polling from fast control values. Inspect the generated file before installation. The generated package can contain Gielz-origin material under its original terms; the GPL license on this tool does not change those terms.

The relay example counts recorded charging and discharging state occurrences during the current local day. An interval already active at midnight can count toward the new day. Use the proxy metrics counters when you need measured relay edges; Recorder retention and restart gaps limit history-based counts. The network-watchdog example alerts only after its configured persistence and reference-training conditions.

The native dashboard Overview reads the optional `sensor.zendure_1_network_monitor` through `sensor.zendure_3_network_monitor` attributes from the network-watchdog package. The status card shows persistent connection incidents with errors per hour and the required observation hours, and Wi-Fi incidents with current RSSI, the frozen pre-incident reference and the required duration. The card also shows Wi-Fi reference learning or the learned 24-hour average. Missing monitor entities display an availability message; stale or paused monitoring waits for fresh data. The existing numeric Wi-Fi RSSI cards remain available without the watchdog package. History and Diagnostics links open the dashboard's existing views.
