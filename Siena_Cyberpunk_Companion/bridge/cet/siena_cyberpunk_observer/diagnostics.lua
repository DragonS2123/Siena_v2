local Diagnostics = {}
Diagnostics.__index = Diagnostics

function Diagnostics.new(enabled)
  return setmetatable({ enabled = enabled, last = {}, repeat_window_ms = 5000 }, Diagnostics)
end

function Diagnostics:log(key, message, now_ms, force)
  if not self.enabled then return end
  local previous = self.last[key]
  if force or previous == nil or now_ms - previous >= self.repeat_window_ms then
    self.last[key] = now_ms
    print("[Siena Observer] " .. message)
  end
end

function Diagnostics:capability(name, available, now_ms)
  self:log("capability:" .. name, "capability " .. (available and "detected: " or "unavailable: ") .. name, now_ms, true)
end

return Diagnostics
