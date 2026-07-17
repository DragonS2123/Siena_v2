local Logger = {}
Logger.__index = Logger

local function clean(value, limit)
  local text = tostring(value == nil and "nil" or value):gsub("[\r\n\t]+", " "):gsub("%s+", " ")
  if #text > limit then text = text:sub(1, limit) .. "..." end
  return text
end

function Logger.new(config)
  return setmetatable({ config = config }, Logger)
end

local function session_id()
  local ok, value = pcall(function()
    local player = Game.GetPlayer()
    if player == nil then return "none" end
    return tostring(player:GetEntityID())
  end)
  return ok and clean(value, 96) or "unavailable"
end

function Logger:write(level, gate, command, message)
  local timestamp = os.date("!%Y-%m-%dT%H:%M:%SZ")
  local line = string.format("%s %s session=%s level=%s gate=%s command=%s %s", timestamp, self.config.log_prefix, session_id(), level, gate, command, clean(message, self.config.max_log_chars))
  local file = io.open(self.config.log_file, "a")
  if file then file:write(line .. "\n"); file:close() end
  if spdlog and spdlog.info then
    if level == "error" and spdlog.error then spdlog.error(line) else spdlog.info(line) end
  else
    print(line)
  end
end

function Logger:info(gate, command, message) self:write("info", gate, command, message) end
function Logger:ok(gate, command, message) self:write("success", gate, command, message) end
function Logger:unavailable(gate, command, message) self:write("unavailable", gate, command, message) end
function Logger:error(gate, command, message) self:write("error", gate, command, message) end

return Logger
