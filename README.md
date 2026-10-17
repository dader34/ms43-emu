# ms43-emu

Runs the firmware of the Siemens MS43 DME (C167CS, BMW M54; program
`430069`, the E46 M54B25 US read) straight from a 512 KB full read, so
program patches can be tried before they go near a car. The sister of
`ms45-emu` and `gs20-emu`.

The MS43 is a C167 like the GS20, not a PowerPC like the MS45, so the CPU
and the chip's peripherals are the ones `gs20-emu` wrote, now their own
package in `../c166-emu` and shared by both. What is the MS43's here is the
board: where the flash, the RAM and the chip select latch are, the 24 MHz
clock, and the monitoring processor.

What it can do today:

- load and check a full read, and read its identification: the hardware
  and Siemens numbers, the software triplets of the boot block, the
  program and the calibration, and the program the calibration is for;
- boot it from the reset vector. The boot block checks its RAM and starts
  the program 165014 instructions after reset; the program's init runs,
  and so do its tasks, on the 1 ms timer tick, with the A/D converter
  scanning through PEC and the SPI jobs on their interrupt;
- keep the monitoring processor happy: the C167 exchanges a 9-byte frame
  with it over SPI every 10 ms (hello, acknowledgement, then a question
  and an answer each time) and resets itself when the monitor does not
  play along. `ms43emu/monitor.py` plays it, from the C167's side of the
  protocol;
- answer a tester on the K line (DS2 at 9600 baud, address 0x12): the
  identification comes back with part number 7551615, the fault memory
  and the status block too;
- send its CAN frames: DME1 (0x316), DME2 (0x329) and DME4 (0x545), every
  10 ms, hear the E46's other modules (`ms43emu/e46.py`: the fuel level
  from the cluster) and read the vehicle speed off the ASC's pulse wire;
- run its engine: the crank and cam signals (`engine.py`), the sensors
  (`sensors.py`), the serial EEPROM with a learnt throttle (`eeprom.py`),
  the electronic throttle it drives (`throttle.py`), and a plant
  (`plant.py`) whose air path is the program's own throttle table: on it
  the program starts the engine on the starter, runs it on the pedal,
  holds it at its limiter and cuts the fuel on the overrun;
- drive a car in BeamNG.drive: `tools/beamng.py` serves the DME on UDP in
  real time and the mod in `beamng/ms43dme` hands any car's throttle to it
  (`beamng/README.md`).

It runs at about real time under PyPy, with no resets or faults over the
ten seconds tried.

    from ms43emu import load_image
    from ms43emu.board import Board, IPS
    b = Board(load_image("stock"))
    b.cpu.reset()
    b.run(IPS)                                        # a second of the module's time
    print(b.monitor.running, b.can.sent[-3:])
    print(b.exchange(bytes.fromhex("12040016")))      # identification

    ../gs20-emu/.pypy/bin/pypy3 tools/run.py --seconds 3 --ds2 00 04 0B03
    ../gs20-emu/.pypy/bin/pypy3 tools/kline.py --ws 8767 --trace     # then https://bmweb.danner.ink/?gateway=ws://localhost:8767
    ../gs20-emu/.pypy/bin/pypy3 tools/bringup.py      # the reset path on the bare CPU, step by step

Not yet: the idle (the program keeps the plate at its stop at idle
speeds, see `docs/status.md` "The idle"; a BeamNG car idles itself
underneath), the injection stage's own hard cut, the EWS (not gating
anything here), flashing over the K line, checksums. `docs/status.md`
has the state and the next steps.

## Setup

    python3.11 -m venv .venv            # or newer
    .venv/bin/pip install -e ../c166-emu -e '.[test]'

Images are BMW-derived and are not in the repo; see `images/README.md`.

    MS43_FULL=... .venv/bin/pytest
    PYTHONPATH=. ../gs20-emu/.pypy/bin/pypy3 -m pytest      # five times faster; c166-emu is installed there

c166-emu is needed at its branch `claude/vigilant-rubin-osbmfk` or later
(CAN frames one byte off, new-data flags the program never saw).

PyPy is what keeps the module in real time (the K line, BeamNG); with
`uv` it is one line:

    uv python install pypy@3.11
    uv venv .pypy --python pypy3.11 && uv pip install --python .pypy/bin/python -e ../c166-emu -e '.[test]'
    .pypy/bin/python -m pytest -q                            # 32 tests, under a minute
    .pypy/bin/python tools/beamng.py --print                 # the DME for BeamNG on UDP 4343

## Ghidra

`ghidra/` has the headless scripts for a Ghidra project of the read
(`~/ms43_430069`, Ghidra 12.1.3 with the C166 module, the memory map and
data pages of `machine.py`), and `ghidra/list.sh` / `ghidra/decomp.sh` to
list a range or decompile a function from the command line:

    H=/opt/homebrew/Cellar/ghidra/12.1.3/libexec/support/analyzeHeadless
    $H ~ ms43_430069 -import images/stock_512k.bin -processor C166:LE:16:default -cspec tasking \
       -loader BinaryLoader -loader-baseAddr 0x000000 -scriptPath $PWD/ghidra/scripts \
       -preScript SetupMemoryMS43.java -noanalysis
    ghidra/list.sh 0E1192:60
    ghidra/decomp.sh 0E09D2

## Layout

- `ms43emu/image.py`    finding and validating a full read; its regions and identification fields
- `ms43emu/machine.py`  the CPU on the MS43's memory map; booting, calling routines, probes
- `ms43emu/board.py`    the peripherals around it, from c166-emu, at 24 MHz; the K line exchange
- `ms43emu/monitor.py`  the monitoring processor on the SPI
- `tools/run.py`        run a read for a while and report; DS2 requests at the end
- `tools/kline.py`      the DME on a K line in real time: a pty, and with --ws BMWeb's gateway WebSocket
                        (and tools/webserial-emu.js for other Web Serial pages); from gs20-emu's
- `tools/bringup.py`    from the reset vector on the bare CPU, with what it touched on the way
- `tests/`              run the stock read (skipped without it)
