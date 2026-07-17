local SafeProbe = {}
SafeProbe.__index = SafeProbe

local function type_name(value)
  if value == nil then return "nil" end
  local text = tostring(value)
  return text:match("^([^%[]+)%[") or type(value)
end

local function bounded_value(value, max_items)
  if type(value) ~= "table" then return tostring(value) end
  local count = 0
  for _ in pairs(value) do
    count = count + 1
    if count >= max_items then break end
  end
  return string.format("table(items<=%d)", count)
end

function SafeProbe.new(config, logger)
  return setmetatable({ config = config, logger = logger, last_run = {} }, SafeProbe)
end

function SafeProbe:call(probe, method, fn)
  local ok, value, second = pcall(fn)
  if not ok then
    self.logger:error(probe, method .. " failed: " .. tostring(value))
    return nil, false
  end
  self.logger:ok(probe, string.format("method=%s type=%s value=%s", method, type_name(value), bounded_value(value, self.config.max_items)))
  if second ~= nil then
    self.logger:ok(probe, string.format("method=%s second_type=%s second=%s", method, type_name(second), bounded_value(second, self.config.max_items)))
  end
  return value, true, second
end

function SafeProbe:run(name, fn)
  if not self.config.enabled then
    self.logger:unavailable(name, "research disabled; set config.enabled=true and reload CET mods")
    return
  end
  local now_ms = os.time() * 1000
  local previous = self.last_run[name] or 0
  if now_ms - previous < self.config.minimum_probe_interval_ms then
    self.logger:unavailable(name, "rate limited")
    return
  end
  self.last_run[name] = now_ms
  self.logger:info(name, "begin")
  local ok, message = pcall(fn)
  if not ok then self.logger:error(name, "probe recovered: " .. tostring(message)) end
  self.logger:info(name, "end")
end

function SafeProbe:type_name(value) return type_name(value) end

return SafeProbe
