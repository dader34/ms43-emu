"""The E46's side of the DME's CAN bus: the modules it listens for, so it
sees a live car instead of a bus with nobody on it.

The program's receive objects (docs/status.md "CAN") take 0x153 (ASC1:
the ASC/DSC's status, vehicle speed and torque requests), 0x1F3 and 0x1F5
(ASC3, the steering angle sensor), 0x613 and 0x615 (the instrument
cluster: odometer and outside temperature, A/C requests) and 0x43D/0x43F
(the automatic gearbox). `Peers` sends each at its period with the
published E46 layouts where they matter (the speed in ASC1, bytes 1-2
little endian in 1/8 km/h, as ms45-emu's e46.py has it) and zeros
elsewhere, which the program takes as nothing requested.

What hangs on it: without vehicle speed on the bus the program does not
take the car as standing still, so its idle speed control never engages;
it treats a released pedal as trailing throttle and only props the
engine up with a little air near stalling (the n_sp_min support).
"""
from c166.cpu import NEVER

ASC1, ASC3, STEERING = 0x153, 0x1F3, 0x1F5
KOMBI1, KOMBI2 = 0x613, 0x615
EGS1, EGS3 = 0x43F, 0x43D
PERIOD_MS = {ASC1: 10, ASC3: 10, STEERING: 10, KOMBI1: 200, KOMBI2: 200, EGS1: 10, EGS3: 10}


class Peers:
    """A board device: the other modules' frames, each at its period."""

    def __init__(self, board, automatic=False):
        self.b = board
        self.data = {ident: bytes(8) for ident in PERIOD_MS}
        if not automatic:
            for ident in (EGS1, EGS3):
                del self.data[ident]
        self.sent = 0
        self.speed_kmh = 0.0
        self.set_speed(0.0)
        now = board.cpu.instructions
        self._due = {ident: now + 1 for ident in self.data}
        self.next_at = now + 1
        board.devices.append(self)
        board.plan()

    def set_speed(self, kmh):
        """Vehicle speed in ASC1, bytes 1-2 little endian, 1/8 km/h."""
        self.speed_kmh = kmh
        v = int(max(0.0, kmh) * 8) & 0xFFFF
        d = bytearray(self.data[ASC1])
        d[1], d[2] = v & 0xFF, v >> 8
        self.data[ASC1] = bytes(d)

    def service(self, now):
        if now < self.next_at:
            return
        ms = self.b.ips // 1000
        for ident, due in self._due.items():
            if now >= due:
                self.b.can.receive(ident, self.data[ident])
                self.sent += 1
                self._due[ident] = due + PERIOD_MS[ident] * ms
                if self._due[ident] <= now:
                    self._due[ident] = now + PERIOD_MS[ident] * ms
        self.next_at = min(self._due.values())
