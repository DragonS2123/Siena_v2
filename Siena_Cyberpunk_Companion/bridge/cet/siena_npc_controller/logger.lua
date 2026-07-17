local Logger = {}
Logger.__index = Logger

local function clean(value)
  local text = tostring(value == nil and "nil" or value):gsub("[\r\n\t]+", " "):gsub("%s+", " ")
  return #text > 240 and text:sub(1, 240) .. "..." or text
end

local function session_id()
  local ok, value = pcall(function() local player = Game.GetPlayer(); return player and player:GetEntityID() or "none" end)
  return ok and clean(value) or "unavailable"
end

function Logger.new() return setmetatable({}, Logger) end
function Logger:trace(event, message)
  local line = string.format("%s [SienaNpcController] session=%s event=%s %s", os.date("!%Y-%m-%dT%H:%M:%SZ"), session_id(), clean(event), clean(message))
  if spdlog and spdlog.info then spdlog.info(line) else print(line) end
end

return Logger
