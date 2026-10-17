-- ms43dme on the game engine side: console helpers that reach the vehicle
-- extension in the player's vehicle.
--
--   ms43dme_ge.status()            the vehicle's bridge status
--   ms43dme_ge.enable(true|false)  take or hand back the throttle
--   ms43dme_ge.host("127.0.0.1", 4343)

local M = {}

local function vehicle()
  return be and be:getPlayerVehicle(0) or nil
end

local function status()
  local v = vehicle()
  if v then v:queueLuaCommand("ms43dme.status()") end
end

local function enable(on)
  local v = vehicle()
  if v then v:queueLuaCommand("ms43dme.setEnabled(" .. tostring(on and true or false) .. ")") end
end

local function host(h, p)
  local v = vehicle()
  if v then v:queueLuaCommand(string.format("ms43dme.setHost(%q, %d)", h or "127.0.0.1", p or 4343)) end
end

M.status = status
M.enable = enable
M.host = host
M.onExtensionLoaded = function() log("I", "ms43dme", "loaded; ms43dme_ge.status() / enable() / host()") end

return M
