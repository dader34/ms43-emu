-- ms43dme: the vehicle's engine run by an emulated Siemens MS43 DME.
--
-- Loaded for every vehicle (lua/vehicle/extensions/auto/). It talks UDP
-- to tools/beamng.py of ms43-emu, which runs the DME's real firmware in
-- real time: each graphics frame this sends what the engine is doing
-- (rpm, pedal, temperatures, speed, key, starter) and takes back what the
-- DME makes of it (the throttle plate as a share of full-load air, a
-- torque factor for fuel and spark, and the DME's own readings).
--
-- The engine's throttle is then the DME's: the combustion engine reads
-- its throttle from the electrics value its `electricsThrottleName` names
-- (jbeam "throttle" by default) and this points it at `ms43Throttle`
-- instead, which it fills with throttle x torque factor. BeamNG's own idle
-- throttle stays underneath as a safety net below idleRPM.
--
-- Console: ms43dme.setEnabled(false) hands the throttle back;
-- ms43dme.setHost("192.168.1.10", 4343) for a DME on another machine;
-- ms43dme.status() prints the last answer. Settings persist in
-- settings/ms43dme.json under the user folder when the game lets us write it.

local M = {}

local socket
do
  local ok, lib = pcall(require, "socket")
  if not ok or type(lib) ~= "table" then ok, lib = pcall(require, "libs/luasocket/socket.socket") end
  if ok and type(lib) == "table" then socket = lib end
end

local HOST, PORT = "127.0.0.1", 4343
local SEND_PERIOD = 1 / 60          -- s between datagrams to the DME
local TIMEOUT = 1.0                 -- s without an answer: the DME is gone, hand the throttle back

local udp = nil
local enabled = true
local connected = false
local lastSend = 0
local lastHeard = -math.huge
local lastOut = nil                 -- the DME's last answer, decoded
local engine = nil
local originalThrottleName = nil
local throttleName = "ms43Throttle"
local sent, heard = 0, 0
local ownRpm = 0
local clock = 0                     -- seconds of GFX time, from the dt we are given

local function log(msg)
  print("[ms43dme] " .. tostring(msg))
end

local function findEngine()
  if not powertrain then return nil end
  local e = powertrain.getDevice("mainEngine")
  if e then return e end
  local devices = powertrain.getDevicesByType and powertrain.getDevicesByType("combustionEngine") or nil
  if devices and devices[1] then return devices[1] end
  return nil
end

local function takeThrottle()
  engine = findEngine()
  if not engine then
    log("no combustion engine on this vehicle; idle")
    return false
  end
  if engine.electricsThrottleName then
    if engine.electricsThrottleName ~= throttleName then
      originalThrottleName = engine.electricsThrottleName
      engine.electricsThrottleName = throttleName
    end
    electrics.values[throttleName] = electrics.values[originalThrottleName or "throttle"] or 0
    log("engine throttle taken over through electrics." .. throttleName)
    return true
  end
  log("this engine has no electricsThrottleName: falling back to electrics.throttle each frame")
  return true
end

local function giveThrottleBack()
  if engine and originalThrottleName and engine.electricsThrottleName == throttleName then
    engine.electricsThrottleName = originalThrottleName
  end
  originalThrottleName = nil
end

local function applyThrottle(value)
  value = math.max(0, math.min(1, value or 0))
  if engine and engine.electricsThrottleName == throttleName then
    electrics.values[throttleName] = value
  else
    electrics.values.throttle = value
  end
end

local function openSocket()
  if not socket then
    log("luasocket is not available in this Lua VM")
    return false
  end
  if udp then udp:close() end
  udp = socket.udp()
  udp:settimeout(0)
  local ok, err = udp:setpeername(HOST, PORT)
  if not ok then
    log("cannot reach the DME at " .. HOST .. ":" .. PORT .. ": " .. tostring(err))
    udp = nil
    return false
  end
  return true
end

local function loadSettings()
  if not jsonReadFile then return end
  local ok, cfg = pcall(jsonReadFile, "settings/ms43dme.json")
  if ok and type(cfg) == "table" then
    HOST = cfg.host or HOST
    PORT = cfg.port or PORT
    if cfg.enabled ~= nil then enabled = cfg.enabled end
  end
end

local function saveSettings()
  if not jsonWriteFile then return end
  pcall(jsonWriteFile, "settings/ms43dme.json", {host = HOST, port = PORT, enabled = enabled}, true)
end

local function carState()
  local v = electrics.values
  local rpm = v.rpm or 0
  local ignitionLevel = v.ignitionLevel or 2
  local starter = (ignitionLevel == 3 or (engine and engine.starterEngagedCoef and engine.starterEngagedCoef > 0)) and 1 or 0
  local coolant = v.watertemp or (engine and engine.thermals and engine.thermals.coolantTemperature) or 20
  local iat = (obj and obj.getEnvTemperature and (obj:getEnvTemperature() - 273.15)) or 20
  local speed = (v.wheelspeed or v.airspeed or 0) * 3.6
  local pedal = v.throttle_input or v.throttle or 0
  return {
    rpm = rpm,
    pedal = pedal,
    coolant = coolant,
    iat = iat,
    speed = speed,
    ignition = (ignitionLevel >= 2) and 1 or 0,
    starter = starter,
  }
end

local function updateGFX(dt)
  if not enabled then return end
  if not udp then
    if not openSocket() then
      enabled = false
      return
    end
  end
  clock = clock + (dt or 0)
  local now = clock
  -- everything the DME has sent since the last frame: the last word counts
  while true do
    local data = udp:receive()
    if not data then break end
    local ok, out = pcall(jsonDecode, data)
    if ok and type(out) == "table" and out.throttle ~= nil then
      lastOut = out
      lastHeard = now
      heard = heard + 1
      if not connected then
        connected = true
        log("DME answering")
        takeThrottle()
      end
    end
  end
  if now - lastSend >= SEND_PERIOD then
    lastSend = now
    local state = carState()
    ownRpm = state.rpm
    local ok, msg = pcall(jsonEncode, state)
    if ok then
      udp:send(msg)
      sent = sent + 1
    end
  end
  if connected then
    if now - lastHeard > TIMEOUT then
      connected = false
      log("DME silent for " .. TIMEOUT .. " s: throttle back to the pedal")
      giveThrottleBack()
      return
    end
    if lastOut and lastOut.power == 1 then
      applyThrottle((lastOut.throttle or 0) * (lastOut.torque or 0))
    else
      applyThrottle(0)
    end
    electrics.values.ms43Rpm = lastOut and lastOut.rpm or 0
    electrics.values.ms43Plate = lastOut and lastOut.plate or 0
    electrics.values.ms43Inj = lastOut and lastOut.inj or 0
    electrics.values.ms43Ign = lastOut and lastOut.ign or 0
    electrics.values.ms43Fuel = lastOut and lastOut.fuel or 0
  end
end

local function onInit()
  loadSettings()
  engine = findEngine()
  connected = false
  lastOut = nil
  if enabled then
    openSocket()
    log("DME bridge to " .. HOST .. ":" .. PORT .. (engine and "" or " (no engine here)"))
  end
end

local function onReset()
  connected = false
  lastOut = nil
  giveThrottleBack()
  engine = findEngine()
end

local function setEnabled(on)
  enabled = on and true or false
  if not enabled then
    connected = false
    giveThrottleBack()
    applyThrottle(electrics.values.throttle_input or 0)
  elseif not udp then
    openSocket()
  end
  saveSettings()
  log(enabled and "enabled" or "disabled")
end

local function setHost(host, port)
  HOST = host or HOST
  PORT = port or PORT
  saveSettings()
  openSocket()
  log("DME at " .. HOST .. ":" .. PORT)
end

local function status()
  log(string.format("enabled=%s connected=%s sent=%d heard=%d own rpm=%.0f last=%s",
    tostring(enabled), tostring(connected), sent, heard, ownRpm, lastOut and jsonEncode(lastOut) or "nothing"))
  return lastOut
end

M.onInit = onInit
M.onReset = onReset
M.updateGFX = updateGFX
M.setEnabled = setEnabled
M.setHost = setHost
M.status = status
M.last = function() return lastOut end

return M
