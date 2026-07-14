local SessionManager = {}
SessionManager.__index = SessionManager

local session_counter = 0

local function new_session_id()
  session_counter = session_counter + 1
  local timestamp = os.time and os.time() or 0
  return string.format("cet-%d-%04d", timestamp, session_counter)
end

function SessionManager.new(reset_after_ms, diagnostics)
  return setmetatable({
    reset_after_ms = reset_after_ms,
    diagnostics = diagnostics,
    session_id = nil,
    sequence = 0,
    unavailable_ms = 0,
    world_available = false
  }, SessionManager)
end

function SessionManager:update(available, delta_ms, now_ms)
  if available then
    self.unavailable_ms = 0
    if self.session_id == nil then
      self.session_id = new_session_id()
      self.sequence = 0
      self.diagnostics:log("session_started", "session started: " .. self.session_id, now_ms, true)
    end
    if not self.world_available then
      self.diagnostics:log("world_detected", "game world detected", now_ms, true)
    end
    self.world_available = true
    return
  end

  self.world_available = false
  if self.session_id ~= nil then
    self.unavailable_ms = self.unavailable_ms + delta_ms
    if self.unavailable_ms >= self.reset_after_ms then
      self.session_id = nil
      self.sequence = 0
      self.unavailable_ms = 0
    end
  end
end

function SessionManager:next_sequence()
  if self.session_id == nil then return nil end
  self.sequence = self.sequence + 1
  return self.sequence
end

function SessionManager:reset(now_ms)
  self.session_id = nil
  self.sequence = 0
  self.unavailable_ms = self.reset_after_ms
  self.world_available = false
  self.diagnostics:log("manual_reset", "session reset requested", now_ms, true)
end

return SessionManager
