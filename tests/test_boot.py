"""The boot block's reset path, from the reset vector into the program."""
from c166 import sfr

from ms43emu import Machine
from ms43emu.machine import PROGRAM_ENTRY


def test_boot_block_starts_the_program(stock):
    m = Machine(stock)
    assert m.boot(max_insns=300_000)
    assert m.pc == PROGRAM_ENTRY and m.in_program
    assert not m.cpu.faults and not m.cpu.open_reads
    cpu = m.cpu
    assert [cpu.rw(getattr(sfr, f"ADDRSEL{i}")) for i in range(1, 5)] == [0x1008, 0x0401, 0x0800, 0x00E0]
    assert [cpu.rw(sfr.DPP0 + 2 * i) for i in range(4)] == [0x1C, 0x3B, 0x10, 0x03]


def test_program_init_runs_without_faults(stock):
    m = Machine(stock)
    m.boot(max_insns=300_000)
    m.cpu.hardware_traps = True
    m.run(1_000_000)
    assert m.in_program and not m.cpu.faults and m.cpu.resets == 0
