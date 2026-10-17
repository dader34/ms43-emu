"""The electronic throttle: a DC motor on an H-bridge turning the plate
against its return spring, the plate's angle read back by the two sensors
(sensors.py, A/D channels 12 and 11).

How the program drives it (430069, 0x09273C): its signed command at 0xE0C8
(-0x8000 full closing .. 0x7FFF full opening) goes out as

    PWM channel 2   2.5 ms period (PP2 0xEA5F at fCPU), PW2 = |cmd| * 2 * 0xEA60 >> 16
                    for opening, PP2+1 minus that for closing
    P6.5            the direction: low opening, high closing

so the bridge's drive is PW2 / (PP2 + 1) - P6.5, from -1 to 1: the PWM
output's high share less the direction pin, as across a bridge whose two
inputs are the PWM output and the direction pin. The controller only runs
with a learnt throttle in the EEPROM (eeprom.learnt) and the monitor
agreeing on the pedal (monitor.py).

The plate, in degrees, is a mass on a spring with a limp-home point:
above it the spring closes the plate, below it it opens it, so with no
drive it rests there -- as the program expects (the limp position it keeps
in the EEPROM, 0x98 counts of sensor 1, 4.9 degrees). It stops at 0 and at
MAX_DEG. The numbers are plausible, not measured; the program's
controller closes the loop around whatever they are.
"""
from c166 import sfr

LIMP_DEG = 4.9
MAX_DEG = 85.0
STEP_S = 0.0005                 # the model's time step
# the plate: acceleration per unit of net drive, deg/s^2; the spring, as a
# share of full drive; damping, drive per deg/s
GAIN = 40000.0
SPRING_PRELOAD = 0.12           # at the limp point, either way
SPRING_RATE_OPEN = 0.004        # per degree above it
SPRING_RATE_CLOSED = 0.03       # per degree below it
DAMPING = 1 / 400.0


class Throttle:
    """A board device: every STEP_S it reads the bridge drive and moves the
    plate; the sensors read `deg`."""

    def __init__(self, board, sensors):
        self.b = board
        self.sensors = sensors
        self.deg = LIMP_DEG
        self.rate = 0.0                 # deg/s
        self.step = max(1, int(board.ips * STEP_S))
        self.next_at = board.cpu.instructions + self.step
        sensors.throttle_deg = self.deg
        board.devices.append(self)
        board.plan()

    def drive(self):
        """The H-bridge's drive, -1 (closing) .. 1 (opening)."""
        cpu = self.b.cpu
        if not cpu.rw(sfr.PWMCON0) & 0x4:          # PWM 2 stopped: the bridge is off
            return 0.0
        high = cpu.rw(sfr.PW2) / (cpu.rw(sfr.PP2) + 1)
        return max(-1.0, min(1.0, high - (1 if cpu.rw(sfr.P6) & 0x20 else 0)))

    def spring(self):
        d = self.deg - LIMP_DEG
        if d > 0:
            return SPRING_PRELOAD + SPRING_RATE_OPEN * d
        if d < 0:
            return -SPRING_PRELOAD + SPRING_RATE_CLOSED * d
        return 0.0

    def service(self, now):
        if now < self.next_at:
            return
        net = self.drive() - self.spring() - DAMPING * self.rate
        self.rate += GAIN * net * STEP_S
        self.deg += self.rate * STEP_S
        if self.deg <= 0.0:
            self.deg, self.rate = 0.0, max(0.0, self.rate)
        elif self.deg >= MAX_DEG:
            self.deg, self.rate = MAX_DEG, min(0.0, self.rate)
        self.sensors.throttle_deg = self.deg
        self.next_at = now + self.step
