local TelemetryClient = {}
TelemetryClient.__index = TelemetryClient

local backoff_steps = { 500, 1000, 2000, 5000, 10000 }

local function endpoint(base, path)
  return base:gsub("/$", "") .. path
end

local function encode(payload)
  local ok, value = pcall(function() return json.encode(payload) end)
  if not ok then return nil, tostring(value) end
  return value, nil
end

function TelemetryClient.new(config, diagnostics)
  return setmetatable({
    config = config,
    diagnostics = diagnostics,
    available = false,
    listener = nil,
    callback = nil,
    in_flight = nil,
    pending_state = nil,
    hello_payload = nil,
    hello_complete = false,
    connected = false,
    shutting_down = false,
    next_attempt_at = 0,
    backoff_index = 1,
    last_heartbeat_at = 0,
    last_http_status = 0,
    last_error = nil,
    dropped_stale_states = 0,
    accepted_samples = {}
  }, TelemetryClient)
end

function TelemetryClient:start(now_ms)
  if AsyncHttpClient == nil or HttpCallback == nil or HttpHeader == nil or NewProxy == nil then
    self.last_error = "RedHttpClient AsyncHttpClient is unavailable"
    self.diagnostics:log("http_unavailable", self.last_error, now_ms, true)
    return false
  end
  if json == nil or type(json.encode) ~= "function" then
    self.last_error = "CET JSON encoder is unavailable"
    self.diagnostics:log("json_unavailable", self.last_error, now_ms, true)
    return false
  end
  local owner = self
  self.listener = NewProxy({
    OnResponse = {
      args = { "handle:HttpResponse" },
      callback = function(response) owner:_on_response(response) end
    }
  })
  self.callback = HttpCallback.Create(self.listener:Target(), self.listener:Function("OnResponse"))
  self.available = true
  return true
end

function TelemetryClient:set_hello_payload(payload)
  self.hello_payload = payload
end

function TelemetryClient:refresh_hello()
  self.hello_complete = false
end

function TelemetryClient:queue_state(state)
  if self.pending_state ~= nil then
    self.dropped_stale_states = self.dropped_stale_states + 1
  end
  self.pending_state = state
end

function TelemetryClient:drop_pending_state()
  self.pending_state = nil
end

function TelemetryClient:update(now_ms, heartbeat_payload)
  self.now_ms = now_ms
  if not self.available or self.shutting_down then return end
  if self.in_flight ~= nil then
    if not self.in_flight.timeout_reported and now_ms - self.in_flight.started_at >= self.config.request_timeout_ms then
      self.in_flight.timeout_reported = true
      self.connected = false
      self.last_error = "request timeout"
      self.diagnostics:log("request_timeout", "request timeout", now_ms, true)
      self.diagnostics:log("backend_disconnected", "backend disconnected", now_ms, true)
    end
    return
  end
  if now_ms < self.next_attempt_at then return end

  if not self.hello_complete then
    if self.hello_payload ~= nil then self:_dispatch("hello", "/api/v1/bridge/hello", self.hello_payload, now_ms, nil) end
    return
  end
  if now_ms - self.last_heartbeat_at >= self.config.heartbeat_interval_ms then
    self:_dispatch("heartbeat", "/api/v1/bridge/heartbeat", heartbeat_payload, now_ms, nil)
    return
  end
  if self.pending_state ~= nil then
    local state = self.pending_state
    self.pending_state = nil
    self:_dispatch("telemetry", "/api/v1/telemetry/state", state, now_ms, state)
  end
end

function TelemetryClient:_dispatch(kind, path, payload, now_ms, state)
  local body, error_message = encode(payload)
  if body == nil then
    self.last_error = "JSON encode failed: " .. error_message
    self.diagnostics:log("json_error", self.last_error, now_ms, true)
    return
  end
  local headers = { HttpHeader.Create("Content-Type", "application/json") }
  self.in_flight = { kind = kind, state = state, started_at = now_ms, timeout_reported = false }
  local ok, error_value = pcall(function()
    AsyncHttpClient.Post(self.callback, endpoint(self.config.backend_url, path), body, headers)
  end)
  if not ok then
    self.in_flight = nil
    self:_network_failure(kind, "request dispatch failed: " .. tostring(error_value), now_ms, state)
  end
end

function TelemetryClient:_on_response(response)
  if self.shutting_down then return end
  local now_ms = self.now_ms or 0
  local request = self.in_flight
  self.in_flight = nil
  if request == nil then return end

  local status = 0
  if response ~= nil then
    local ok, value = pcall(function() return response:GetStatusCode() end)
    if ok and type(value) == "number" then status = value end
  end
  self.last_http_status = status
  local expected = (request.kind == "telemetry") and 202 or 200
  if status == expected then
    local was_disconnected = not self.connected
    self.connected = true
    self.last_error = nil
    self.backoff_index = 1
    self.next_attempt_at = now_ms
    if request.kind == "hello" then
      self.hello_complete = true
      self.last_heartbeat_at = now_ms
      self.diagnostics:log("hello_success", "bridge hello success", now_ms, true)
    elseif request.kind == "heartbeat" then
      self.last_heartbeat_at = now_ms
    elseif request.kind == "telemetry" then
      table.insert(self.accepted_samples, now_ms)
      self:_trim_samples(now_ms)
      self.diagnostics:log("telemetry_accepted", "telemetry accepted", now_ms, false)
    end
    if was_disconnected then self.diagnostics:log("backend_reconnected", "backend reconnected", now_ms, true) end
    return
  end

  if request.kind == "telemetry" and status >= 400 and status < 500 then
    self.connected = true
    self.last_error = "telemetry rejected with HTTP " .. tostring(status)
    self.diagnostics:log("telemetry_rejected", self.last_error, now_ms, true)
    return
  end
  self:_network_failure(request.kind, request.kind .. " failed with HTTP " .. tostring(status), now_ms, request.state)
end

function TelemetryClient:_network_failure(kind, message, now_ms, failed_state)
  if failed_state ~= nil and self.pending_state == nil then self.pending_state = failed_state end
  self.connected = false
  self.hello_complete = false
  self.last_error = message
  local delay = math.min(backoff_steps[self.backoff_index] or self.config.max_backoff_ms, self.config.max_backoff_ms)
  self.backoff_index = math.min(self.backoff_index + 1, #backoff_steps)
  self.next_attempt_at = now_ms + delay
  self.diagnostics:log(kind .. "_failed", message, now_ms, false)
  self.diagnostics:log("backend_disconnected", "backend disconnected", now_ms, false)
end

function TelemetryClient:_trim_samples(now_ms)
  while #self.accepted_samples > 0 and now_ms - self.accepted_samples[1] > 5000 do
    table.remove(self.accepted_samples, 1)
  end
end

function TelemetryClient:telemetry_rate(now_ms)
  self:_trim_samples(now_ms)
  if #self.accepted_samples < 2 then return 0 end
  local elapsed = (self.accepted_samples[#self.accepted_samples] - self.accepted_samples[1]) / 1000
  return elapsed > 0 and (#self.accepted_samples - 1) / elapsed or 0
end

function TelemetryClient:shutdown()
  self.shutting_down = true
  self.pending_state = nil
  self.in_flight = nil
  self.callback = nil
  self.listener = nil
  self.available = false
end

return TelemetryClient
