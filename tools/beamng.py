#!/usr/bin/env python
"""The emulated MS43 as a car's engine controller, for BeamNG.drive.

    .pypy/bin/python tools/beamng.py [--image stock] [--port 4343] [--mips 6]

Runs the DME in real time (PyPy keeps it there at 6 M instructions a
second) and serves ms43emu.bridge on UDP: the car sends a JSON datagram
with what the engine is doing, this answers each with what the DME makes
of it. beamng/ms43dme is the BeamNG side (install it as a mod; it finds
this on localhost:4343). The datagrams:

    car -> DME   {"rpm": 812, "pedal": 0.0, "coolant": 20.0, "iat": 20.0, "speed": 0.0,
                  "ignition": 1, "starter": 0}
    DME -> car   {"power": 1, "throttle": 0.21, "torque": 0.9, "fuel": 1, "plate": 3.1,
                  "setpoint": 3.0, "rpm": 810, "inj": 2.7, "ign": 8.1, "lambda": 1.0,
                  "maf": 19.8, "load": 137.0, "coolant": 20.2, "state": 2, "t": 12.345}

`throttle` is the share of full-load air the plate lets through at this
speed (the program's own throttle table), `torque` what the program's fuel
and spark leave of that torque (0 with the injection cut), `rpm` the
speed the DME itself measures. --print shows a line a second.
"""
import argparse
import os
import socket
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ms43emu import load_file                 # noqa: E402
from ms43emu.bridge import Bridge, decode, encode   # noqa: E402

SLICE_SECONDS = 0.0025                        # module time between looks at the socket
SEND_SECONDS = 0.02                           # the DME's state goes out this often


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--image", default="stock", help="stock, patched, or a 512 KB file")
    ap.add_argument("--port", type=int, default=4343)
    ap.add_argument("--bind", default="0.0.0.0", help="address to listen on (default all; BeamNG on another machine needs that)")
    ap.add_argument("--mips", type=int, default=6, choices=[6, 12])
    ap.add_argument("--print", action="store_true", help="print the DME's state once a second")
    args = ap.parse_args()

    bridge = Bridge(load_file(args.image), ips=args.mips * 1_000_000)
    board = bridge.board
    cpu = board.cpu
    IPS = board.ips
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((args.bind, args.port))
    sock.setblocking(False)
    print(f"MS43 on {bridge.image.name}, program {bridge.image.program_ref()} ({sys.implementation.name}); "
          f"UDP {args.bind}:{args.port}, waiting for the car", flush=True)

    client = None
    epoch = (time.time(), cpu.instructions)
    window = epoch
    slow = False
    last_send = last_print = 0.0
    heard = 0
    try:
        while True:
            now = time.time()
            ahead = (cpu.instructions - epoch[1]) / IPS - (now - epoch[0])
            if ahead < -0.2 or not bridge.powered:
                epoch = (now, cpu.instructions)
            if now - window[0] >= 3:
                rate = (cpu.instructions - window[1]) / IPS / (now - window[0])
                window = (now, cpu.instructions)
                if bridge.powered and (rate < 0.9) != slow:
                    slow = rate < 0.9
                    print(f"the module is running at {rate:.0%} of real time" if slow else "the module is keeping real time again", flush=True)
            if ahead > 0:
                time.sleep(min(ahead, 0.05))
            # everything the car has sent since the last look: the last word counts
            while True:
                try:
                    datagram, addr = sock.recvfrom(4096)
                except (BlockingIOError, InterruptedError):
                    break
                except OSError:
                    break
                inputs = decode(datagram)
                if inputs:
                    if client != addr:
                        print(f"car at {addr[0]}:{addr[1]}", flush=True)
                        client = addr
                    heard += 1
                    bridge.update(**inputs)
            bridge.run(SLICE_SECONDS)
            t = board.seconds
            if client is not None and t - last_send >= SEND_SECONDS:
                last_send = t
                try:
                    sock.sendto(encode(bridge.outputs()), client)
                except OSError:
                    pass
            if args.print and now - last_print >= 1.0:
                last_print = now
                o = bridge.outputs()
                print(f"t={o['t']:7.2f} car rpm {bridge.rpm:5.0f} pedal {bridge.pedal:.2f} | dme rpm {o['rpm']:5} state {o['state']} "
                      f"plate {o['plate']:5.1f} thr {o['throttle']:.2f} tq {o['torque']:.2f} fuel {o['fuel']} inj {o['inj']:5.2f} "
                      f"ign {o['ign']:5.1f} maf {o['maf']:6.1f} | {heard} datagrams", flush=True)
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()


if __name__ == "__main__":
    main()
