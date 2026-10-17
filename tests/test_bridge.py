"""The bridge (bridge.py): the DME run from a car's readings and answering
with what the car's engine model needs, and the datagrams it speaks."""
import json

import pytest

from ms43emu import find_image, load_image, bridge, plant


@pytest.fixture(scope="module")
def dme():
    if find_image("stock") is None:
        pytest.skip("no stock image")
    b = bridge.Bridge(load_image("stock"))
    b.run(1.0)                                   # key on, the throttle learnt
    return b


def test_the_datagrams_round_trip():
    d = bridge.decode(bridge.encode({"rpm": 812.5, "pedal": 0.25, "coolant": 61, "junk": 1}))
    assert d == {"rpm": 812.5, "pedal": 0.25, "coolant": 61}
    assert bridge.decode(b"not json") == {} and bridge.decode(b"[1,2]") == {}
    out = json.loads(bridge.encode({"throttle": 0.5, "torque": 1.0}))
    assert out["throttle"] == 0.5


def test_overrun_cuts_the_fuel_and_the_pedal_brings_it_back(dme):
    # the car turns the engine at 2000 rpm with the pedal up: overrun, no fuel, the plate shut
    dme.update(rpm=2000, pedal=0.0, coolant=60, iat=20, speed=0)
    dme.run(1.5)
    o = dme.outputs()
    assert o["power"] == 1 and o["state"] == 2 and abs(o["rpm"] - 2000) < 30
    assert o["fuel"] == 0 and o["torque"] == 0.0 and o["inj"] < plant.FUEL_CUT_MS
    assert 0 <= o["throttle"] <= 0.3
    closed = o["plate"]
    dme.update(pedal=0.5)
    dme.run(1.0)
    o = dme.outputs()
    assert o["fuel"] == 1 and 1 < o["inj"] < 20
    assert o["plate"] > closed + 10 and o["throttle"] > 0.6
    assert o["lambda"] < 1.3 and o["torque"] > 0.5
    assert o["maf"] > 50 and o["load"] > 150


def test_the_key_off_stops_the_module(dme):
    dme.update(ignition=0)
    assert not dme.powered
    o = dme.outputs()
    assert o["power"] == 0 and o["throttle"] == 0.0 and o["torque"] == 0.0
    dme.update(ignition=1, pedal=0.0, rpm=0)
    assert dme.powered
    dme.run(0.5)
    assert dme.outputs()["state"] == 0
