"""The DME wired to a car simulator: BeamNG, or anything that can say what
the engine is doing and take what the DME makes of it.

The car turns the engine; the DME runs on the board here, in the module's
time. Each update the car gives (`update`): the engine speed, the pedal,
the coolant and intake air temperatures, the vehicle speed, the ignition
key and the starter. The DME's answer (`outputs`) is what the car's engine
model needs: the throttle, as the share of full-load air the plate lets in
at this speed (plant.Air, the program's own table), a torque factor for
what the program does with fuel and spark (nothing injected, or the
mixture or the ignition timing taking torque away), and for the dashboard
the engine speed as the DME measures it, the plate angle, the injection
time, the ignition advance, the load and the air mass.

The wire is `encode`/`decode`: one JSON object a datagram each way, the
keys below. tools/beamng.py serves it on UDP in real time; the BeamNG side
is beamng/ms43dme.
"""
import json

from . import live, eeprom, plant
from .board import Board
from .sensors import Sensors
from .throttle import Throttle
from .e46 import Peers

IPS = 6_000_000                        # instructions a second: real time under PyPy
OPERATING_STATE = 0xF900               # 0 seeking, 1 cranking, 2 running (docs/status.md "The sync")
SETPOINT = 0xE14E                      # the throttle setpoint, 0.0073 degrees a count

# the car's side: key -> (attribute, default)
INPUTS = {
    "rpm": ("rpm", 0.0),               # engine speed the car turns the crank at
    "pedal": ("pedal", 0.0),           # accelerator, 0..1
    "coolant": ("coolant_c", 20.0),    # degrees C
    "iat": ("intake_c", 20.0),
    "speed": ("speed_kmh", 0.0),       # vehicle speed, km/h (on CAN from the ASC)
    "ignition": ("ignition", 1),       # the key: 0 off, 1 on (BeamNG's ignitionLevel >= 2)
    "starter": ("starter", 0),         # 1 while cranking
}


class Bridge:
    def __init__(self, image, ips=IPS, learnt=(0x68, 0x39C, 0x98, 0x36C)):
        self.image = image
        self.board = Board(image, ips=ips)
        self.sensors = Sensors(self.board)
        self.throttle = Throttle(self.board, self.sensors)
        self.peers = Peers(self.board)
        self.air = plant.Air.from_image(image)
        self.hard_limit = plant.hard_limit_rpm(image)
        self.board.eeprom.words = eeprom.learnt(image, *learnt)
        self.rpm = 0.0
        self.pedal = 0.0
        self.coolant_c = 20.0
        self.intake_c = 20.0
        self.speed_kmh = 0.0
        self.ignition = 1
        self.starter = 0
        self.powered = False
        self.updates = 0
        self.power(True)

    # ---- the car's side ------------------------------------------------------------
    def power(self, on):
        """The key: on boots the module from its reset vector, off stops it."""
        if on and not self.powered:
            self.board.power_cycle()
        self.powered = bool(on)

    def update(self, **car):
        """New readings from the car (the INPUTS keys); unknown keys are ignored."""
        for key, value in car.items():
            if key in INPUTS and value is not None:
                setattr(self, INPUTS[key][0], float(value))
        self.updates += 1
        self._apply()

    def _apply(self):
        on = self.ignition >= 1
        if on != self.powered:
            self.power(on)
        s = self.sensors
        s.pedal = max(0.0, min(1.0, self.pedal))
        s.coolant_c, s.intake_c = self.coolant_c, self.intake_c
        s.maf_kgh = self.air.kgh(self.throttle.deg, self.rpm, self.intake_c)
        self.peers.set_speed(self.speed_kmh)
        if self.rpm != self.board.engine.rpm:
            self.board.engine.set_rpm(max(0.0, self.rpm))

    def run(self, seconds):
        """Run the module for `seconds` of its time (the throttle model moves
        meanwhile, so the air is refreshed on the way)."""
        if not self.powered:
            return
        b = self.board
        step = max(1, int(b.ips * 0.0025))
        end = b.cpu.instructions + int(seconds * b.ips)
        while b.cpu.instructions < end:
            b.run(min(step, end - b.cpu.instructions))
            self.sensors.maf_kgh = self.air.kgh(self.throttle.deg, self.rpm, self.intake_c)

    # ---- the DME's side --------------------------------------------------------------
    def outputs(self):
        if not self.powered:
            return {"power": 0, "throttle": 0.0, "torque": 0.0, "fuel": 0, "plate": self.throttle.deg,
                    "rpm": 0, "inj": 0.0, "ign": 0.0, "maf": 0.0, "load": 0.0, "state": 0, "t": self.board.seconds}
        v = live.read(self.board)
        rpm = max(self.rpm, 1.0)
        deg = self.throttle.deg
        fraction = self.air.fraction(deg, rpm, self.intake_c)
        mg = self.air.mg(deg, rpm, self.intake_c)
        full = self.air.mg(plant.MAX_DEG, rpm, self.intake_c)
        # fuel: what the program injects, against the air it fuels for (its
        # own load), cut where it cuts and at its hard limiter (plant.hard_limit_rpm)
        fuel = plant.fuel_mg(v["injector_ms"])
        lam = v["load_mg"] / (plant.STOICH * fuel) if fuel > 0 else 99.0
        advance = sum(v["ignition_deg"].values()) / 6
        running = self.board.cpu.rb(OPERATING_STATE) == 2
        on = running and plant.fuelled(v["injector_ms"]) and rpm < self.hard_limit
        factor = plant.burn_fraction(lam) * plant.ignition_efficiency(advance, mg / full if full else 0.0) if on else 0.0
        return {
            "power": 1,
            "throttle": round(fraction, 4),            # share of full-load air at this speed
            "torque": round(factor, 4),                # what fuel and spark leave of it
            "fuel": 1 if on else 0,
            "plate": round(deg, 2),                    # degrees
            "setpoint": round(self.board.cpu.rw(SETPOINT) * 0.0073, 2),
            "rpm": round(v["rpm"]),                    # as the DME measures it
            "inj": round(v["injector_ms"], 3),         # ms
            "ign": round(advance, 1),                  # degrees BTDC, mean of the six
            "lambda": round(min(lam, 9.99), 3),
            "maf": round(v["maf_kgh"], 1),             # as the DME reads it, kg/h
            "load": round(v["load_mg"], 1),            # mg a filling
            "coolant": round(v["coolant_c"], 1),
            "state": self.board.cpu.rb(OPERATING_STATE),
            "t": round(self.board.seconds, 3),
        }


# ---- the wire ---------------------------------------------------------------------
def decode(datagram):
    """A datagram from the car: the dict of inputs it carries (empty when it is not ours)."""
    try:
        d = json.loads(datagram.decode("utf-8", "replace"))
    except ValueError:
        return {}
    if not isinstance(d, dict):
        return {}
    return {k: d[k] for k in INPUTS if k in d}


def encode(outputs):
    return json.dumps(outputs, separators=(",", ":")).encode()
