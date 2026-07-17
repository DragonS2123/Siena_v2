local SafeCall = {}
SafeCall.__index = SafeCall
function SafeCall.new(logger) return setmetatable({ logger = logger }, SafeCall) end
function SafeCall:call(label, fn)
  local ok, value = pcall(fn)
  if not ok then self.logger:trace("npc_runtime_error", "call=" .. label .. " error=" .. tostring(value)); return false, nil end
  return true, value
end
return SafeCall
