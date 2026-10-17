"""Programming over the K line, as WinKFP's PABD 11MDS43.IPO does it with
the SGBD 11MDS43.PRG: seed and key (0x90), FLASH_SCHREIBEN_ENDE as a
check (07 0F), FLASH_LOESCHEN (07 06), FLASH_SCHREIBEN (07 02). After the
key the boot block's flash driver runs from internal RAM and writes the
chip through CS1 (0x100000) in word mode; without CS1 mapped its polling
times out and it resets (SRST at 0x001CF0), and the erase gets no answer."""
import pytest

from ms43emu import find_image, load_image
from ms43emu.board import Board

IPS = 6_000_000
PROGRAM = 0x090000               # the 0PA's first address, flash offset 0x10000


def ds2(payload, address=0x12):
    t = bytes([address, len(payload) + 3]) + bytes(payload)
    x = 0
    for c in t:
        x ^= c
    return t + bytes([x])


@pytest.fixture(scope="module")
def board():
    if find_image("stock") is None:
        pytest.skip("no stock image")
    b = Board(load_image("stock"), ips=IPS)
    b.cpu.reset()
    b.run(IPS)
    return b


def ask(b, payload, seconds=1.0):
    t = ds2(payload)
    a = b.exchange(t, seconds=seconds)
    return a[len(t):] if a.startswith(t) else a


def unlock(b):
    """The SGBD's SEED_KEY with Z = 5. The module answers BUSY for its first nine seconds or so."""
    for _ in range(200):
        r = ask(b, b"\x90BMW\x05")
        if r[2] != 0xA1:
            break
    assert r[2] == 0xA0 and r[1] > 5
    idx, key = 5, []
    for i in range(4):
        key.append((r[0x12 + i] + r[0x29 + i] + r[idx]) & 0xFF)
        idx = (idx + 1) % 0x2E
    assert ask(b, bytes([0x90] + key))[2:4] == b"\xA0\x00"


def test_erase_and_write(board):
    b = board
    unlock(b)
    address = PROGRAM.to_bytes(3, "big")
    assert ask(b, b"\x07\x0F" + address + b"\x00")[2] == 0xA0
    resets = b.resets
    erase = ask(b, b"\x07\x06" + address + b"\x00", seconds=5.0)
    assert erase[2:9] == b"\xA0\x06" + address + b"\x00\x01"
    assert b.resets == resets
    assert sorted(b.flash.erased) == list(range(0x10000, 0x70000, 0x10000))
    data = bytes(range(0x60))
    write = ask(b, b"\x07\x02" + address + bytes([len(data)]) + data, seconds=2.0)
    assert write[2] == 0xA0 and write[7] == len(data) and write[8] == 1
    assert bytes(b.cpu.flash[0x10000:0x10060]) == data
