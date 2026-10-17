"""The engine as the DME's plant: the air it draws through the throttle,
the torque it makes from what the program injects and fires, and -- with
nobody else turning it -- its speed.

Two uses. Under BeamNG the car turns the engine: the bridge feeds the
plant the car's rpm and the throttle model's plate angle, the plant gives
the sensor model the air mass (the MAF) and the car a throttle fraction
and a torque factor (bridge.py). Alone, `Dyno` closes the loop here: it
integrates the engine speed from the torque against a load and drives
the crank model, so the program idles the engine by itself (tests).

The air path is the program's own: its calibration holds the air it
expects per cylinder filling for a throttle angle and a speed (the 16 x 16
"MAF diagnosis" table, cal 0x7AAEE, which the DBW idle control works back
through to pick a plate angle), and `Air.from_image` reads it, so the
plant and the program agree about what the plate does. Without an image
`Air()` is a steady-state orifice-and-pump balance: the plate an orifice
of area A(theta) under the ambient pressure, the cylinders a pump of
displacement VD at volumetric efficiency VE(rpm) drawing from the
manifold at pressure p, p where the two flows meet. Its closed-plate
angle is fitted to the table.

Torque: fuel per cycle from the program's pulse width (0x040DAA, dead
time and flow fitted to the program's own idle and full load: see
FUEL_MG_PER_MS), lambda from that and the air, a burn fraction from
lambda, an ignition efficiency from how far the program's advance
(live.IGNITION) is from MBT, thermal efficiency, less friction. The
numbers are an M54B25's as far as public figures go (2494 cc, 245 Nm,
6500 rpm limiter); the shape is a textbook's, not measured.
"""
import math

from . import live

# ---- the engine ---------------------------------------------------------------------
DISPLACEMENT_L = 2.494
CYLINDERS = 6
THROTTLE_BORE_MM = 62.0           # the M54's throttle body
MAX_DEG = 85.0                    # the plate's stop (throttle.py)
LEAK_MM2 = 10.0                   # past the closed plate: enough for 5 kg/h or so at 900 rpm
CLOSED_DEG = 15.0                 # where the plate sits in the bore at the stop, as the program's table has it
AMBIENT_KPA = 101.3
R_AIR = 287.0                     # J/(kg K)
CD = 0.8                          # discharge coefficient of the plate
# volumetric efficiency over rpm, a long-stroke six with VANOS: broad and flat
VE_CURVE = [(0, 0.60), (800, 0.74), (1500, 0.82), (2500, 0.88), (3500, 0.92), (4500, 0.93),
            (5500, 0.90), (6500, 0.84), (7500, 0.72)]
# the program's fuel: pulse width to milligrams. From its own numbers:
# 137 mg/stroke at 2.77 ms idling (lambda 1) and 458 mg at 9.2 ms at full
# load (lambda ~0.88) fit a 0.47 ms dead time at 4.05 mg/ms
INJECTOR_DEAD_MS = 0.47
FUEL_MG_PER_MS = 4.05
FUEL_CUT_MS = 0.8                 # the program's "no fuel": it leaves 0.6-0.7 ms, the dead time, in 0x040DAA
MAX_RPM = 8000.0                  # where the valves float: the plant's own ceiling
STOICH = 14.7
LHV_J_PER_MG = 44.0               # 44 MJ/kg
THERMAL_EFFICIENCY = 0.40
MBT_DEG = (32.0, 24.0)            # advance for best torque at no load and full load, degrees BTDC
IGNITION_LOSS = 0.0008            # torque fraction lost per degree^2 from MBT
FRICTION_NM = (18.0, 30.0)        # at rest and the extra at 6500 rpm (rising as rpm^2)
COLD_FRICTION_NM = 25.0           # more at -20 C, fading by 80 C
INERTIA = 0.25                    # kg m^2, the engine and flywheel alone


def _interp(curve, x):
    if x <= curve[0][0]:
        return curve[0][1]
    for (x0, y0), (x1, y1) in zip(curve, curve[1:]):
        if x <= x1:
            return y0 + (y1 - y0) * (x - x0) / (x1 - x0)
    return curve[-1][1]


def volumetric_efficiency(rpm):
    return _interp(VE_CURVE, rpm)


def throttle_area_mm2(deg):
    """The open area past the plate: the bore less the plate's projection,
    plus the leak at the closed stop."""
    deg = max(0.0, min(MAX_DEG, deg))
    bore = math.pi * (THROTTLE_BORE_MM / 2) ** 2
    projection = bore * math.cos(math.radians(deg + CLOSED_DEG)) / math.cos(math.radians(CLOSED_DEG))
    return LEAK_MM2 + max(0.0, bore - projection)


def _flow_function(ratio):
    """Compressible flow through an orifice: the mass flow factor for a
    downstream/upstream pressure ratio (choked below 0.528)."""
    g = 1.4
    if ratio <= 0.528:
        return math.sqrt(g) * (2 / (g + 1)) ** ((g + 1) / (2 * (g - 1)))
    return math.sqrt(2 * g / (g - 1) * (ratio ** (2 / g) - ratio ** ((g + 1) / g)))


def throttle_flow_kgs(area_mm2, map_kpa, ambient_kpa, air_c):
    """Mass flow through the plate for a manifold pressure."""
    t = air_c + 273.15
    p0 = ambient_kpa * 1000
    return CD * area_mm2 * 1e-6 * p0 / math.sqrt(R_AIR * t) * _flow_function(min(1.0, map_kpa / ambient_kpa))


def pumping_flow_kgs(rpm, map_kpa, air_c):
    """What the cylinders draw from the manifold."""
    t = air_c + 273.15
    density = map_kpa * 1000 / (R_AIR * t)
    return volumetric_efficiency(rpm) * DISPLACEMENT_L * 1e-3 * rpm / 120 * density


def manifold_kpa(throttle_deg, rpm, air_c=20.0, ambient_kpa=AMBIENT_KPA):
    """The manifold pressure at which the plate's flow meets the pump's."""
    if rpm <= 0:
        return ambient_kpa
    area = throttle_area_mm2(throttle_deg)
    lo, hi = 1.0, ambient_kpa
    for _ in range(40):
        mid = (lo + hi) / 2
        if throttle_flow_kgs(area, mid, ambient_kpa, air_c) > pumping_flow_kgs(rpm, mid, air_c):
            lo = mid                       # the plate lets in more than the pump takes: p rises
        else:
            hi = mid
    return (lo + hi) / 2


def air_kgh(throttle_deg, rpm, air_c=20.0, ambient_kpa=AMBIENT_KPA):
    """The air mass flow the MAF sees, kg/h."""
    if rpm <= 0:
        return 0.0
    p = manifold_kpa(throttle_deg, rpm, air_c, ambient_kpa)
    return pumping_flow_kgs(rpm, p, air_c) * 3600


def air_fraction(throttle_deg, rpm, air_c=20.0):
    """The air at this plate angle as a share of the air at the plate's
    stop, same speed: what the throttle means to a car model that takes
    throttle as a share of full torque (the BeamNG bridge)."""
    if rpm <= 0:
        return 0.0
    full = air_kgh(MAX_DEG, rpm, air_c)
    return air_kgh(throttle_deg, rpm, air_c) / full if full > 0 else 0.0


def air_mg_per_stroke(kgh, rpm):
    """Air per cylinder filling, mg: the program's "load"."""
    if rpm <= 0:
        return 0.0
    strokes_per_s = rpm / 120 * CYLINDERS
    return kgh / 3.6 * 1000 / strokes_per_s


# ---- the program's own air table ---------------------------------------------------------
# "MAF diagnosis (16x16) TPS vs engine speed", mg a filling (XDF MS430069
# "IP_MAF_1_DIAG"): the air the program expects at a throttle angle and a
# speed, and what its model-based idle control works back through
MAF_TABLE = 0x7AAEE                   # 16 x 16 words, row = speed, column = angle, x 0.021194781 mg
MAF_TABLE_DEG = 0x75206               # 16 words, x 0.0018310547 degrees
MAF_TABLE_RPM = 0x74633               # 16 bytes, x 32 rpm


class Air:
    """Air through the throttle: `kgh(deg, rpm)`, `fraction(deg, rpm)`,
    `mg(deg, rpm)`. From the program's table when made with `from_image`,
    from the orifice model otherwise."""

    def __init__(self, table=None):
        self.table = table                # (degs, rpms, rows of mg) or None

    @classmethod
    def from_image(cls, image):
        d = image.data
        word = lambda a: d[a] | d[a + 1] << 8
        degs = [word(MAF_TABLE_DEG + 2 * i) * 0.0018310547 for i in range(16)]
        rpms = [d[MAF_TABLE_RPM + i] * 32.0 for i in range(16)]
        rows = [[word(MAF_TABLE + 2 * (16 * r + c)) * 0.021194781 for c in range(16)] for r in range(16)]
        return cls((degs, rpms, rows))

    def mg(self, deg, rpm, air_c=20.0):
        """Air per cylinder filling, mg."""
        if rpm <= 0:
            return 0.0
        if self.table is None:
            return air_mg_per_stroke(air_kgh(deg, rpm, air_c), rpm)
        degs, rpms, rows = self.table
        deg = max(degs[0], min(degs[-1], deg))
        r = max(rpms[0], min(rpms[-1], rpm))
        i = max(0, min(14, next((k for k in range(15) if rpms[k + 1] >= r), 14)))
        j = max(0, min(14, next((k for k in range(15) if degs[k + 1] >= deg), 14)))
        fr = (r - rpms[i]) / (rpms[i + 1] - rpms[i])
        fc = (deg - degs[j]) / (degs[j + 1] - degs[j])
        a = rows[i][j] + (rows[i][j + 1] - rows[i][j]) * fc
        b = rows[i + 1][j] + (rows[i + 1][j + 1] - rows[i + 1][j]) * fc
        mg = a + (b - a) * fr
        # the table is at the program's reference temperature; colder air is denser
        return mg * (293.15 / (air_c + 273.15))

    def kgh(self, deg, rpm, air_c=20.0):
        return self.mg(deg, rpm, air_c) * CYLINDERS * rpm / 120 * 3.6 / 1000

    def fraction(self, deg, rpm, air_c=20.0):
        """The air at this angle as a share of the air at the plate's stop,
        same speed: what the throttle means to a car model that takes
        throttle as a share of full torque (the BeamNG bridge)."""
        full = self.mg(MAX_DEG, rpm, air_c)
        return self.mg(deg, rpm, air_c) / full if full > 0 else 0.0


# ---- torque ---------------------------------------------------------------------
def fuel_mg(injector_ms):
    return max(0.0, injector_ms - INJECTOR_DEAD_MS) * FUEL_MG_PER_MS


def burn_fraction(lam):
    """How much of the energy available (the fuel's, or the air's share of
    it when rich) a mixture at lambda releases: all of it from rich to
    somewhat lean, fading to a misfire past that. Rich mixtures burn all
    the air down to the puddling of a cold start's long pulses."""
    if lam <= 0:
        return 0.0
    if lam < 1.0:
        return max(0.3, min(1.0, (lam - 0.1) / 0.4))        # rich: 1 from 0.5 down, a third at 0.2
    return max(0.0, min(1.0, (1.7 - lam) / 0.4))            # lean: 1 to 1.3, out by 1.7


def fuelled(injector_ms):
    """Whether the program is injecting at all: its cut leaves the dead time."""
    return injector_ms >= FUEL_CUT_MS


# ---- the program's limiter --------------------------------------------------------------
HARD_LIMIT_TABLE = 0x75D41          # ID_N_MAX_MAX_MT: six bytes x 32 rpm, by gear (the manual gearbox's)


def hard_limit_rpm(image):
    """The program's hard limiter (the fuel cut) for a manual gearbox, the
    lowest gear's: cal 0x5D41. The emulator sees the program's soft limiter
    close the throttle but not the injection stage's cut, which does not
    show in the pulse width it keeps in RAM; the bridge applies this one."""
    return min(image.data[HARD_LIMIT_TABLE + i] * 32 for i in range(6))


def ignition_efficiency(advance_deg, load_fraction):
    """Torque relative to MBT for an ignition advance, degrees BTDC."""
    mbt = MBT_DEG[0] + (MBT_DEG[1] - MBT_DEG[0]) * max(0.0, min(1.0, load_fraction))
    return max(0.0, 1 - IGNITION_LOSS * (mbt - advance_deg) ** 2)


def friction_nm(rpm, coolant_c=90.0):
    cold = COLD_FRICTION_NM * max(0.0, min(1.0, (80 - coolant_c) / 100))
    return FRICTION_NM[0] + cold + FRICTION_NM[1] * (rpm / 6500) ** 2


def torque_nm(air_mg, rpm, injector_ms, advance_deg, coolant_c=90.0, full_mg=None):
    """Brake torque from the air drawn (mg a filling), the fuel injected
    (the pulse width) and the spark (degrees BTDC), Nm. `full_mg` is the
    filling at the plate's stop at this speed, for the load MBT depends on."""
    if rpm <= 0:
        return 0.0
    air = air_mg
    fuel = fuel_mg(injector_ms)
    lam = air / (STOICH * fuel) if fuel > 0 else 99.0
    energy = min(fuel, air / STOICH) * LHV_J_PER_MG * burn_fraction(lam) * CYLINDERS   # J a cycle
    full = full_mg if full_mg is not None else air_mg_per_stroke(air_kgh(MAX_DEG, rpm), rpm)
    load = air / full if full > 0 else 0.0
    indicated = THERMAL_EFFICIENCY * energy / (4 * math.pi) * ignition_efficiency(advance_deg, load)
    return indicated - friction_nm(rpm, coolant_c)


# ---- the loop closed here ---------------------------------------------------------------
class Dyno:
    """Turns the board's crank from the plant's torque against a load: a
    board device stepping every STEP_S. `rpm` is the engine's speed; `load_nm`
    the torque taken off it (a brake); `torque_nm` the last brake torque."""

    STEP_S = 0.002

    def __init__(self, board, sensors, throttle, rpm=0.0, load_nm=0.0, inertia=INERTIA, air=None):
        self.b = board
        self.sensors = sensors
        self.throttle = throttle
        self.air = air if air is not None else Air.from_image(board.image)
        self.rpm = rpm
        self.load_nm = load_nm
        self.inertia = inertia
        self.torque_nm = 0.0
        self.starter_nm = 0.0              # a starter motor's torque while cranking
        self.step = max(1, int(board.ips * self.STEP_S))
        self.next_at = board.cpu.instructions + self.step
        board.devices.append(self)
        board.plan()
        self._apply()

    def crank(self, nm=40.0):
        """The starter engaged (and off again with 0)."""
        self.starter_nm = nm

    def _apply(self):
        self.sensors.maf_kgh = self.air.kgh(self.throttle.deg, self.rpm, self.sensors.intake_c)
        if self.rpm != self.b.engine.rpm:
            self.b.engine.set_rpm(self.rpm)

    def service(self, now):
        if now < self.next_at:
            return
        v = live.read(self.b)
        advance = sum(v["ignition_deg"].values()) / len(v["ignition_deg"])
        mg = self.air.mg(self.throttle.deg, self.rpm, self.sensors.intake_c)
        full = self.air.mg(MAX_DEG, self.rpm, self.sensors.intake_c)
        inj = v["injector_ms"] if fuelled(v["injector_ms"]) else 0.0
        self.torque_nm = torque_nm(mg, self.rpm, inj, advance, self.sensors.coolant_c, full) if self.rpm > 0 else 0.0
        net = self.torque_nm + self.starter_nm - self.load_nm
        if self.rpm <= 0 and net <= 0:
            net = 0.0
        omega = self.rpm * 2 * math.pi / 60 + net / self.inertia * self.STEP_S
        self.rpm = max(0.0, min(MAX_RPM, omega * 60 / (2 * math.pi)))
        if self.rpm < 60 and self.starter_nm == 0 and net < 0:
            self.rpm = 0.0                 # stalled
        self._apply()
        self.next_at = now + self.step
