"""The engine's position sensors, as the MS43 sees them on its CAPCOM pins.

    crank   60-2 wheel, one falling edge a tooth          CC15 (P2.15) = T7IN, T0IN (P3.0), CAPIN (P3.2)
    intake  cam, one high and one low half a cam turn     CC14 (P2.14)
    exhaust cam, the same, offset                         CC13 (P2.13)

How the program takes them (430069, see docs/status.md "The engine"):
CC15 captures T1 on every falling edge and PEC channel 7 copies the time
into a buffer at 0xE5B4, so a tooth costs no interrupt; CC14 and CC13
capture both edges and their handlers (0x0E3918, 0x0DFE42) read the pin
level in P2 to know which edge it was. From the intake cam's two edges
and the teeth between them the program finds the tooth period and,
after a few consistent turns, syncs.

Position is counted in tooth pitches over the four-stroke cycle: 120 of
them in 720 degrees, 6 degrees each. Tooth k of a turn (k = pitch mod 60)
is there for k < 58; 58 and 59 are the gap. A tooth's falling edge is at
the start of its pitch, its rising edge half a pitch later.

The cam edges are half a cam turn (60 pitches) apart, as NXT-Tronic's
MS43 notes give them; where the intake's sit is what the program's cam
window check asks for (see CAM_EDGES).

Time is the board's instruction count; `rpm` may be changed at any time
and the next pitch is timed at the new speed.
"""
from c166.cpu import NEVER

PITCHES = 120                      # tooth pitches in a four-stroke cycle
TEETH = 60                         # pitches in a turn
MISSING = 2                        # teeth left out before the reference
CRANK, INTAKE, EXHAUST = 15, 14, 13
# Where the program wants them (all as angles of T0, which reads pitch + 1
# once synced):
#   - intake: rising edge in 31-46, falling in 91-106, else 0x0A4620
#     faults the cam (0xFC4D bit 6);
#   - exhaust: rising in 14-26, falling in 74-86 (0x0A4920);
#   - at the gaps the CC24 state at 0x0E40D0 takes the two cam levels and
#     wants both high at one gap and both low at the other -- that is how
#     it tells the two turns of the cycle apart.
# The middles of the windows satisfy all three.
CAM_EDGES = {INTAKE: (38, 98), EXHAUST: (19, 79)}


class Engine:
    """The crank and cam signals for a given speed. A board device: it has
    `next_at` and `service(now)`."""

    def __init__(self, board, rpm=0.0):
        self.b = board
        self.capcom = board.capcom
        self.rpm = rpm
        self.pitch = 0                      # the pitch the crank is in, 0..119
        self.half = False                   # in the second half of the pitch
        self.revolutions = 0
        self.teeth = 0                      # falling edges sent
        self.next_at = NEVER
        # the pins rest high: no edge until the engine turns
        for n in (CRANK, INTAKE, EXHAUST):
            self.capcom.pins[n] = 1
        for n, (rise, fall) in CAM_EDGES.items():
            high = rise <= self.pitch < fall if rise < fall else (self.pitch >= rise or self.pitch < fall)
            self.capcom.pins[n] = 1 if high else 0

    def pitch_instructions(self):
        """Instructions per tooth pitch at the current speed (a turn is 60)."""
        return self.b.ips * 60.0 / (self.rpm * TEETH)

    def start(self, rpm):
        """Turn at `rpm` from now on (0 stops it), with an edge right away."""
        self.rpm = rpm
        self.next_at = self.b.cpu.instructions + 1 if rpm > 0 else NEVER
        self.b.plan()

    def set_rpm(self, rpm):
        """Change the speed without disturbing the pattern: the edge due next
        keeps its share of the pitch at the new speed. For a plant that
        updates the speed every few milliseconds (plant.py, bridge.py);
        `start` would send an edge at each update."""
        if rpm <= 0:
            self.rpm = 0.0
            self.next_at = NEVER
            self.b.plan()
            return
        if self.rpm <= 0 or self.next_at == NEVER:
            self.start(rpm)
            return
        now = self.b.cpu.instructions
        old_half = self.pitch_instructions() / 2
        remaining = max(0, self.next_at - now)
        self.rpm = rpm
        self.next_at = now + max(1, int(remaining * (self.pitch_instructions() / 2) / old_half))
        self.b.plan()

    def _crank(self, level):
        """The crank signal: CC15's pin (P2.15), which is also T7IN, T0IN
        (P3.0) and CAPIN (P3.2). After sync the program counts the teeth on
        both T0 and T7 (TxI falling edges) and its stall check compares
        them; at a gap it has not confirmed it times the teeth through
        CAPIN (T5 into CAPREL, the CRIC handler at 0x0DFEDA)."""
        self.capcom.edge(CRANK, level)
        self.b.capcom2.count_edge(level)
        self.capcom.count_edge(level)
        self.b.capin.edge(level)

    def service(self, now):
        if self.rpm <= 0:
            self.next_at = NEVER
            return
        k = self.pitch % TEETH
        tooth = k < TEETH - MISSING
        cc = self.capcom
        if not self.half:
            # the start of a pitch: the tooth's falling edge, and any cam edge
            if tooth:
                self._crank(0)
                self.teeth += 1
            for n, (rise, fall) in CAM_EDGES.items():
                if self.pitch == rise:
                    cc.edge(n, 1)
                elif self.pitch == fall:
                    cc.edge(n, 0)
            self.half = True
        else:
            if tooth:
                self._crank(1)
            self.half = False
            self.pitch = (self.pitch + 1) % PITCHES
            if self.pitch % TEETH == 0:
                self.revolutions += 1
        self.next_at = now + max(1, int(self.pitch_instructions() / 2))
