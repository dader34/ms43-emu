"""The program's live values, read from its RAM the way a datalogger does.

The addresses and scaling are the RomRaider MS43 logger definitions'
(github.com/Rustle333/Siemens-MS43, "MS43 logger_METRIC_EN_v1.0.0.xml")
for ECU id 7551615 -- the part number this program's DS2 identification
answers with. Addresses below 0x10000 are internal RAM, 0x04xxxx the
external RAM.
"""

# name: (address, size in bytes, scale, offset) -> value = raw * scale + offset
VALUES = {
    "rpm": (0xE0E4, 2, 1.0, 0.0),
    "injector_ms": (0x040DAA, 2, 0.0053333333, 0.0),
    "load_mg": (0xE330, 2, 0.0212, 0.0),
    "maf_kgh": (0xE32E, 2, 0.25, 0.0),
    "throttle_deg": (0xE14C, 2, 0.0073, 0.0),
    "pedal_deg": (0xE34A, 2, 0.0018310547, 0.0),
    "coolant_c": (0x040433, 1, 0.75, -48.0),
    "intake_air_c": (0x040432, 1, 0.747, -48.0),
}

# ignition angle per cylinder, degrees of crank before TDC: raw * -0.375 + 72
IGNITION = {1: 0xFBAB, 2: 0xFBAF, 3: 0xFBAD, 4: 0xFBB0, 5: 0xFBAC, 6: 0xFBAE}


def read(board):
    """The values now, as a dict."""
    cpu = board.cpu
    out = {}
    for name, (addr, size, scale, offset) in VALUES.items():
        raw = cpu.rw(addr) if size == 2 else cpu.rb(addr)
        out[name] = raw * scale + offset
    out["ignition_deg"] = {c: cpu.rb(a) * -0.375 + 72 for c, a in IGNITION.items()}
    return out
