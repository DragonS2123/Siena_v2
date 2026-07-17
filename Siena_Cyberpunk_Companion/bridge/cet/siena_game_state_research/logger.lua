local Logger = {}
Logger.__index = Logger

local function clean(value, limit)
  local text = tostring(value == nil and "nil" or value)
    :gsub("[\r\n\t]+", " ")
    :gsub("%s+", " ")
    :gsub("%a:[/\\][^%s]+", "<path>")
    :gsub("[Bb]earer%s+[%w%._%-]+", "Bearer <redacted>")
    :gsub("([Tt]oken[=:])[^%s]+", "%1<redacted>")
  if #text > limit then text = text:sub(1, limit) .. "..." end
  return text
end

function Logger.new(config)
  return setmetatable({ config = config }, Logger)
end

function Logger:write(level, probe, message)
  local line = string.format("%s [%s] [%s] %s", self.config.log_prefix, level, probe, clean(message, self.config.max_log_value_chars))
  if spdlog ~= nil and spdlog.info ~= nil then
    if level == "error" and spdlog.error ~= nil then spdlog.error(line) else spdlog.info(line) end
  else
    print(line)
  end
end

function Logger:info(probe, message) self:write("info", probe, message) end
function Logger:ok(probe, message) self:write("ok", probe, message) end
function Logger:unavailable(probe, message) self:write("unavailable", probe, message) end
function Logger:error(probe, message) self:write("error", probe, message) end

return Logger
