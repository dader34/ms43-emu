"""An MS43 as its program sees it: the C167CS with the flash of a full read
in it, and what it takes to run the program's own routines.

The memory map is the one the boot block's reset code sets up (0x002D06):

  ADDRSEL1 = 0x1008   1 MB at 0x100000   the flash again (CS1), address masked to 512 KB: the
                                         boot block's flash driver writes its commands and data
                                         here (0x10AAAA, 0x105554) and polls through CS0
  ADDRSEL2 = 0x0401   8 KB at 0x040000   external RAM (DPP2 = 0x10 points at it)
  ADDRSEL3 = 0x0800   4 KB at 0x080000   the chip select latch of the SPI devices (written
                                         as `EXTP #0x20; MOV 0x0000, R0`), memory here
  ADDRSEL4 = 0x00E0   4 KB at 0x00E000   in segment 0
  CS0                 the rest           the 512 KB flash, address bit 19 ignored

The boot block's RAM check covers 0x040000-0x043FFF, twice the 8 KB;
with the window larger than ADDRSEL2 says, the boot block does not start
the program, so the 8 KB stays (docs/status.md).

Data pages as the reset path leaves them: DPP0 = 0x1C (the calibration at
0x070000), DPP1 = 0x3B, DPP2 = 0x10 (the external RAM), DPP3 = 3. Which
registers carry arguments and the result is not checked for this program
yet; `call` assumes the Tasking convention gs20-emu found (R12-R15 in, R4
out, R0 the user stack).
"""
from collections import Counter

from c166.cpu import Cpu, CpuError

from .image import Image, FULL_SIZE

RESET_TARGET = 0x002D06          # the reset vector's JMPS
HANDOVER = 0x003040              # PUSH R5; PUSH R4; RETS: the boot block starts the program
PROGRAM_ENTRY = 0x0E366A         # where that RETS lands, 165014 instructions after reset
EXT_RAM = (0x040000, 0x2000)
LATCH = (0x080000, 0x1000)

RETURN_SEG, RETURN_IP = 0xFF, 0xFFFE           # where a called routine returns to: nothing is there
MAX_INSNS = 50_000_000


def make_cpu():
    cpu = Cpu(flash_size=FULL_SIZE, default_map=False)
    cpu.map_flash(0x010000, 0x100000)
    cpu.map_flash(0x100000, 0x200000)          # CS1: the flash driver's write window
    cpu.map_ram(*EXT_RAM)
    cpu.map_ram(*LATCH)
    return cpu


class Machine:
    def __init__(self, image: Image):
        self.image = image
        self.cpu = make_cpu()
        self.cpu.flash[:] = image.data
        self.probe_counts = Counter()

    # ---- running ---------------------------------------------------------------
    def reset(self):
        self.cpu.reset()

    def boot(self, until=PROGRAM_ENTRY, max_insns=MAX_INSNS):
        """Run the reset path from the reset vector. Returns True when `until` was reached."""
        self.cpu.reset()
        self.cpu.run(max_insns, until=until)
        return self.cpu.pc == until

    def run(self, max_insns, until=None):
        return self.cpu.run(max_insns, until=until)

    def call(self, addr, *args, stack=(), near=False, max_insns=MAX_INSNS):
        """Call a routine with R12.. = args and run until it returns. Returns R4.

        stack: further arguments, as words on the user stack (R0), the first one lowest.
        near: the routine ends in RET, not RETS."""
        cpu = self.cpu
        for i, a in enumerate(args):
            cpu.set_r(12 + i, a)
        r0 = cpu.r(0)
        for i, word in enumerate(stack):
            cpu.ww(cpu.da((r0 - 2 * len(stack) + 2 * i) & 0xFFFF), word)
        cpu.set_r(0, r0 - 2 * len(stack))
        sp = cpu.sp
        seg = addr >> 16
        if near:
            cpu.push(RETURN_IP)
            stop = (seg << 16) | RETURN_IP
        else:
            cpu.push(RETURN_SEG)
            cpu.push(RETURN_IP)
            stop = (RETURN_SEG << 16) | RETURN_IP
        cpu.csp, cpu.ip = seg, addr & 0xFFFF
        cpu.run(max_insns, until=stop)
        if cpu.pc != stop or cpu.sp != sp:
            raise CpuError(f"routine 0x{addr:06X} did not return within {max_insns} instructions "
                           f"(pc=0x{cpu.pc:06X}, sp=0x{cpu.sp:04X}, was 0x{sp:04X})")
        cpu.set_r(0, r0)
        return cpu.r(4)

    # ---- probes ------------------------------------------------------------------
    def probe(self, addr, name=None, on_hit=None):
        """Count executions of the instruction at addr; on_hit(machine) sees the registers as they are there."""
        name = name or f"0x{addr:06X}"
        self.probe_counts[name] = 0

        def hit(cpu):
            self.probe_counts[name] += 1
            if on_hit is not None:
                on_hit(self)

        self.cpu.pc_hooks[addr] = hit

    # ---- memory --------------------------------------------------------------------
    def read8(self, addr):
        return self.cpu.rb(addr)

    def read16(self, addr):
        return self.cpu.rw(addr)

    def write8(self, addr, v):
        self.cpu.wb(addr, v)

    def write16(self, addr, v):
        self.cpu.ww(addr, v)

    def read(self, addr, size):
        return self.cpu.read(addr, size)

    def data_addr(self, a16):
        """Where a 16-bit data address points with the data page pointers as they are."""
        return self.cpu.da(a16)

    @property
    def pc(self):
        return self.cpu.pc

    @property
    def in_program(self):
        """Whether the CPU is executing the program (0x090000-0x0EFFFF) rather than the boot block."""
        return 0x090000 <= self.cpu.pc < 0x0F0000
