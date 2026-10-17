"""Where an MS43 read gets from the reset vector: the segment changes on
the way, where the CPU is at the end, the bus and data page set-up, what
was read where nothing is mapped, flash writes and interrupts.

    ../gs20-emu/.pypy/bin/pypy3 tools/bringup.py [image] [instructions]
"""
import collections
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from c166 import sfr                                 # noqa: E402
from c166.cpu import CpuError                        # noqa: E402
from ms43emu import Machine, load_file               # noqa: E402

TRAIL_INSNS = 400_000                                # stepped one by one to log segment changes


def main():
    image = load_file(sys.argv[1] if len(sys.argv) > 1 else "stock")
    total = int(sys.argv[2]) if len(sys.argv) > 2 else 4_000_000
    m = Machine(image)
    cpu = m.cpu
    cpu.reset()
    print(f"{image.name}: program {image.program_ref()}, reset -> 0x{image.reset_target():06X}")
    seg, changes = 0, []
    try:
        while cpu.instructions < min(total, TRAIL_INSNS) and len(changes) < 16:
            pc = cpu.pc
            if pc >> 16 != seg:
                changes.append((cpu.instructions, pc))
                seg = pc >> 16
            cpu.run(1)
        samples = collections.Counter()
        while cpu.instructions < total:
            if cpu.run(min(100_000, total - cpu.instructions)) == 0:
                break
            samples[cpu.pc] += 1
    except CpuError as e:
        print("stopped:", e)
        samples = collections.Counter()
    print("segment changes (instruction, pc):", ", ".join(f"{n}:0x{pc:06X}" for n, pc in changes))
    print(f"after {cpu.instructions} instructions: pc=0x{cpu.pc:06X}, in program {m.in_program}, "
          f"idle {cpu.idle}, resets {cpu.resets}, faults {len(cpu.faults)}")
    if samples:
        print("pc every 100k:", ", ".join(f"0x{pc:06X} x{n}" for pc, n in samples.most_common(6)))
    print("bus:", " ".join(f"{n}={cpu.rw(getattr(sfr, n)):04X}" for n in
                           ("SYSCON", "BUSCON0", "BUSCON1", "BUSCON2", "ADDRSEL1", "ADDRSEL2", "ADDRSEL3", "ADDRSEL4")))
    print("data pages:", " ".join(f"DPP{i}={cpu.rw(sfr.DPP0 + 2 * i):03X}" for i in range(4)),
          f"SP={cpu.sp:04X} CP={cpu.cp:04X}")
    print("open reads:", ", ".join(f"0x{a:06X} x{n}" for a, n in sorted(cpu.open_reads.items())) or "none")
    print("flash writes:", len(cpu.flash_writes), cpu.flash_writes[:4])
    print("interrupts taken:", dict(cpu.interrupts_taken) or "none")


if __name__ == "__main__":
    main()
