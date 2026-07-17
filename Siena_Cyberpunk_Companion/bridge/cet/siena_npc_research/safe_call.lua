local SafeCall = {}
SafeCall.__index = SafeCall

function SafeCall.new(config, logger)
  return setmetatable({ config = config, logger = logger }, SafeCall)
end

function SafeCall:call(gate, command, label, fn)
  local ok, value = pcall(fn)
  if not ok then
    self.logger:error(gate, command, label .. " failed=" .. tostring(value))
    return false, nil
  end
  return true, value
end

function SafeCall:manual(gate, command, fn)
  if not self.config.enabled then
    self.logger:unavailable(gate, command, "research_disabled; edit config.lua enabled=true and reload mods")
    return
  end
  local ok, err = pcall(fn)
  if not ok then self.logger:error(gate, command, "caught=" .. tostring(err)) end
end

return SafeCall
