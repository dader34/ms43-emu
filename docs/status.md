# Where it stands

Status as of 2026-10-17. The program runs its tasks with the monitoring
processor, answers DS2 on the K line, sends and hears its CAN frames,
reads its sensors, drives its throttle, and runs an engine model or a
BeamNG car (bridge.py, beamng/).

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

## The plant

`ms43emu/plant.py`: the engine the program controls. The air path is the
program's own: its calibration holds the air it expects per cylinder
filling for a throttle angle and a speed (the 16 x 16 "MAF diagnosis"
table at cal 0x7AAEE, axes 0x75206 (degrees, 16 words x 0.00183) and
0x74633 (rpm, 16 bytes x 32); XDF `IP_MAF_1_DIAG`), and `Air.from_image`
reads it, so the plant and the program agree about what the plate does.
Its closed plate leaks 80 mg a filling at 416 rpm and 33 at 896 (5-6
kg/h); 2.5 degrees is already 200 mg at 896 rpm. Without an image `Air()`
is an orifice-and-pump balance fitted to that table (a plate sitting 15
degrees into the bore at its stop).

Torque: fuel from the pulse width the program keeps at 0x040DAA (0.47 ms
dead time, 4.05 mg/ms: fitted to the program's own 137 mg at 2.77 ms
idling and 458 mg at 9.2 ms at full load), lambda against the air, a
burn fraction, an ignition efficiency from the distance to MBT, 40 %
thermal efficiency, less friction. `Dyno` integrates the speed from it
against a load and turns the crank model (`engine.set_rpm`, which keeps
the tooth pattern; `start` sends an edge at once and must not be called
every step). The numbers are plausible, not measured: 232 Nm at 3000 rpm
full load.

With the pedal a third down the program starts the engine on the
starter, runs it, holds it at its soft limiter with the throttle (the
plate between 10 and 20 degrees around 6300-6500 rpm) and cuts the fuel
on the overrun (tests/test_dyno.py).

### The fuel cut and the limiter

The program's overrun cut shows in 0x040DAA as 0.6-0.7 ms, the dead
time: `plant.fuelled` takes anything under 0.8 ms as no fuel. Its hard
limiter (ID_N_MAX_MAX_MT, cal 0x75D41, six bytes x 32 rpm by gear: 6592
in the low gears) does not show there: past it the pulse width stays and
the CC6-CC11 injection compares keep coming (seven a cycle a cylinder at
any speed, so the compares are not the injection either; the injection
stage's own cut is somewhere the emulator does not read). The bridge
applies that limit itself (`plant.hard_limit_rpm`). The soft limiter
(6496) works through the throttle: at a third of the pedal it holds; at
full pedal the setpoint only comes down to 74 degrees and the engine
over-revs, so the hard cut matters.

## The idle

The program does not open the throttle at idle speeds in the emulation,
and the reason was followed all the way; what is left is a question
about the car, not the emulator.

- The throttle setpoint (0xE14E) is written at 0x099880 from 0x0406A4,
  which 0x09B138 computes as the "driver wish" map IP_TPS_SP_PVS (cal
  0x7E4DC, 12 x 12, columns rpm/32 from the byte at 0x04007F, rows the
  pedal 0xE34A): pedal angle to throttle angle. Its rows below 17.5
  degrees of pedal give 0 degrees of throttle from 512 to 2500 rpm
  (2 degrees in the 416 rpm column); 17.5 gives 1.7, 20 gives 5.25.
- The pedal fed to it is max(the driver's pedal 0x040FF6, the idle
  governor's pedal equivalent 0x040FF8), at 0x0BD272.
- The governor (0x0B1C86; running only with 0xFD2C bit 9, the "engine
  running" state -- bit 8 is the start phase, where it resets itself)
  has a feed-forward from the coolant (0x040884, 22 % at 20 C, 17 % at
  90 C), a P term and an I term (0x040856, up to 96 %), a sum at
  0x040888 (clamped at 134 %), its target at 0x040878 (978 rpm at 20 C
  with catalyst heating, 700 warm) and n_dif at 0x0408DE. The sum goes
  through the correction at 0x0B0FD2 (x 0x0408B8, ramped) into 0x0408A6
  and the table IP_PVS_ISA_isapwm (cal 0x7BA30: 0..175 % to 0..20
  degrees of pedal) times IP_FAC_PVS_ISA (1.0 at no pedal) at 0x0B10xx,
  and comes out at 15 degrees at most.
- 15 degrees of pedal is 0 degrees of throttle on that map. So the
  program holds the plate at its closed stop whenever the engine turns
  faster than the start phase, with the stop's leak (33-80 mg) for air,
  and the plant stalls. The blocks that add idle air directly (0xE194
  through 0x09B134) are the automatic gearbox's (0xFD2E bit 6, set only
  with 0xFD9C bit 0, the automatic).

Nothing modelled was found wanting: the CAN peers are heard (vehicle
speed 0), the pedal reads 0, the coolant reads right, the states and
flags change as they should. Either the car's stop lets through far
more air than the program's own table says for 0 degrees, or an input
not modelled raises the governor's pedal equivalent past 17.5 degrees.
For BeamNG it does not matter: the car's engine idles itself below its
idleRPM and the DME's plate adds on top. For the dyno, the pedal holds
the engine.

## The E46 bus and the speed wire

`ms43emu/e46.py`: `Peers` sends the ASC's 0x153 (with the vehicle speed
in bytes 1-2, 1/8 km/h), 0x1F3, 0x1F5 and the cluster's 0x613 (the fuel
level in byte 2, litres) and 0x615 at their periods, zeros otherwise;
the gearbox's 0x43D/0x43F only when told the car is an automatic. The
program polls its objects for NEWDAT (0x0D42EC copies a frame's
configuration byte and data into 0xE1A2.., every object in turn) with
the CAN interrupt disabled; the tank level goes from 0xE1A5 to 0x040102.

That took three corrections to c166-emu's CAN model (its branch of the
same name): data at +7 after the configuration byte, not +8 (every frame
either way was a byte off; DME1 now carries its rpm in bytes 2-3 as
published); NEWDAT/INTPND set as the chip shows a set pair ('10', the
program tests for exactly that); the interrupt only when the firmware
enabled it (the MS43 does not, and each frame was running its XP0
handler, the SPI job scheduler).

The vehicle speed the program uses is not the CAN one but a pulse train
on CC30 (P7.6, captured on the falling edge; the handler 0x0EB9F0 counts
the pulses into 0xE5EA and keeps the periods at 0xE5EC..; 0x0DD12C makes
the speed of them, wanting at least three pulses in two seconds), as the
ASC/DSC puts it on a wire: 6.97 pulses a second per km/h, C_VS_FAC = 6274
a kilometre times four, checked at 50, 100 and 200 Hz and at 60 and 120
km/h. `Peers.set_speed` drives both; `set_fuel` the level.

## The faults a tester sees

`tools/kline.py` now puts the sensors, the throttle and a learnt EEPROM
around the module (`--bare` for the board alone). Bare, a tester reads
five faults at key-on: 100 (control unit), 111 (pedal sensor 2 shorted to
battery), 146/147 (the throttle potentiometers) and 48 (the monitor's
pedal reading against the C167's): every A/D input at the converter's
default of 0x300, so 3.74 V on the pedal and throttle, 19.6 V battery,
7.5 V references, and the monitor reporting 0 V.

The battery voltage is A/D channel 5 (0xE7DD, 0x04007C; the ignition
key's is channel 1, 0xE7DE), 0.102 V a byte of count >> 2; the two 5 V
references are multiplexer B's slots 0 and 1 (0x04008A/8B, 0.0392 V a
byte). All placed through the faults' own freeze frames (fault 100
carries VB, 111 V_REF_1/2) with one channel at a time set apart.
`Sensors` holds them at 13.8 V and 5.0 V.

With everything attached (the K line tool now also puts the other
modules on the bus): 100 remains, from bit 11 of 0xFD5E, set at 0x0C7C5E
when 0xFDC6 bit 0 is still clear a second after the key (C_T_DLY_IGK_FTL,
cal 0x70C8F); bit 10, the tank level under C_FTL_DMTL_MIN, clears once
the cluster frame has been read, which is why the stored freeze frame
still shows both (0x0C) while the live word shows 0x0800. The two bits
are the tank-leak diagnosis module's (DMTL) preconditions: the check
runs under cal 0x70B1C ("Configuration DiagnosticModuleTankLeakage", 1
here). 0xFDC6.0 is bit 6 of a fault's status word, copied at 0x0D61AE
right after the fault debounce 0x0AB27C on the record at 0x04195A (its
status byte reads 0x18 and never gains bit 6 here). What sets that bit
on the car -- presumably the leak test having run and passed -- the
emulator has no module for. Switching the configuration byte off in the
CPU's flash copy changes the fault to 0x06 and brings fault 48 back, so
that is not an answer either. Left as the one code a tester sees at
key-on; not harmful.

Then 128 while idling at a speed the program cannot hold (see "The
idle"), and 15 and 105 on a drive, with the oxygen sensors now following
the mixture (`Sensors.lambda_`, set by the plant and the bridge): names
to be read off a tester.

## The EWS

Not gating anything here. Over 38 s of running with the throttle,
injection and ignition active, no EWS release ever sent and none
modelled, the fault memory fills slowly (0x64 at key-on; 0x92, 0x93,
0x8B, 0x80, 0x3F, 0x0F later; 0x30 is the lambda heater, as the XDF's
C_DTC constants say) and nothing stops. The XDF knows only the EWS fault
increments (C_ABC_INC_IMOB at cal 0x70A41, C_ABC_INC_TOUT_IMOB at
0x70A6E), not a switch. A guess at why: the EEPROM here holds only the
header and the learnt throttle, so the program has no ISN to insist on
and runs as a new, unmarried module would. Not verified; the public
EWS-delete patches (ms4x.net) were not needed.

## The BeamNG bridge

`ms43emu/bridge.py` and `tools/beamng.py`: the board with sensors,
throttle, CAN peers and the program's air table, fed the car's rpm,
pedal, temperatures, speed, key and starter, answering with the plate as
a share of full-load air at that speed, a torque factor (burn fraction
times ignition efficiency, 0 with the fuel cut or past the hard
limiter), and its own readings. `encode`/`decode` are the JSON
datagrams; the server keeps real time the way tools/kline.py does. The
car side is `beamng/ms43dme` (README there): a vehicle Lua extension
BeamNG loads for every car, which points the engine at
`electrics.ms43Throttle` and fills it with throttle x torque.

A key off and on (`Bridge.power`, `Board.power_cycle`) now resets the
monitoring processor and the EEPROM's command state with the CPU: before,
the monitor stayed mid-protocol, the program reset itself eight times and
ran on without it, and the throttle controller stayed off.

## Next

- The idle: why the program's own driver-wish map gives no throttle for
  the governor's pedal equivalent (the car's stop leak? an input?).
- The injection stage's hard cut, so the bridge need not apply the
  limiter itself; and the injector and coil outputs, which are not on
  the port pins the program writes at engine rates.
- The gear: the program keeps 0 at 0x0401AC (0x0D8D5E) with speed on the
  bus; the limiter tables are by gear.
- Under PyPy the bridge keeps real time at 6 M instructions a second with
  the plant; CPython does not.
- Faults 100 (the tank-leak module's status bit), 128, 15 and 105: what
  the program wants for them.
