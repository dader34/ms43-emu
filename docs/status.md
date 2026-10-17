# Where it stands

Status as of 2026-10-16. The program runs its tasks with the monitoring
processor, answers DS2 on the K line and sends its CAN frames; no engine.

## The read

`images/stock_512k.bin`, program `430069` (E46 M54B25, US), sha256
`a6aa131f...51ee55`. The chip has the GS20's bottom-boot sector layout
(16K, 8K, 8K, 32K, then 64K each, an Am29F400BB there); the two 8K
sectors at 0x4000-0x7FFF are blank in this read.

| offsets           | what                                                                |
|-------------------|---------------------------------------------------------------------|
| 0x00000-0x0FFFF   | boot block: vectors (all JMPS), reset code at 0x2D06, IDs 0x3F50-0x3FFF |
| 0x10000-0x6FFFF   | program, run at 0x090000-0x0EFFFF; ID block 0x6FFB2, end mark 0x6FFF0 `90 48 24 42` |
| 0x70000-0x7FFFF   | calibration, read at 0x070000 (DPP0); header, `ca430069.DAT`, end mark 0x7FFF0 `42 24 48 90` |

Identification fields: hardware `6577355402`, Siemens `5WK90027`, program
part `6577355117`; software triplets boot `11143053`, program
`111430536901`, calibration `1114300069010000`, three copies each.
Whether the boot block votes them like the GS20's is not traced. The DS2
identification answers with `7551615`, the part number in the program at
0x06FE80.

The program's own trap vector table is at 0x0EEE00 (offset 0x6EE00); its
first 66 bytes match the boot block's. Its unused vectors are a JMPS to
themselves.

## The memory map

From the reset code, which also sets SYSCON = 0xE286 and BUSCON0-4:

- ADDRSEL2 = 0x0401: external RAM, 8 KB at 0x040000 (DPP2 = 0x10);
- ADDRSEL4 = 0x00E0: 4 KB at 0x00E000, segment 0 (plain memory here);
- ADDRSEL3 = 0x0800: 4 KB at 0x080000, the chip select latch of the SPI
  devices. The SPI jobs write the shadow at 0xFDCA to it with
  `EXTP #0x20, #4; SCXT CP, #0xFDCA; MOV 0x0000, R0`. Mapped as memory, so
  the monitor model can see its select bit (6, active low);
- ADDRSEL1 = 0x1008: 1 MB at 0x100000, the flash again (CS1). The boot
  block's flash driver writes its commands and data there, in word mode
  (unlock at 0x10AAAA and 0x105554), and polls through CS0;
- CS0, the rest: the flash, with address bit 19 ignored.

The boot block's RAM check runs over 16 KB, 0x040000-0x043FFF, from the
top down: it fills 0x5555 (0x3008), writes one word 0x4A4B (0x3014) and
clears to zero (0x3038). The upper 8 KB lands on the flash (8193 writes,
recorded by the core as flash writes). With the RAM window made 256 KB
instead, the boot block does not start the program and stays at
0x001F08, so the check tells the two apart, and the 8 KB that ADDRSEL2
says is what makes it start the program. Why the check runs over 16 KB is
not read.

## The clock

24 MHz: the firmware's baud rate reloads are S0BG = 0x4D (9615 baud, DS2's
9600) and 0x47 (10417, ISO 9141's 10400), and 24 MHz is the only clock in
reach that gives both. The board takes an instruction as two clocks (12 M
a second); the program keeps its schedule from the timers either way.

## How far it gets

1. Reset vector, JMPS to 0x002D06: CP = 0xF600, DPP0-2 = 0x1C, 0x3B,
   0x10, the bus set-up, the RAM check.
2. 0x003040 `PUSH R5; PUSH R4; RETS` starts the program at 0x0E366A,
   instruction 165014.
3. The init waits on the CAPCOM timer T1 (0x0DF5F2: five counts), which
   `c166.periph.CapcomTimers` now drives, and sets up T4 (the 1 ms tick,
   trap 0x24), the A/D converter (auto scan, results moved by PEC, trap
   0x28), the SSC (traps 0x2D/0x2E, PEC channels 2 and 3), CAN, and the
   SPI job scheduler, a software interrupt on XP0IC (trap 0x40).
4. The program's ROM check is the first thing that takes time: a table
   CRC-16 at 0x0E3644 (table at 0x0EE87A).
5. The tasks run: every 10 ms a monitor exchange, the watchdog serviced,
   DME1/DME2/DME4 on CAN.

Over 10 s of the module's time: no resets, no faults, no reads of
unmapped addresses, nothing configured that is not modelled.

## The monitoring processor

The C167 has a monitor beside it, as the DBW cars need. The handler is
0x0E09D2 (decompiled by `ghidra/decomp.sh 0E09D2`, kept out of the repo in `ghidra/out/`). The link:

- SPI, the monitor master. The SPI job (0x0EB1D6) makes the C167 the
  slave (SCLK and MTSR inputs, MRST an output, SSCCON MS clear), selects
  the monitor in the latch, and arms PEC: channel 3 sends 9 bytes from
  0xFC30, channel 2 takes 9 into 0xFC27. A frame is 8 bytes and their XOR
  (0x0E11F4).
- States, by the first byte: 12 hello (the C167 sends `12 32`, wants `12`
  with 0x32 in byte 2), CF acknowledgement, AD running, 3B given up.
- Running: the monitor asks question q in byte 1 (index*4 + function,
  0..0x27, in order); the C167 answers in its next frame (bytes 1-2) from
  the table at 0x0EE4D2 with one of four functions (0x0E128A, 0x0E1412,
  0x0E1502, 0x0E16B6).
- What it checks of the monitor, besides state and checksum: the question
  following the last one (when byte 3 is 0); bits 1-5 of byte 2 all
  inverted every 8 frames; byte 3 different in frames 29 and 31 of each
  31. A failure counts 3 into a debouncer at 0xE015-0xE019 (0x0E121E), a
  good frame takes 1 off (0x0E1250); at 12 the C167 resets.
- Resets: 0x0E1192 stores the reason (and its complement) at 0xE7E9, counts
  at 0xE7EA, and executes SRST, at most 8 times; then it sets 0xE027 = 3B
  and runs without the monitor. Reasons seen in the handler: 1-5 (link
  and protocol), 9, 10, 11 (other flags, not read).

Without the monitor model the C167 resets 8 times in 0.5 s and then runs
on in state 3B. With it: state AD, 40 questions answered, debouncers at 0.
The model does not check the answers, keeps them in `answers`, and sends
zeros for its own measurements (bytes 4-7, copied to 0xFBD1-0xFBD4). The
meaning of byte 3 (sent nonzero once every 31 frames here) is a guess
from the checks.

## CAN

Transmit objects 14 (0x316 DME1), 13 (0x329 DME2) and 12 (0x545 DME4),
sent with one write of 0x6595 each (TXRQ set, CPUUPD reset). Receive
objects for 0x153, 0x1F3, 0x1F5, 0x43D, 0x43F, 0x613 and 0x615; nothing
sends them yet. The CAN model's transmit rule is now the chip's (TXRQ set
while CPUUPD ends up reset), which also covers the GS20's 0xF7FF/0xEFFF
sequence with the same frames as before.

## DS2

`12 04 00 16` (identification), `04` and `04 01` (fault memory: five
entries, 0x64 0x6F 0x92 0x93 0x30, as expected with no sensors) and
`0B 03` (status block) are answered. Nothing else is tried.

## The K line for a tester

`tools/kline.py --ws 8767` (gs20-emu's, ported) serves the DME in real time
on a pseudo-terminal and on BMWeb's gateway WebSocket. At 6 M instructions
a second (the default; the tasks run at the same rates as at 12 M) PyPy
keeps up with room to spare. Checked with a gateway client: open, the
identification answered 64 ms after the request, fault memory, getSignals.

## Programming

WinKFP's PABD 11MDS43.IPO with the SGBD 11MDS43.PRG, the GS20's template
(see winkfp-re/GS20-FLASH.md). `90 42 4D 57 Z` answers BUSY for about the
first nine seconds after power-on, then gives the seed; the key opens it.
After that the boot block's driver runs from internal RAM (0xF9xx-0xFBxx,
copied from 0x001Bxx-0x001Dxx). Erase `07 06 09 00 00 00` answers
`A0 06 09 00 00 00 01` and erases 0x10000-0x6FFFF; a write `07 02 A A A n`
answers `A0 02 <next> n 01`. `07 0F` partway through the writes answers BUSY.

With CS1 unmapped (as it was), the driver's writes went nowhere, its DQ7
poll timed out and it executed SRST (0x001CF0); the erase got no answer
and WinKFP reported IFH-0009 as error 211 (and 2022, "battery voltage").

## CAPCOM compare

c166-emu's CapcomTimers now has the compare channels (2026-10-16, for the
MS42's 1 ms tick). Here CC12 is in a compare mode with its interrupt on
(trap 0x1C, about 155 a second), and CC13-CC15 are set up for capture,
which is not modelled; nothing else changed (monitor, DS2, CAN as before).

## Next

- The engine: a crank wheel (60-2) and cam on the CAPCOM inputs, as
  ms45-emu's crank model, then the injection and ignition outputs.
- Sensors: which A/D channel is which (supply, pedal, throttle, MAF,
  temperatures), and the monitor's measurements.
- CAN partners (ASC 0x153, cluster 0x613/0x615, EGS 0x43D/0x43F),
  ms45-emu's `e46.py`.
- The checksums (the ROM check's CRC and where it keeps its expected
  value; the boot block's checks before it starts the program), then
  flashing over the K line, as gs20-emu has it.

## The engine

`ms43emu/engine.py` drives the crank and cams; `board.engine.start(rpm)`
turns it. Since 2026-10-17 the program syncs on it and holds sync from
cranking to 6000 rpm, with the angle counted in hardware and its six
ignition compares firing once a cycle, 120 degrees apart
(tests/test_engine.py).

### What the board wires to what

| signal | pins | how the program (430069) uses it |
|---|---|---|
| crank, 60-2, falling edge a tooth | CC15 (P2.15) = T7IN, T0IN (P3.0), CAPIN (P3.2) | before sync CC15 captures T1 and PEC channel 7 copies each time into a buffer at 0xE5B4 (`PECC7 = 0x212`; the CC15 handler 0x0DFF92 rewinds it); after sync T0 and T7 count the teeth (counter mode, falling edges); in an unconfirmed gap CAPIN times them (T5 into CAPREL, CRIC handler 0x0DFEDA, at most two teeth) |
| intake cam | CC14 (P2.14), both edges | handler 0x0E3918; the edge's angle at 0xFBDC |
| exhaust cam | CC13 (P2.13), both edges | handler 0x0DFE42; the angle at 0xFBDF |
| time base | T1, fCPU/128 (5.33 us) | overflows counted at 0xE640 |
| virtual teeth | T6, counting down | loaded with 1.5 tooth periods before a gap; its underflow is the missing tooth, and its handler (0x0E4E90) moves T0/T7 on by software; T0/T7 in counter mode with input x00 count T6's wraps |

### The sync

The operating state is at 0xF900 (dispatcher 0x0E191E): 0 seeking, 1
cranking, 2 running, 5 fault. The angle events run from CC24 on T7: the
handler (0x0E3D68) switches to the bank at 0xF7D8 and jumps to the state
in its R4. At the gap the state 0x0E40D0 wants the tooth period to jump
(the gap tooth) and the two cam levels equal -- both high at one gap,
both low at the other, which is how it tells the turns apart -- then sets
T0 = T7 = 61 and starts them counting. At the next gap 0x0E4A00 stops
them, T6 supplies the two missing teeth, and when T7 reaches CC24 = 120
the state 0x0E4AE6 sets T0 = T7 = 0 and starts them again.

What the program checks of the cams (angles of T0, which reads pitch + 1):

- intake (0x0A4620): rising edge in 31-46, falling in 91-106, else fault
  0xFC4D bit 6 and state 5;
- exhaust (0x0A4920): rising in 14-26, falling in 74-86.

The engine puts the intake at pitches 38/98 and the exhaust at 19/79.

### What the C167 model needed

All in c166-emu's periph.py: CAPCOM capture modes driven by pin edges,
with P2 reading the pin levels; the second CAPCOM unit on the board (T7,
T8, CC16-CC31); counter mode for T0/T7 (pin edges or T6's wraps); a
compare match when software writes a timer onto its compare value (the
gap handling depends on it: T6's handler writes T7 to 120 and waits for
CC24); GPT2's CAPIN capture into CAPREL with the CRIC request.

### Speed

At ips 6M (four clocks an instruction) one second of the module's time
takes 0.9-1.1 s under pypy with the engine turning at any speed; at 12M,
about 1.5 s.

### Next

- the EWS release, without which there may be no injection (not checked
  yet: the injection compares are scheduled with durations either way).

## The sensors

`ms43emu/sensors.py`: physical values in, A/D counts out, through the A/D
converter's `source` hook (c166 periph). What each input is, from the
program:

| A/D | what | where the program takes it |
|---|---|---|
| 0 | air mass meter | 0xE51E, kg/h at 0xE32E (x0.25) |
| 6 | pedal sensor 2 (half scale) | 0x092BCC: 0xE108, scaled 0xE102 |
| 10 | pedal sensor 1 | 0x092BCC: 0xE106, scaled 0xE100; pedal 0xE0FE |
| 11 | throttle sensor 2 (falling) | 0x099102: 0xE15C |
| 12 | throttle sensor 1 (rising) | 0x099102: 0xE15A; angle 0xE14C |
| 13 | multiplexer A: slot 1 intake air, slot 3 coolant | 0x0DE354: slots at 0xE53E + 2n |
| 4 | multiplexer B | 0x0DE354: slots at 0xE54E + 2n |
| 2, 3, 7, 8 | the four oxygen sensors | one routine, four instances (0x0B6FD8) |

Every conversion lands at 0xE51E + 2*channel (the A/D handler 0x0E5744,
its sequence from the table at cal 0x076669). The two multiplexers share
an address, bits 0-2 of the external latch 0x080000: 0x0DE354 converts
channel 13 then channel 4, stores both under the current address and
steps it.

Scaling: pedal value = (raw - offset) * gain / 256 with (0xA7, 0x4822) for
sensor 1 and (0x65, 0x9044) for sensor 2 (cal 0x70D72/0x70812,
0x70D73/0x70814); throttle 14 angle counts (0.0073 degrees) per A/D count
from 0x68 on sensor 1, down from 0x39C on sensor 2. The air mass and the
two temperature curves were swept on the emulator (the temperatures at
power-up: once running the program rate-limits them) and are tables in
sensors.py.

The throttle task (0x0D8B00, entry 142 of the task table at 0x0EE286)
runs on the PWM unit's period interrupt -- channel 2, every 2.5 ms --
which needed a PWM model in c166 periph.

## The EEPROM

`ms43emu/eeprom.py`: a 93C86 (1024 x 16, Microwire), bit by bit on the
SSC. The program reaches it in an SPI job (bit 11 of 0xFDE8, run from the
XP0 software interrupt, entry 0x0EB280): 16-bit SSC words, chip select on
latch bit 7 (active high), P3.8 read as the ready line, the command from
0xE630 (0x1800 | address to read), the word back in 0xE62E. Chip select
dropping is seen through a write hook on the latch (c166 cpu
`hook_ext_write`).

What is in it, so far:

- word 0x19, the header: the 8 bytes at 0x0EFFB2 and their checksum
  (0x0BDC2C). Without it the program takes every record as another
  program's (0xFD46 bit 5) and uses defaults;
- word 0x1A4, the learnt throttle (0x09B2F2): sensor 1 and 2 at the closed
  stop, at the limp-home position, 0x5555, the checksum.

The checksum is the routine at 0x0C9CF8: a table-driven CRC from 0x000F,
the table at 0x0EE87A. `eeprom.learnt(image, ...)` builds an EEPROM with
both records.

## The throttle

`ms43emu/throttle.py`. The program's motor command (signed, 0xE0C8) goes
out on PWM channel 2 and P6.5 (0x09273C): drive = PW2 / (PP2 + 1) - P6.5.
The plate is a mass on a spring with the limp-home point at 4.9 degrees,
damping and stops at 0 and 85; plausible numbers, the program's
controller closes the loop.

The controller runs only when 0xFD0C bit 9 is clear (0x0922A0), which
needs:

- no throttle fault (0xFD16 bit 3): a learnt throttle in the EEPROM, else
  0xFD14 bit 0 ("not learnt") and bit 4 hold it off;
- the monitor agreeing on the pedal (0xFD08 bit 1 via bit 0, 0x0D1CDE):
  the monitor's data byte 4 is its own reading of pedal sensor 2, which
  must be within 23 of the C167's (0xE013, channel 6 >> 2).

With both, at key-on the program learns the closed stop from the model
(0xE176 = 0x68) and the plate follows the setpoint (0xE14E): at 800 rpm,
half pedal gives about 44 degrees in 0.3 s.

### Next

- the engine model: air from throttle and rpm into the MAF, torque from
  the program's injection and ignition;
- the bridge to BeamNG;
- the EWS release.
