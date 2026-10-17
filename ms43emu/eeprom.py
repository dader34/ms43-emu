"""The serial EEPROM: a 93C86, 1024 words of 16 bits, Microwire.

How the program reaches it (430069): an SPI job, bit 11 of the job word at
0xFDE8, run from the XP0 software interrupt (0x0EB076, entry 0x0EB280).
The job sets the SSC to 16-bit words, raises chip select -- bit 7 of the
external latch 0x080000, active high, from the shadow at 0xFDCA -- waits a
T6 count and reads P3.8 (MRST, the chip's DO): high is "ready", low ends
the job with the error bit (0xFDE8 bit 3). Then it shifts the command word
from 0xE630 out (0x1800 | address for READ: three zeros, the start bit,
opcode 10, ten address bits) and reads the answer. 0x0DF63A starts a read
of one word; 0x0BD7B0 reads a block (the throttle adaptation is 12 bytes at
byte address 0x348, with the valid mark 0x5555; 0x09B2F2).

The chip is modelled bit by bit, so whatever word sizes and order the
program uses come out as on the part: after a READ's last address bit DO
gives a dummy 0, then the sixteen data bits, then (sequential read) the
next word's. WRITE, ERASE, EWEN/EWDS, ERAL and WRAL do what they do on the
part, instantly; DO then reads high (ready) at once. `words` is the
contents; `load`/`save` keep it in a file.
"""
from c166 import sfr

WORDS = 1024
ADDRESS_BITS = 10
LATCH = 0x080000
SELECT = 0x80                    # the latch bit for its chip select, active high
DO_BIT = 0x0100                  # P3.8, MRST


class Eeprom:
    def __init__(self, board):
        self.b = board
        self.words = [0xFFFF] * WORDS
        self.writable = False
        self.writes = 0
        self._reset_command()
        self._do = 1
        cpu = board.cpu
        before = cpu.readers.get(sfr.P3)
        own = lambda a: cpu.low[sfr.P3] | cpu.low[sfr.P3 + 1] << 8
        self._p3 = before or own
        cpu.hook_read(sfr.P3, self._read_p3)

    # ---- the pins --------------------------------------------------------------------
    def selected(self):
        return bool(self.b.cpu.ext_ram[LATCH] & SELECT)

    def _read_p3(self, addr):
        value = self._p3(addr)
        do = self._do if self.selected() else 1
        return value & ~DO_BIT | (DO_BIT if do else 0)

    def exchange(self, word, bits):
        """One SSC transfer while selected: `bits` bits of `word` in, MSB
        first; returns what DO shifted out meanwhile."""
        out = 0
        for i in range(bits - 1, -1, -1):
            out = out << 1 | self._clock((word >> i) & 1)
        return out

    def latch(self, value):
        """A write to the latch: dropping chip select ends any command, and
        with it selected again DO shows ready (high)."""
        if not value & SELECT:
            self._reset_command()
            self._do = 1

    # ---- the protocol ------------------------------------------------------------------
    def _reset_command(self):
        self.started = False
        self.bits = []                   # after the start bit: opcode, address, data
        self.reading = None              # address being read out, with the bits left
        self.out_bits = []

    def _clock(self, bit):
        """One clock: take DI, give DO."""
        if self.out_bits:
            self._do = self.out_bits.pop(0)
            if not self.out_bits and self.reading is not None:
                # sequential read: the next word follows
                self.reading = (self.reading + 1) % WORDS
                self.out_bits = self._word_bits(self.reading)
            return self._do
        if not self.started:
            if bit:
                self.started = True
                self.bits = []
            return self._do
        self.bits.append(bit)
        self._decode()
        return self._do

    def _word_bits(self, address):
        w = self.words[address]
        return [(w >> i) & 1 for i in range(15, -1, -1)]

    def _decode(self):
        b = self.bits
        if len(b) < 2 + ADDRESS_BITS:
            return
        op = b[0] << 1 | b[1]
        addr = 0
        for x in b[2:2 + ADDRESS_BITS]:
            addr = addr << 1 | x
        if op == 0b10:                   # READ
            self.reading = addr
            self.out_bits = self._word_bits(addr)
            self._do = 0                 # the dummy zero, on this clock
            self.started = False
            return
        if op == 0b00:
            sub = addr >> (ADDRESS_BITS - 2)
            if sub == 0b11:              # EWEN
                self.writable = True
            elif sub == 0b00:            # EWDS
                self.writable = False
            elif sub == 0b10:            # ERAL
                if self.writable:
                    self.words = [0xFFFF] * WORDS
            elif sub == 0b01:            # WRAL: needs the data
                if len(b) < 2 + ADDRESS_BITS + 16:
                    return
                if self.writable:
                    self.words = [self._data()] * WORDS
            self._done()
            return
        if op == 0b11:                   # ERASE
            if self.writable:
                self.words[addr] = 0xFFFF
            self._done()
            return
        if op == 0b01:                   # WRITE: needs the data
            if len(b) < 2 + ADDRESS_BITS + 16:
                return
            if self.writable:
                self.words[addr] = self._data()
                self.writes += 1
            self._done()

    def _data(self):
        d = 0
        for x in self.bits[2 + ADDRESS_BITS:2 + ADDRESS_BITS + 16]:
            d = d << 1 | x
        return d

    def _done(self):
        self._do = 1                     # programming takes no time: ready at once
        self.started = False
        self.bits = []

    # ---- contents --------------------------------------------------------------------
    def load(self, path):
        data = open(path, "rb").read()
        self.words = [data[2 * i] << 8 | data[2 * i + 1] for i in range(min(WORDS, len(data) // 2))] + \
            [0xFFFF] * max(0, WORDS - len(data) // 2)

    def save(self, path):
        open(path, "wb").write(bytes(x for w in self.words for x in (w >> 8, w & 0xFF)))


# ---- what the program keeps in it ------------------------------------------------------
CRC_TABLE = 0x0EE87A             # 256 words; the routine at 0x0C9CF8
CRC_INIT = 0x000F
THROTTLE_ADAPTATION = 0x1A4      # word address (byte 0x348), six words (0x09B2F2)
VALID = 0x5555


def crc(image, data):
    """The program's checksum of a record (0x0C9CF8): table-driven, from
    0x000F, a byte at a time."""
    table = image.data
    off = CRC_TABLE - 0x80000
    r = CRC_INIT
    for byte in data:
        i = (byte ^ r) & 0xFF
        r = (r >> 8) ^ (table[off + 2 * i] | table[off + 2 * i + 1] << 8)
    return r


def throttle_adaptation(image, tps1_stop, tps2_stop, tps1_limp, tps2_limp):
    """The six words of a learnt throttle (0x09B330): the two sensors'
    counts at the closed stop and at the spring's limp-home position, the
    valid mark, the checksum of the ten bytes before it."""
    words = [tps1_stop, tps2_stop, tps1_limp, tps2_limp, VALID]
    data = bytes(x for w in words for x in (w & 0xFF, w >> 8))
    return words + [crc(image, data)]


HEADER = 0x19                    # word address (byte 0x32): the program's mark, 8 bytes, and its checksum
PROGRAM_MARK = 0x0EFFB2          # the 8 bytes the header must repeat (0x0BDC2C); otherwise every
                                 # stored adaptation is taken as another program's and ignored


def _words(data):
    return [data[i] | data[i + 1] << 8 for i in range(0, len(data), 2)]


def header(image):
    mark = bytes(image.data[PROGRAM_MARK - 0x80000:PROGRAM_MARK - 0x80000 + 8])
    return _words(mark) + [crc(image, mark)]


def learnt(image, tps1_stop, tps2_stop, tps1_limp, tps2_limp):
    """A whole EEPROM as a car's that has run this program: the header and
    a learnt throttle; everything else erased (0xFFFF)."""
    words = [0xFFFF] * WORDS
    words[HEADER:HEADER + 5] = header(image)
    words[THROTTLE_ADAPTATION:THROTTLE_ADAPTATION + 6] = throttle_adaptation(image, tps1_stop, tps2_stop, tps1_limp, tps2_limp)
    return words
