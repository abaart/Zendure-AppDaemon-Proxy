# SPDX-License-Identifier: GPL-3.0-only
import asyncio

import pytest

from conftest import device_response
from zendure_proxy_config import Config
from zendure_proxy_get_handler import _update_device_state, build_combined_response
from zendure_proxy_post_handler import execute_post
from zendure_proxy_state import DeviceState, ProxyState


@pytest.mark.parametrize("protocol,state_attr,config_attr", [
    ("equalMode", "equal_mode", "equal_mode"),
    ("alwaysDualMode", "always_dual_mode", "always_dual_mode"),
    ("dualModeDamper", "dualmode_damper_enabled", "damper_enable"),
])
@pytest.mark.parametrize("configured", [False, True])
def test_post_mode_override_is_reported_by_get(protocol, state_attr, config_attr, configured):
    cfg = Config(device_ips=["ip1"], **{config_attr: configured})
    state = ProxyState(device_count=1, devices=[DeviceState(ip="ip1")])
    reports = [device_response(1, "SN1")]
    _update_device_state(0, reports[0], state)
    assert build_combined_response(reports, state, cfg)["properties"][protocol] == int(configured)
    setattr(state, state_attr, True)
    assert build_combined_response(reports, state, cfg)["properties"][protocol] == 1
    for requested in (0, 1, 0):
        asyncio.run(execute_post({"properties": {protocol: requested}}, [], state, cfg, lambda *a, **k: None))
        assert build_combined_response(reports, state, cfg)["properties"][protocol] == requested


def test_disabled_damper_override_stops_temporary_smart_mode_aggregation():
    cfg = Config(device_ips=["ip1", "ip2"], damper_enable=True)
    state = ProxyState(device_count=2, devices=[DeviceState(ip="ip1"), DeviceState(ip="ip2")])
    reports = [device_response(1, "SN1", properties={"smartMode": 1}),
               device_response(2, "SN2", properties={"smartMode": 0})]
    for idx, report in enumerate(reports):
        _update_device_state(idx, report, state)
    state.dualmode_damper_active = True
    assert build_combined_response(reports, state, cfg)["properties"]["smartMode"] == 1
    asyncio.run(execute_post({"properties": {"dualModeDamper": 0}}, [], state, cfg, lambda *a, **k: None))
    assert build_combined_response(reports, state, cfg)["properties"]["smartMode"] == 0
