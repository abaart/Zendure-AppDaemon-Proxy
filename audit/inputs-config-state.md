# Configuration and state implementation inputs

The new `zendure_proxy_config.py` and `zendure_proxy_state.py` were written from:

- `audit/data-contract.json`: public dataclass field names, types and defaults.
- `audit/interface-signatures.json`: helper names and function signatures.
- `tests/test_node_red_repeat_standby_serial_simulated.py`: device-list overlay, ten numbered slots, placeholder IPs, invalid-cap warnings, booleans, connection settings, and delay/pause alias behavior.
- Other synthetic compatibility test references to `Config`, `DeviceState`, and `ProxyState` fields and effective capability properties.
- Coordinator-provided compatibility facts: legacy single/dual-mode configuration aliases; positive device capabilities use an 800 W fallback when nonpositive, with a positive configured ceiling applied; `counter_missing` has `device_count` entries when positive and three entries otherwise.

Implementation declarations were manually transcribed from the data contract. Parsing, slot overlay, capability calculations, and counter resizing were written independently. The implementer did not open predecessor core modules, Node-RED exports, upstream repositories, or historical implementations for these modules. A team inventory tool incidentally returned another audit agent's summary; the summary was not an implementation input for configuration or state.

Verification: `compileall` passed for both modules. A direct contract comparison checked every dataclass field and default against `audit/data-contract.json`; alias, device capability and counter-size assertions passed. The configuration subset of `tests/test_node_red_repeat_standby_serial_simulated.py` passed as part of a focused 16-test run.
