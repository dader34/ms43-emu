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

The vehicle speed the program actually uses is not the one on CAN but a
pulse train on CC30 (P7.6, captured on the falling edge, handler
0x0EB9F0: the pulses counted into 0xE5EA, their period into 0xE5EC..;
0x0DD12C makes the speed of them, needing at least three pulses in two
seconds), as the ASC/DSC sends it on a wire: 6.97 pulses a second per
km/h (C_VS_FAC = 6274 a kilometre, times four). `Peers.set_speed` drives
both. The cluster frame 0x613 carries the fuel level in its byte 2
(litres; the program takes it below 10 as a fault of its own, 100).
"""
from c166.cpu import NEVER

SPEED_CHANNEL = 30                      # CC30, the second CAPCOM unit
PULSES_PER_KMH_S = 6274 * 4 / 3600.0    # pulses a second at 1 km/h
FUEL_LITRES = 40

ASC1, ASC3, STEERING = 0x153, 0x1F3, 0x1F5
KOMBI1, KOMBI2 = 0x613, 0x615
EGS1, EGS3 = 0x43F, 0x43D
PERIOD_MS = {ASC1: 10, ASC3: 10, STEERING: 10, KOMBI1: 200, KOMBI2: 200, EGS1: 10, EGS3: 10}


class Peers:
    """A board device: the other modules' frames, each at its period."""

    def __init__(self, board, automatic=False):
        self.b = board
        self.data = {ident: bytes(8) for ident in PERIOD_MS}
        self.data[KOMBI1] = bytes([0, 0, FUEL_LITRES, 0, 0, 0, 0, 0])
        if not automatic:
            for ident in (EGS1, EGS3):
                del self.data[ident]
        self.sent = 0
        self.speed_kmh = 0.0
        self.pulses = 0
        self._level = 1
        self._pulse_at = NEVER
        board.capcom2.pins[SPEED_CHANNEL] = 1
        now = board.cpu.instructions
        self._due = {ident: now + 1 for ident in self.data}
        self.next_at = now + 1
        board.devices.append(self)
        self.set_speed(0.0)

    def set_speed(self, kmh):
        """Vehicle speed: the pulses on CC30 and ASC1's bytes 1-2 (little
        endian, 1/8 km/h)."""
        self.speed_kmh = max(0.0, kmh)
        v = int(self.speed_kmh * 8) & 0xFFFF
        d = bytearray(self.data[ASC1])
        d[1], d[2] = v & 0xFF, v >> 8
        self.data[ASC1] = bytes(d)
        if self.speed_kmh <= 0:
            self._pulse_at = NEVER
        elif self._pulse_at == NEVER:
            self._pulse_at = self.b.cpu.instructions + self._half_period()
        self.next_at = min(self.next_at, self._pulse_at)
        self.b.plan()

    def set_fuel(self, litres):
        d = bytearray(self.data[KOMBI1])
        d[2] = int(max(0, min(127, litres)))
        self.data[KOMBI1] = bytes(d)

    def _half_period(self):
        return max(1, int(self.b.ips / (2 * PULSES_PER_KMH_S * self.speed_kmh)))

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
        if now >= self._pulse_at:
            self._level ^= 1
            self.b.capcom2.edge(SPEED_CHANNEL, self._level)
            if not self._level:
                self.pulses += 1
            self._pulse_at = now + self._half_period() if self.speed_kmh > 0 else NEVER
        self.next_at = min(min(self._due.values()), self._pulse_at)
