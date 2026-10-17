"""The engine's signals reaching the program, and the program running its
engine schedule on them (docs/status.md "The engine"):

- the crank teeth through CC15 and PEC channel 7, the tooth period measured;
- sync at the gap, held: the operating state 2, T0 and T7 counting the teeth
  in hardware and wrapping every 120;
- the cam edges inside the windows the program checks;
- the six ignition compares (CC0-CC5) once a cycle each, 20 teeth apart --
  a straight six's 120 degrees -- at cranking speed and at 6000 rpm."""
import collections

import pytest

from c166 import sfr
from ms43emu import find_image, load_image
from ms43emu.board import Board, IPS
from ms43emu.engine import PITCHES, TEETH, MISSING, CAM_EDGES, INTAKE, EXHAUST

E5D8_PERIOD = 0xE5D8        # tooth period, T1 counts: the last two crank captures (PEC 7)
F900_STATE = 0xF900         # the operating state (0x0E191E): 2 is running
FC4D_FAULTS = 0xFC4D        # bit 6: intake cam out of its window (0x0A4620)
FBDC_INTAKE = 0xFBDC        # the intake cam edge's angle
T1_HZ = 24_000_000 / 128    # T1 as the program sets it: fCPU/8 << 4


@pytest.fixture(scope="module")
def running():
    """Boot, crank at 200 rpm for 1.5 s (sync on the first gaps), record the
    compares over the next 1.5 s of cranking."""
    if find_image("stock") is None:
        pytest.skip("no stock image")
    b = Board(load_image("stock"))
    b.cpu.reset()
    b.run(IPS)
    b.engine.start(200)
    b.run(int(0.5 * IPS))
    period = b.cpu.rw(E5D8_PERIOD)
    b.run(int(1.0 * IPS))
    hits = collections.Counter()
    b.capcom.on_compare = lambda n, mode: hits.update([n])
    b.run(int(1.5 * IPS))
    return b, period, hits


def test_the_wheel_has_58_teeth_a_turn():
    teeth = sum(1 for p in range(PITCHES) if p % TEETH < TEETH - MISSING)
    assert teeth == 2 * 58


def test_the_cams_are_where_the_program_wants_them():
    # angles of T0 (pitch + 1): intake 31-46 rising, 91-106 falling;
    # exhaust 14-26 / 74-86; both high at one gap, both low at the other
    (ir, if_), (er, ef) = CAM_EDGES[INTAKE], CAM_EDGES[EXHAUST]
    assert 31 <= ir + 1 <= 46 and 91 <= if_ + 1 <= 106
    assert 14 <= er + 1 <= 26 and 74 <= ef + 1 <= 86

    def high(edges, p):
        rise, fall = edges
        return rise <= p < fall

    for gap in (58, 59, 118, 119):
        assert high(CAM_EDGES[INTAKE], gap) == high(CAM_EDGES[EXHAUST], gap)


def test_the_program_measures_the_tooth_period(running):
    b, period, _ = running
    # 200 rpm: a pitch is 5 ms, 937.5 counts of T1
    expected = T1_HZ * 60 / (200 * TEETH)
    assert abs(period - expected) <= 2


def test_sync_is_held_with_the_angle_in_hardware(running):
    b, _, _ = running
    cpu = b.cpu
    assert cpu.rb(F900_STATE) == 2
    assert not cpu.rb(FC4D_FAULTS) & 0x40
    # T0 and T7 count the teeth (running, counter mode) and wrap at 120
    assert cpu.rw(sfr.T01CON) & 0x48 == 0x48 and cpu.rw(sfr.T78CON) & 0x48 == 0x48
    assert cpu.rw(sfr.T0) < 120 and cpu.rw(sfr.T0) == cpu.rw(sfr.T7)
    assert b.resets == 1 and not cpu.faults


def test_the_six_ignition_compares_fire_once_a_cycle_120_degrees_apart(running):
    b, _, hits = running
    cycles = 1.5 * 200 / 120                    # 2.5 cycles recorded
    for n in range(6):
        assert abs(hits[n] - cycles) <= 1, (n, hits[n])
    angles = sorted(b.cpu.rw(getattr(sfr, f"CC{n}")) for n in range(6))
    steps = [b2 - a for a, b2 in zip(angles, angles[1:])]
    assert steps == [20] * 5                    # 120 degrees of crank


def test_sync_holds_at_6000_rpm(running):
    b, _, _ = running
    hits = collections.Counter()
    b.capcom.on_compare = lambda n, mode: hits.update([n])
    b.engine.start(6000)
    b.run(int(0.5 * IPS))
    hits.clear()
    b.run(int(0.5 * IPS))
    assert b.cpu.rb(F900_STATE) == 2
    for n in range(6):
        assert abs(hits[n] - 25) <= 1          # 0.5 s at 6000 rpm: 25 cycles
