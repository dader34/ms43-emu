"""Run an MS43 read on the board for a while and say how it is doing:
resets and faults, the monitor link, the interrupts, the CAN frames, and
the answers to DS2 requests sent at the end.

    ../gs20-emu/.pypy/bin/pypy3 tools/run.py [--image stock] [--seconds 2] [--ds2 00 04 0B03]

Each --ds2 argument is a request payload in hex for address 0x12; the
length and the checksum are added.
"""
import argparse
import collections
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ms43emu import load_file                  # noqa: E402
from ms43emu.board import Board, IPS           # noqa: E402
from ms43emu.monitor import RUN                # noqa: E402

MONITOR_STATE, MONITOR_RESETS, MONITOR_REASON, DEBOUNCERS = 0xE027, 0xE7EA, 0xE7E9, 0xE015


def ds2(payload, address=0x12):
    t = bytes([address, len(payload) + 3]) + bytes(payload)
    x = 0
    for c in t:
        x ^= c
    return t + bytes([x])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", default="stock")
    ap.add_argument("--seconds", type=float, default=2.0)
    ap.add_argument("--ips", type=int, default=IPS)
    ap.add_argument("--ds2", nargs="*", default=["00"])
    args = ap.parse_args()

    b = Board(load_file(args.image), ips=args.ips)
    cpu = b.cpu
    frames, last = collections.Counter(), {}

    def heard(ident, data):
        frames[ident] += 1
        last[ident] = data

    b.can.on_tx = heard
    cpu.reset()
    started = time.time()
    b.run(int(args.seconds * b.ips))
    wall = time.time() - started
    print(f"{b.image.name}: program {b.image.program_ref()}, {b.seconds:.2f} s of module time in {wall:.1f} s")
    print(f"  resets {b.resets - 1}, faults {len(cpu.faults)} {cpu.faults[:2]}, open reads {len(cpu.open_reads)}, "
          f"not modelled {sorted(b.unsupported) or 'nothing'}")
    state = cpu.rb(MONITOR_STATE)
    print(f"  monitor: state 0x{state:02X}{' (running)' if state == RUN else ''}, {b.monitor.running} rounds, "
          f"{len(b.monitor.answers)} questions answered, resets for it {cpu.rb(MONITOR_RESETS)} "
          f"(last reason {cpu.rb(MONITOR_REASON)}), debouncers {[cpu.rb(DEBOUNCERS + i) for i in range(5)]}")
    print("  interrupts:", ", ".join(f"0x{t:02X} x{n}" for t, n in sorted(cpu.interrupts_taken.items())),
          f"| PEC {cpu.pec_transfers} | watchdog services {cpu.watchdog_services}")
    print("  CAN out:", ", ".join(f"{i:03X} x{n} [{last[i].hex(' ')}]" for i, n in sorted(frames.items())) or "nothing")
    for payload in args.ds2:
        answer = b.exchange(ds2(bytes.fromhex(payload)), seconds=1.0)
        print(f"  DS2 {payload}: {answer.hex(' ')}")
        printable = bytes(c if 32 <= c < 127 else 0x2E for c in answer[3:-1]).decode()
        if printable.strip("."):
            print(f"         {printable}")


if __name__ == "__main__":
    main()
