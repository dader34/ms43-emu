# ms43dme: a BeamNG.drive car on the emulated MS43

The mod in `ms43dme/` hands a BeamNG vehicle's throttle to the Siemens
MS43 running in ms43-emu. The DME's real firmware reads the car (engine
speed, pedal, temperatures, vehicle speed, key, starter), runs its own
throttle control, fuelling and ignition, and the car's engine gets what
the DME decides: the throttle plate, as the share of full-load air it
lets through, and a torque factor for the fuel and spark (nothing on an
overrun cut, nothing past the hard limiter, less with the idle retard).

It fits any car with a combustion engine, including an E36 mod: it is a
vehicle Lua extension in `lua/vehicle/extensions/auto/`, which BeamNG
loads for every vehicle, so no jbeam of the car is touched. The engine
is found through `powertrain.getDevice("mainEngine")` and told to read
its throttle from `electrics.ms43Throttle` (its `electricsThrottleName`)
while the DME answers; when the DME goes silent the pedal is handed back.

## Running it

1. The DME, on this machine or another, in real time (PyPy keeps it
   there; see the repository README for the setup):

       .pypy/bin/python tools/beamng.py --print

   It listens on UDP port 4343 and prints a line a second once the car
   talks to it.

2. The mod: copy the `ms43dme` folder into BeamNG's user folder under
   `mods/unpacked/` (so you get `.../mods/unpacked/ms43dme/lua/...`), or
   zip the folder's contents as `ms43dme.zip` into `mods/`. Enable it in
   the Mods Manager if it is not already.

3. Spawn a car. The console (`~`) shows `[ms43dme] DME bridge to
   127.0.0.1:4343` and then `[ms43dme] DME answering` once the first
   packet comes back. From then on the throttle is the DME's.

Console helpers (the vehicle's Lua, `ms43dme.*`, or from the game engine
side `ms43dme_ge.*`):

    ms43dme_ge.status()               the last answer from the DME
    ms43dme_ge.enable(false)          throttle back to the pedal (true takes it again)
    ms43dme_ge.host("192.168.1.10", 4343)   a DME on another machine

Settings persist in `settings/ms43dme.json` under the user folder.

## What the car sees

Each GFX frame the mod sends a JSON datagram and applies the last answer:

    car -> DME   {"rpm": 812, "pedal": 0.0, "coolant": 20.0, "iat": 20.0, "speed": 0.0,
                  "ignition": 1, "starter": 0}
    DME -> car   {"power": 1, "throttle": 0.21, "torque": 0.9, "fuel": 1, "plate": 3.1,
                  "setpoint": 3.0, "rpm": 810, "inj": 2.7, "ign": 8.1, "lambda": 1.0,
                  "maf": 19.8, "load": 137.0, "coolant": 20.2, "state": 2, "t": 12.3}

The engine's throttle becomes `throttle x torque`. `electrics.ms43Rpm`,
`ms43Plate`, `ms43Inj`, `ms43Ign` and `ms43Fuel` carry the DME's own
readings for a gauge or an app.

`ignition` follows BeamNG's `ignitionLevel` (2 or 3 is on): the DME boots
when the key goes on and stops when it goes off. `starter` is 1 while
cranking. The car's own idle logic stays underneath: BeamNG's engine adds
throttle below its `idleRPM` of its own accord, which covers the one thing
the emulated DME does not do yet (docs/status.md, "The idle").

## Limits

- The torque factor scales BeamNG's torque curve; it is a proxy for what
  the DME's fuel and spark do, not a combustion model.
- Vehicle speed reaches the DME on CAN as the ASC would send it; the
  gearbox is not on the bus (the DME takes the car as a manual).
- One car at a time per DME.
