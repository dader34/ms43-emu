"""The loop closed on the board (plant.Dyno): the program starts the engine
on the starter, runs it on the pedal, holds it at its limiter with the
throttle, and cuts the fuel when the pedal is released."""
import pytest

from ms43emu import find_image, load_image, live, eeprom, plant
from ms43emu.board import Board
from ms43emu.sensors import Sensors
from ms43emu.throttle import Throttle
from ms43emu.plant import Dyno
from ms43emu.e46 import Peers

IPS = 6_000_000


@pytest.fixture(scope="module")
def started():
    """Key on with a warm engine, the pedal a third down, the starter until it catches."""
    if find_image("stock") is None:
        pytest.skip("no stock image")
    img = load_image("stock")
    b = Board(img, ips=IPS)
    s = Sensors(b)
    t = Throttle(b, s)
    s.coolant_c = s.intake_c = 60.0
    b.eeprom.words = eeprom.learnt(img, 0x68, 0x39C, 0x98, 0x36C)
    b.cpu.reset()
    d = Dyno(b, s, t)
    Peers(b)
    b.run(IPS)
    s.pedal = 0.3
    d.crank(60.0)
    for _ in range(30):
        b.run(IPS // 10)
        if d.rpm > 700:
            break
    d.crank(0.0)
    b.run(IPS // 2)
    return b, s, t, d


def test_the_hard_limit_is_read_from_the_calibration():
    if find_image("stock") is None:
        pytest.skip("no stock image")
    assert 6000 < plant.hard_limit_rpm(load_image("stock")) < 7000


def test_the_program_catches_on_the_starter_and_runs(started):
    b, s, t, d = started
    assert b.cpu.rb(0xF900) == 2 and d.rpm > 1000
    v = live.read(b)
    assert plant.fuelled(v["injector_ms"]) and t.deg > 5


def test_the_soft_limiter_holds_the_engine_with_the_throttle(started):
    b, s, t, d = started
    b.run(3 * IPS)                       # a third of the pedal, no load: up to the limiter
    seen = []
    for _ in range(12):
        b.run(IPS // 4)
        seen.append((d.rpm, t.deg))
    rpms = [r for r, _ in seen]
    assert 5800 < max(rpms) < 7000
    assert min(rpms) > 5000
    assert min(deg for _, deg in seen) < 0.7 * max(deg for _, deg in seen)   # the plate worked


def test_releasing_the_pedal_cuts_the_fuel_and_closes_the_plate(started):
    b, s, t, d = started
    s.pedal = 0.0
    b.run(int(1.5 * IPS))
    v = live.read(b)
    assert not plant.fuelled(v["injector_ms"])
    assert t.deg < 1.0 and d.rpm < 6000
