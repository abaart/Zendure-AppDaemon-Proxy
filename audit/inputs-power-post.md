# Independent power and POST implementation inputs

The implementation worker wrote `zendure_proxy_power.py` and
`zendure_proxy_post_handler.py` in the new repository with SPDX identifier
`GPL-3.0-only`. A license identifier records the chosen distribution terms;
no automatic copyright ownership claim or legal clean-room certification is made.

## Sources supplied to the implementation worker

- `audit/interface-signatures.json`: callable names and argument contracts.
- `audit/data-contract.json`: configuration/state field names and defaults.
- `audit/IMPLEMENTATION_INPUTS.md`: implementation separation requirements.
- `tests/test_node_red_power_distribution_compat.py`,
  `tests/test_node_red_post_power_compat.py`,
  `tests/test_node_red_repeat_standby_serial_simulated.py`,
  `tests/test_appdaemon_proxy_release_gate.py`, `tests/conftest.py` and
  `tests/node_red_expected.py`: synthetic, owned regression contracts.
- The separately audited retained `zendure_proxy_health.py` and
  `zendure_proxy_anti_pingpong.py`: health result recording, public reply
  sanitization, reserve selection, minimum control power and mode delay APIs.
- Mathematical behavior clarification from the coordinator: ordinary shares
  use direction headroom raised to `balancing_factor`, multiplied by device
  capacity; proportional clipping redistributes floating shares before integer
  flooring. Boundary shares start with up to 100 W per selected device and
  divide remaining watts by `1 + max(selected SoCs) - SoC` for charging, or
  `1 + SoC - min(selected SoCs)` for discharging. Fractional remainders prefer
  the largest fraction, then higher weight, then earlier selected position.
- Transition behavior clarification from the coordinator: transition startup
  requires an eligible original device and capacity for 95% of the aggregate
  request; boundary and forced-all distribution clear transition timers.

The worker did not read predecessor core modules, Node-RED implementations,
upstream source, upstream history, or predecessor history. Test filenames
containing `node_red` identify regression requirements; the new modules were
not translated from Node-RED code.

## Safety correction to one inherited regression expectation

The previous synthetic test
`test_discharging_one_device_below_min_soc_holds_low_device_back` used SoCs
`[8, 50]`, `min_soc=100`, device capacities `800 W`, and a `1000 W` discharge
request. The previous assertion expected active devices `[1, 0]` and physical
commands `[200, 800]`, allowing the 8% battery to discharge when the healthy
battery could provide only 800 W.

The coordinator explicitly authorized replacing the expectation with the
requested safety invariant: discharge eligibility requires
`electric_level > max(min_soc / 10, 10)`. The test is now named
`test_discharging_excludes_low_device_when_healthy_capacity_is_insufficient`
and requires active devices `[1]`, active count `1`, and commands `[0, 800]`.
The test strengthens the safety contract; the requested capacity shortfall
remains unserved rather than reintroducing the low battery.

## Implemented behavior

- Monotonic duration timestamps through `now()`; wall clock through `epoch()`.
- `PROXY_VERSION = "v0.2.0"` for new runtime/report consumers.
- Weighted and equal allocation for one through ten devices, with reported
  hardware caps and configured caps enforced for every outgoing power key.
- SoC discharge exclusion, low-SoC charging priority, high-SoC selection,
  low-power charging rotation, and fresh measured charging shortfall
  compensation. Compensation increases commands on other healthy selected
  devices within caps; discharge shortfalls do not enable low batteries.
- Durable runtime toggles, aggregate limit division, requested power signs,
  explicit zero keys, previous direction selection, standby suppression,
  same-payload wake commands and repeat timestamp handling.
- Capacity-gated single-to-dual and device-switch transitions applied to POST
  commands. Boundary and forced-all requests clear transition timers.
- Reserve selection and mode delays using the retained audited helpers;
  optional relay hold timers preserve their original expiry across repeated
  zero commands and obey SoC constraints and physical caps.
- Failed POST replies update device health and exclude the failed device from
  subsequent writes. Internal failure metadata is removed from public replies.
- Empty direction-eligible pools return zero power safely.

Degraded-device held-power subtraction was considered and intentionally left
out on coordinator instruction: the preserved POST compatibility contract
allocates the aggregate request to the eligible pool. The new worker did not
silently introduce a different degraded-power accounting policy.

## Verification

The following new file adds fourteen safety/runtime cases:
`tests/test_independent_power_safety.py`. Cases cover low discharge SoC values
`0`, `8`, and `10` across ordinary, equal, and forced distribution; POST
transition integration; runtime disabling of configured equal mode; and fully
SoC-blocked charge/discharge pools.

Executed successfully:

```sh
python3 -m pytest -q tests/test_independent_power_safety.py tests/test_node_red_power_distribution_compat.py tests/test_node_red_post_power_compat.py tests/test_node_red_repeat_standby_serial_simulated.py tests/test_appdaemon_proxy_release_gate.py
```

Result: **188 passed**. The command verifies local synthetic and mocked runtime
behavior. Live Zendure devices, Home Assistant deployment, and publication were
outside the implementation worker's scope.
