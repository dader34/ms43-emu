"""Loading a 512 KB full read of an MS43.

A full read is the DME's whole flash chip, offsets 0x00000-0x7FFFF: the
boot block, the program and the calibration. The chip (bottom boot
sectors like the GS20's Am29F400BB: 16K, 8K, 8K, 32K, then 64K each)
ignores address bit 19, and the firmware uses both halves: the boot block
runs at its offsets in segment 0, the program at 0x090000-0x0EFFFF and
the calibration as data at 0x070000 (DPP0 = 0x1C). `read` takes a CPU
address and masks it.

Images are BMW-derived and never committed. They are found through
environment variables, or in ./images/ under the names below.
"""
import os
from dataclasses import dataclass

FULL_SIZE = 0x80000
FLASH_MASK = FULL_SIZE - 1

# (start, size), as offsets in the read
BOOT = (0x00000, 0x10000)
PROGRAM = (0x10000, 0x60000)
CALIBRATION = (0x70000, 0x10000)
REGIONS = {"boot": BOOT, "program": PROGRAM, "calibration": CALIBRATION}

JMPS = 0xFA                         # every vector in the table at the start of the boot block is a JMPS
VECTORS_CHECKED = (0x00, 0x04, 0x08, 0x0C)

# The identification fields, as they are in the stock 430069 read. Each
# region carries the same software reference three times (a triplet, like
# the GS20's, presumably voted by the boot block: not traced yet).
BOOT_HARDWARE = (0x03F50, 10)       # "6577355402"
BOOT_SIEMENS = (0x03F80, 8)         # "5WK90027"
BOOT_TRIPLET = (0x03FE8, 8)         # "11143053" x3
PROGRAM_REF = (0x6FFBA, 6)          # "430069"
PROGRAM_PART = (0x6FFC0, 10)        # "6577355117"
PROGRAM_TRIPLET = (0x6FFCC, 12)     # "111430536901" x3
PROGRAM_END_MARK = (0x6FFF0, b"\x90\x48\x24\x42")
CAL_REF = (0x70008, 6)              # "430069": the program the calibration is for
CAL_TRIPLET = (0x70010, 16)         # "1114300069010000" x3
CAL_NAME = (0x70040, 12)            # "ca430069.DAT"
CAL_END_MARK = (0x7FFF0, b"\x42\x24\x48\x90")

DEFAULT_NAMES = {"stock": "stock_512k.bin", "patched": "patched_512k.bin"}
ENV_NAMES = {"stock": "MS43_FULL", "patched": "MS43_PATCHED"}

IMAGES_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "images")


def _text(data, field):
    at, size = field
    return data[at:at + size].decode("latin-1")


@dataclass
class Image:
    data: bytes
    name: str = ""
    check: bool = True              # off for a DME that is half written or deliberately broken

    def __post_init__(self):
        if len(self.data) != FULL_SIZE:
            raise ValueError(f"{self.name}: a full read is 0x{FULL_SIZE:X} bytes, this is 0x{len(self.data):X}")
        if not self.check:
            return
        for at in VECTORS_CHECKED:
            if self.data[at] != JMPS:
                raise ValueError(f"{self.name}: no JMPS at boot block offset 0x{at:02X} (not an MS43 read?)")

    def region(self, name):
        start, size = REGIONS[name]
        return self.data[start:start + size]

    def read(self, addr, size):
        """Bytes at a CPU address the flash is decoded at (0x000000-0x0FFFFF, the chip masks bit 19)."""
        if not 0 <= addr < 0x100000:
            raise ValueError(f"0x{addr:06X} is outside the flash windows")
        off = addr & FLASH_MASK
        if off + size > FULL_SIZE:
            raise ValueError(f"0x{addr:06X}+{size} runs off the end of the chip")
        return self.data[off:off + size]

    # ---- identification -------------------------------------------------------
    def triplet(self, field):
        """The three copies of a region's software reference, as read."""
        at, size = field
        return tuple(_text(self.data, (at + i * size, size)) for i in range(3))

    def program_ref(self):
        """"430069": the program's reference, from the ID block at the end of the program."""
        return _text(self.data, PROGRAM_REF)

    def calibration_ref(self):
        """The program the calibration says it is for."""
        return _text(self.data, CAL_REF)

    def ids(self):
        return {
            "hardware": _text(self.data, BOOT_HARDWARE),
            "siemens": _text(self.data, BOOT_SIEMENS),
            "boot": self.triplet(BOOT_TRIPLET),
            "program": self.program_ref(),
            "program_part": _text(self.data, PROGRAM_PART),
            "program_triplet": self.triplet(PROGRAM_TRIPLET),
            "calibration": self.calibration_ref(),
            "calibration_triplet": self.triplet(CAL_TRIPLET),
            "calibration_name": _text(self.data, CAL_NAME),
        }

    def end_marks(self):
        """Whether the program and the calibration end in their markers (90 48 24 42 and its reverse)."""
        return tuple(self.data[at:at + len(mark)] == mark for at, mark in (PROGRAM_END_MARK, CAL_END_MARK))

    def reset_target(self):
        """Where the reset vector's JMPS goes."""
        seg, off = self.data[1], self.data[2] | (self.data[3] << 8)
        return (seg << 16) | off


def find_image(kind="stock"):
    """Path of an image, or None when it is not on this machine."""
    path = os.environ.get(ENV_NAMES[kind]) or os.path.join(IMAGES_DIR, DEFAULT_NAMES[kind])
    return path if os.path.isfile(path) else None


def load_file(spec):
    """An image by kind ("stock", "patched") or from a 512 KB file."""
    if spec in DEFAULT_NAMES:
        return load_image(spec)
    with open(spec, "rb") as f:
        return Image(f.read(), os.path.basename(spec))


def load_image(kind="stock"):
    path = find_image(kind)
    if path is None:
        raise FileNotFoundError(f"No {kind} image. Set {ENV_NAMES[kind]}, or put {DEFAULT_NAMES[kind]} in {IMAGES_DIR}")
    with open(path, "rb") as f:
        return Image(f.read(), kind)
