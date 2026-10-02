# SPDX-License-Identifier: GPL-3.0-only
"""Safety invariants beyond the synthetic compatibility examples."""
import asyncio

import pytest

from conftest import FakeDeviceClient
from zendure_proxy_config import Config
from zendure_proxy_power import now
from zendure_proxy_post_handler import execute_post
from zendure_proxy_state import DeviceState, ProxyState


@pytest.mark.parametrize('soc', [0, 8, 10])
@pytest.mark.parametrize('equal,forced', [(False, False), (True, False), (False, True)])
def test_discharge_boundary_remains_excluded_in_every_distribution_mode(soc, equal, forced):
    state = ProxyState(device_count=2, ac_mode=2, min_soc=50,
                       devices=[DeviceState(sn='LOW', electric_level=soc), DeviceState(sn='HIGH', electric_level=50)])
    state.forced_dual_transition_start_ts = now()
    state.forced_dual_transition_original_device = 0
    clients = [FakeDeviceClient(), FakeDeviceClient()]
    asyncio.run(execute_post({'properties': {'acMode': 2, 'outputLimit': 1600}}, clients, state,
                            Config(device_ips=['low', 'high'], equal_mode=equal, always_dual_mode=forced), lambda *args, **kwargs: None))
    assert clients[0].post_payloads[0]['properties']['outputLimit'] == 0
    assert clients[1].post_payloads[0]['properties']['outputLimit'] <= 800
    assert state.devices_active_idx == [1]
    assert state.forced_dual_transition_start_ts == 0


def test_normal_single_to_dual_transition_is_applied_to_post_commands():
    state = ProxyState(device_count=2, ac_mode=1, latest_power_cmd=300,
                       device_active_count=1, devices_active_idx=[0],
                       devices=[DeviceState(sn='A', electric_level=50, latest_power_cmd=300), DeviceState(sn='B', electric_level=50)])
    clients = [FakeDeviceClient(), FakeDeviceClient()]
    asyncio.run(execute_post({'properties': {'acMode': 1, 'inputLimit': 500}}, clients, state,
                            Config(device_ips=['a', 'b'], single_mode_upper_pct=50), lambda *args, **kwargs: None))
    assert state.single_to_dual_transition_start_ts > 0
    assert [client.post_payloads[0]['properties']['inputLimit'] for client in clients] == [475, 25]


def test_normal_single_device_switch_keeps_old_and_new_devices_active():
    state = ProxyState(device_count=2, ac_mode=1, latest_power_cmd=300,
                       device_active_count=1, devices_active_idx=[0],
                       devices=[DeviceState(sn='A', electric_level=70, latest_power_cmd=300), DeviceState(sn='B', electric_level=40)])
    clients = [FakeDeviceClient(), FakeDeviceClient()]
    asyncio.run(execute_post({'properties': {'acMode': 1, 'inputLimit': 300}}, clients, state,
                            Config(device_ips=['a', 'b']), lambda *args, **kwargs: None))
    assert state.forced_dual_transition_start_ts > 0
    assert state.device_active_count == 2
    assert state.devices_active_idx == [0, 1]
    assert [client.post_payloads[0]['properties']['inputLimit'] for client in clients] == [285, 15]


def test_runtime_equal_mode_can_disable_configured_equal_mode():
    cfg = Config(device_ips=['a', 'b'], equal_mode=True)
    state = ProxyState(device_count=2, ac_mode=1, equal_mode=True,
                       devices=[DeviceState(sn='A', electric_level=50), DeviceState(sn='B', electric_level=50)])
    clients = [FakeDeviceClient(), FakeDeviceClient()]
    asyncio.run(execute_post({'properties': {'equalMode': 0}}, clients, state, cfg, lambda *args, **kwargs: None))
    asyncio.run(execute_post({'properties': {'acMode': 1, 'inputLimit': 300}}, clients, state, cfg, lambda *args, **kwargs: None))
    assert state.equal_mode is False
    assert [client.post_payloads[0]['properties']['inputLimit'] for client in clients] == [300, 0]


@pytest.mark.parametrize('mode,power_key', [(1, 'inputLimit'), (2, 'outputLimit')])
def test_all_devices_at_direction_soc_limit_receive_zero(mode, power_key):
    state = ProxyState(device_count=2, ac_mode=mode,
                       devices=[DeviceState(sn='A', electric_level=50, soc_limit=mode), DeviceState(sn='B', electric_level=50, soc_limit=mode)])
    clients = [FakeDeviceClient(), FakeDeviceClient()]
    asyncio.run(execute_post({'properties': {'acMode': mode, power_key: 500}}, clients, state,
                            Config(device_ips=['a', 'b']), lambda *args, **kwargs: None))
    assert state.device_active_count == 0
    assert state.devices_active_idx == []
    assert [client.post_payloads[0]['properties'][power_key] for client in clients] == [0, 0]


def _damper_case(*, mode=2, previous_count=1, upper_pct=100, reserve=False):
    state = ProxyState(device_count=2, ac_mode=mode, latest_power_cmd=600 if mode == 1 else -600,
                       device_active_count=previous_count,
                       devices_active_idx=[0] if previous_count == 1 else [0, 1],
                       anti_pingpong_active=reserve,
                       devices=[DeviceState(sn='A', electric_level=50), DeviceState(sn='B', electric_level=50)])
    cfg = Config(device_ips=['a', 'b'], damper_enable=True,
                 single_mode_upper_pct=upper_pct, anti_pingpong_enable=reserve,
                 anti_pingpong_activation_mode='smart')
    return state, cfg, [FakeDeviceClient(), FakeDeviceClient()]


def test_damper_allows_charging_request_to_use_two_devices():
    state, cfg, clients = _damper_case(mode=1)
    asyncio.run(execute_post({'properties': {'acMode': 1, 'inputLimit': 900}}, clients, state, cfg, lambda *args, **kwargs: None))
    assert state.dualmode_damper_active is False
    assert state.device_active_count == 2
    assert [client.post_payloads[0]['properties']['inputLimit'] for client in clients] == [450, 450]


def test_discharge_damper_holds_configured_percentage_of_active_device_cap():
    state, cfg, clients = _damper_case(upper_pct=80)
    asyncio.run(execute_post({'properties': {'acMode': 2, 'outputLimit': 690}}, clients, state, cfg, lambda *args, **kwargs: None))
    assert state.dualmode_damper_active is True
    assert state.device_active_count == 1
    assert [client.post_payloads[0]['properties']['outputLimit'] for client in clients] == [640, 0]


def test_discharge_damper_keeps_already_active_two_device_distribution():
    state, cfg, clients = _damper_case(previous_count=2)
    asyncio.run(execute_post({'properties': {'acMode': 2, 'outputLimit': 900}}, clients, state, cfg, lambda *args, **kwargs: None))
    assert state.dualmode_damper_active is False
    assert state.device_active_count == 2
    assert [client.post_payloads[0]['properties']['outputLimit'] for client in clients] == [450, 450]


def test_discharge_damper_skips_active_reserve_policy_capacity_fallback():
    state, cfg, clients = _damper_case(reserve=True)
    asyncio.run(execute_post({'properties': {'acMode': 2, 'outputLimit': 900}}, clients, state, cfg, lambda *args, **kwargs: None))
    assert state.anti_pingpong_last_reason == 'service_capacity'
    assert state.dualmode_damper_active is False
    assert state.device_active_count == 2
    assert [client.post_payloads[0]['properties']['outputLimit'] for client in clients] == [450, 450]


def test_discharge_damper_expires_and_allows_two_device_commands(monkeypatch):
    import zendure_proxy_post_handler as post_handler

    current_ts = [100.0]
    monkeypatch.setattr(post_handler, 'now', lambda: current_ts[0])
    state, cfg, clients = _damper_case(upper_pct=80)
    cfg.damper_timer = 10
    asyncio.run(execute_post({'properties': {'acMode': 2, 'outputLimit': 690}}, clients, state, cfg, lambda *args, **kwargs: None))
    assert state.dualmode_damper_active is True
    current_ts[0] = 111.0
    asyncio.run(execute_post({'properties': {'acMode': 2, 'outputLimit': 690}}, clients, state, cfg, lambda *args, **kwargs: None))
    assert state.dualmode_damper_active is False
    assert state.device_active_count == 2
    assert all(client.post_payloads[-1]['properties']['outputLimit'] > 0 for client in clients)


def test_runtime_damper_toggle_can_disable_configured_damper():
    state, cfg, clients = _damper_case()
    asyncio.run(execute_post({'properties': {'dualModeDamper': 0}}, clients, state, cfg, lambda *args, **kwargs: None))
    asyncio.run(execute_post({'properties': {'acMode': 2, 'outputLimit': 900}}, clients, state, cfg, lambda *args, **kwargs: None))
    assert state.dualmode_damper_active is False
    assert state.device_active_count == 2
    assert [client.post_payloads[0]['properties']['outputLimit'] for client in clients] == [450, 450]
