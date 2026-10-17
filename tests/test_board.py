"""The program on the board: the monitor handshake, the K line, CAN.
One board runs a second of the module's time and is shared."""
import collections

import pytest

from ms43emu import find_image, load_image
from ms43emu.board import Board, IPS
from ms43emu.monitor import RUN, checksum


@pytest.fixture(scope="module")
def board():
    if find_image("stock") is None:
        pytest.skip("no stock image")
    b = Board(load_image("stock"))
    b.cpu.reset()
    b.run(IPS)
    return b


def ds2(payload, address=0x12):
    t = bytes([address, len(payload) + 3]) + bytes(payload)
    x = 0
    for c in t:
        x ^= c
    return t + bytes([x])


def test_runs_without_resets_or_faults(board):
    assert board.resets == 1                     # the power-on reset of the fixture
    assert not board.cpu.faults and not board.cpu.open_reads
    assert board.unsupported <= {"CC13 capture", "CC14 capture", "CC15 capture"}   # set up; nothing captures yet
    assert board.in_program
    assert board.cpu.watchdog_services >= 90     # every 10 ms


def test_monitor_handshake_reaches_running(board):
    m, cpu = board.monitor, board.cpu
    assert m.heard[0][:2] == bytes([0x12, 0x32]) and m.heard[1][0] == 0xCF
    assert all(f[0] == RUN for f in m.heard[2:]) and m.bad == 0
    assert all(checksum(f) == f[8] for f in m.heard)
    assert cpu.rb(0xE027) == RUN                 # the C167's side of it
    assert cpu.rb(0xE7EA) == 0                   # no resets for the monitor
    assert [cpu.rb(0xE015 + i) for i in range(5)] == [0] * 5     # its debouncers
    assert len(m.answers) == 40 and m.running >= 90


def test_can_frames(board):
    sent = collections.Counter(i for i, d in board.can.sent)
    assert set(sent) == {0x316, 0x329, 0x545}
    assert all(n >= 90 for n in sent.values())   # every 10 ms


def test_identification(board):
    answer = board.exchange(ds2([0x00]), seconds=1.0)
    assert answer[:3] == bytes([0x12, 0x2E, 0xA0])
    assert answer[3:10] == b"7551615"            # the part number, as in the program at 0x06FE80
    x = 0
    for c in answer:
        x ^= c
    assert x == 0
