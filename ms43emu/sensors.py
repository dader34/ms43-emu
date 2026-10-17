"""The engine's analogue sensors, as the MS43's A/D converter reads them.

What each input is was found from the program (430069), see
docs/status.md "The sensors":

    ch 0    hot-film air mass meter (MAF)
    ch 6    pedal sensor 2 (half scale)       } 0x092BCC: raw -> 0xE108 / 0xE106,
    ch 10   pedal sensor 1                    }   scaled into 0xE102 / 0xE100
    ch 11   throttle sensor 2 (falling)       } 0x099102: raw -> 0xE15C / 0xE15A,
    ch 12   throttle sensor 1 (rising)        }   angle into 0xE14C
    ch 13   multiplexer A, its address the external latch 0x080000 bits 0-2
            (0x0DE354): slot 1 intake air, slot 3 coolant
    ch 4    multiplexer B, the same address: slot 0 reference voltage 1,
            slot 1 reference voltage 2 (the sensor supplies, 5 V)
    ch 5    battery voltage (the main relay's), 0xE7DD / 0x04007C
    ch 1    the ignition key's voltage, 0xE7DE
    ch 2, 3, 7, 8   the four oxygen sensors (one routine, four instances)

The voltages were placed by their fault freeze frames (DS2 04 01: fault
100 carries VB, 111 carries V_REF_1/2), 0.102 V a byte of count >> 2 for
the battery, 0.0392 V a byte for the references. Left at the converter's
default of 0x300 they read 19.6 V and 7.5 V and the program stores fault
100 (control unit) at key-on.

The transfer curves are the program's: each was swept on the emulator and
the value the program computed read back from RAM (tools in the
docs). Between the points they are interpolated.
"""

# counts (10 bit) -> what the program makes of them
# MAF: 0xE32E x 0.25 kg/h, swept with the engine at 800 rpm
MAF_CURVE = [(64, 7.5), (96, 9.75), (128, 12.25), (160, 15.25), (192, 18.75), (224, 23.0),
             (256, 27.75), (288, 33.25), (320, 39.5), (352, 46.25), (384, 54.25), (416, 63.25),
             (448, 73.5), (480, 85.75), (512, 99.5), (544, 115.25), (576, 133.25), (608, 153.25),
             (640, 175.75), (672, 200.75), (704, 228.5), (736, 259.5), (768, 294.0), (800, 332.75),
             (832, 377.0), (864, 427.75), (896, 487.0), (928, 555.0), (960, 627.0), (992, 702.75)]
# NTC sensors, read at power-up (once running the program rate-limits them)
COOLANT_CURVE = [(0x060, 122.25), (0x0A0, 99.75), (0x0E0, 87.0), (0x120, 76.5), (0x160, 67.5),
                 (0x1A0, 60.0), (0x1E0, 53.25), (0x220, 46.5), (0x260, 39.75), (0x2A0, 33.0),
                 (0x2E0, 26.25), (0x320, 18.75), (0x360, 9.75), (0x3A0, -1.5), (0x3E0, -23.25)]
INTAKE_CURVE = [(0x060, 109.62), (0x0A0, 89.45), (0x0E0, 76.75), (0x120, 66.29), (0x160, 58.07),
                (0x1A0, 51.35), (0x1E0, 44.63), (0x220, 38.65), (0x260, 31.93), (0x2A0, 25.21),
                (0x2E0, 18.48), (0x320, 11.76), (0x360, 3.54), (0x3A0, -7.66), (0x3E0, -30.82)]

# pedal: the program's offsets and gains (cal 0x70D72/0x70812, 0x70D73/0x70814):
# value = (raw - offset) * gain / 256, 0..0xFFFF; sensor 2 has twice the gain
PEDAL1 = (0xA7, 0x4822)
PEDAL2 = (0x65, 0x9044)
PEDAL_FULL = 0xE600              # the travel's end, short of where the sensors run out
# throttle: 14 counts of the angle (0.0073 degrees) per A/D count
TPS1_ZERO, TPS2_ZERO = 0x68, 0x39C
COUNTS_PER_DEGREE = 1 / (0.0073 * 14)
LATCH = 0x080000                 # the external latch; bits 0-2 address both multiplexers
BATTERY_V_PER_COUNT = 0.102 / 4  # VB and the key voltage: a byte of count >> 2 is 0.102 V
REF_V_PER_COUNT = 0.0392 / 4     # the two 5 V references on multiplexer B


def _inverse(curve, value):
    """The count at which `curve` gives `value` (clamped to its ends)."""
    pts = sorted(curve, key=lambda p: p[1])
    if value <= pts[0][1]:
        return pts[0][0]
    for (c0, v0), (c1, v1) in zip(pts, pts[1:]):
        if value <= v1:
            return c0 + (c1 - c0) * (value - v0) / (v1 - v0)
    return pts[-1][0]


class Sensors:
    """Physical values in, A/D counts out: set the attributes, the board's
    A/D converter asks `count(channel)` at each conversion."""

    def __init__(self, board):
        self.b = board
        self.pedal = 0.0                 # 0 released .. 1 floored
        self.throttle_deg = 3.0          # the throttle plate, degrees open
        self.maf_kgh = 0.0
        self.coolant_c = 20.0
        self.intake_c = 20.0
        self.oxygen_v = 0.45             # all four sensors, unless `lambda_` is set
        self.lambda_ = None              # the mixture, for narrowband sensors that switch on it
        self.battery_v = 13.8            # the main relay (ch 5) and the ignition key (ch 1)
        self.ref_v = 5.0                 # the sensor supplies, both
        self.extra = {}                  # channel -> count, anything else
        board.adc.source = self.count
        board.monitor.measure = self.monitor

    def oxygen_volts(self):
        """A narrowband sensor: 0.9 V rich, 0.1 V lean, steep through
        stoichiometric; `oxygen_v` as given when no mixture is set."""
        if self.lambda_ is None:
            return self.oxygen_v
        x = (1.0 - self.lambda_) / 0.02
        x = max(-40.0, min(40.0, x))
        import math
        return 0.5 + 0.4 * math.tanh(x)

    def monitor(self):
        """The monitor's own readings (monitor.py): pedal sensor 2, 8 bits."""
        return bytes([int(self._pedal(*PEDAL2)) >> 2 & 0xFF, 0, 0, 0])

    def _pedal(self, offset, gain):
        return offset + self.pedal * PEDAL_FULL * 256 / gain

    def count(self, ch):
        if ch == 0:
            return _inverse(MAF_CURVE, self.maf_kgh)
        if ch == 10:
            return self._pedal(*PEDAL1)
        if ch == 6:
            return self._pedal(*PEDAL2)
        k = self.throttle_deg * COUNTS_PER_DEGREE
        if ch == 12:
            return TPS1_ZERO + k
        if ch == 11:
            return TPS2_ZERO - k
        if ch in (2, 3, 7, 8):
            return self.oxygen_volts() * 1023 / 5
        if ch in (5, 1):
            return min(1023, self.battery_v / BATTERY_V_PER_COUNT)
        slot = self.b.cpu.ext_ram[LATCH] & 7
        if ch == 13:
            if slot == 1:
                return _inverse(INTAKE_CURVE, self.intake_c)
            if slot == 3:
                return _inverse(COOLANT_CURVE, self.coolant_c)
        if ch == 4 and slot in (0, 1):
            return min(1023, self.ref_v / REF_V_PER_COUNT)
        return self.extra.get(ch)
