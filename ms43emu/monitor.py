"""The monitoring processor beside the C167: the other end of its 9-byte
SPI exchange, written from the C167's side of the protocol
(the handler at 0x0E09D2; ghidra/out/fn_0E09D2.txt).

The monitor is the SPI master. For each exchange the C167 makes itself
the slave (SSCCON with MS clear), selects the monitor through the chip
select latch at 0x080000 (bit 6 low, from the shadow at 0xFDCA), and lets
PEC move its 9 bytes out of 0xFC30 (channel 3) and the monitor's 9 into
0xFC27 (channel 2). A frame is 8 bytes and their XOR. The C167 handles the
monitor's frame in its task and leaves its next one at 0xFC30, so in each
exchange the monitor answers the frame it saw the time before.

The states, by the first byte of a frame:

  12  hello. The C167 starts with 12 32; it wants 12 back with 0x32 in the
      third byte
  CF  the C167's acknowledgement, with the answer to question 0
  AD  running: the monitor asks question q in the second byte (q =
      index*4 + function, 0..0x27, one after the other), the C167 answers it
      in its next frame (bytes 1-2) from the table at 0x0EE4D2 and four
      answer functions
  3B  the C167 has given up on the monitor (eight resets) and runs without it

What the C167 checks of the monitor's frames: the checksum; the state; the
question following the last one; bits 1-5 of the third byte all inverted
every eight frames; the fourth byte different in the frames 29 and 31 of
each 31 (it saves it in one and compares in the other); each failure
counts three into a debouncer at 0xE015-0xE019, a good frame takes one
off, and at 12 the C167 resets with a reason code (at most eight times,
counted at 0xE7EA). This model does all of that; it does not check the
C167's answers, only keeps them (`answers`).

What the monitor itself measures (bytes 4-7, which the C167 copies to
0xFBD1-0xFBD4) reads `measure()` when that is set, `data` otherwise.
Byte 4 is its own reading of pedal sensor 2, 8 bits: the C167 keeps its
reading of the same sensor (A/D channel 6 >> 2) at 0xE013 and, at 0x0D1CDE,
wants the two within cal 0x7130E (23) of each other, else it sets 0xFD08
bit 0 and the throttle controller stays off (0x0922A0). Byte 5, times
four, past cal 0x709F4 starts a check sequence at 0x0D3D18; 0 keeps out
of it. In the hello frame bytes 5 and 6 are wanted 0 (0x0E0A62).
"""
from c166 import sfr

LATCH = 0x080000                 # the chip select latch (the ADDRSEL3 window)
SELECT = 0x40                    # its bit for the monitor, active low
MS = 0x4000                      # SSCCON: master select
FRAME = 9
HELLO, ACK, RUN, GAVE_UP = 0x12, 0xCF, 0xAD, 0x3B
QUESTIONS = 0x28
STATUS = 0x32                    # the third byte of the hello; its bits 1-5 also toggle while running


def checksum(frame):
    x = 0
    for b in frame[:8]:
        x ^= b
    return x


class Monitor:
    def __init__(self, board):
        self.b = board
        self.data = bytes(4)
        self.measure = None              # fn() -> 4 bytes: what the monitor measures now
        self.heard = []                  # the C167's frames, as received
        self.sent = []                   # the frames sent
        self.answers = {}                # question -> the C167's answer (bytes 1-2 of its next frame)
        self.bad = 0                     # frames from the C167 with a wrong checksum
        self.running = 0                 # AD frames sent
        self.question = 0
        self.last = None                 # the state of the C167's last frame
        self._rx = bytearray()
        self._tx = self._frame()
        board.cpu.hook_write(sfr.SSCCON, self._ssccon)

    # ---- the SPI side ------------------------------------------------------------
    def selected(self):
        cpu = self.b.cpu
        return not cpu.rw(sfr.SSCCON) & MS and not cpu.ext_ram[LATCH] & SELECT

    def _ssccon(self, addr, value):
        self._rx.clear()                 # the C167 sets the port up before every exchange
        return value

    def exchange(self, byte):
        """One byte each way; returns the monitor's."""
        i = len(self._rx)
        if i >= FRAME:
            return 0xFF
        self._rx.append(byte & 0xFF)
        out = self._tx[i]
        if len(self._rx) == FRAME:
            self._heard(bytes(self._rx))
        return out

    # ---- the protocol -----------------------------------------------------------
    def _heard(self, frame):
        if len(self.heard) < 1000:
            self.heard.append(frame)
        if checksum(frame) != frame[8]:
            self.bad += 1
            return
        state = frame[0]
        if state == RUN and self.last in (ACK, RUN):
            self.answers[self.question] = frame[1] << 8 | frame[2]
        self.last = state
        self._tx = self._frame()
        if len(self.sent) < 1000:
            self.sent.append(bytes(self._tx))

    def _frame(self):
        if self.last == HELLO:
            body = [ACK, 0, STATUS, 0]
        elif self.last in (ACK, RUN):
            self.running += 1
            n = self.running
            self.question = n % QUESTIONS
            status = STATUS ^ (0x3E if (n // 8) % 2 else 0)
            injected = 1 if n % 31 == 29 else 0
            body = [RUN, self.question, status, injected]
        elif self.last == GAVE_UP:
            body = [GAVE_UP, 0, STATUS, 0]
        else:
            body = [HELLO, 0, STATUS, 0]
        data = self.measure() if self.measure is not None and self.last in (ACK, RUN) else self.data
        frame = bytes(body) + bytes(data)
        return frame + bytes([checksum(frame)])
