local PresenceClient = {}
PresenceClient.__index = PresenceClient

local function endpoint(base, path, revision)
  local separator = path:find("?", 1, true) and "&" or "?"
  return base:gsub("/$", "") .. path .. separator .. "after_revision=" .. tostring(revision)
end

local function utf8_prefix(value, max_chars)
  local count = 0
  local index = 1
  local last = 0
  while index <= #value and count < max_chars do
    local byte = value:byte(index)
    local length = 1
    if byte >= 240 then length = 4 elseif byte >= 224 then length = 3 elseif byte >= 192 then length = 2 end
    if index + length - 1 > #value then break end
    last = index + length - 1
    index = index + length
    count = count + 1
  end
  return value:sub(1, last)
end

local function sanitize_text(value, max_chars)
  if type(value) ~= "string" then return "" end
  local bytes = {}
  local newlines = 0
  for index = 1, #value do
    local byte = value:byte(index)
    if byte == 10 then
      if newlines < 2 then table.insert(bytes, "\n") end
      newlines = newlines + 1
    elseif byte == 9 or byte >= 32 then
      if byte ~= 127 then table.insert(bytes, string.char(byte)) end
    end
  end
  local result = table.concat(bytes):gsub("[ \t]+", " "):gsub("^%s+", ""):gsub("%s+$", "")
  local lower = result:lower()
  if lower:match("^%s*[%{%[]") or lower:find("<html", 1, true) then return "" end
  return utf8_prefix(result, max_chars)
end

local function valid_snapshot(value)
  return type(value) == "table"
    and type(value.revision) == "number"
    and type(value.active) == "boolean"
    and type(value.state) == "string"
    and (value.session_id == nil or type(value.session_id) == "string")
end

function PresenceClient.new(config, backend_url, diagnostics)
  return setmetatable({
    config = config,
    backend_url = backend_url,
    diagnostics = diagnostics,
    available = false,
    enabled = config.enabled,
    in_flight = nil,
    request_token = 0,
    next_poll_at = 0,
    current_backoff_ms = config.error_backoff_ms,
    last_revision = 0,
    snapshot = nil,
    snapshot_received_at = 0,
    snapshot_expires_at = 0,
    current_session = nil,
    last_session = nil,
    connected = false,
    last_error = nil,
    last_http_status = 0,
    last_latency_ms = nil,
    poll_count = 0,
    unchanged_count = 0,
    parse_failures = 0,
    now_ms = 0
  }, PresenceClient)
end

function PresenceClient:start(now_ms)
  self.now_ms = now_ms
  local valid, message = self.config_module.validate(self.config, self.backend_url)
  if not valid then
    self.last_error = message
    self.diagnostics:log("presence_config", message, now_ms, true)
    return false
  end
  if AsyncHttpClient == nil or HttpCallback == nil or NewProxy == nil then
    self.last_error = "RedHttpClient async GET is unavailable"
    self.diagnostics:log("presence_http_unavailable", self.last_error, now_ms, true)
    return false
  end
  if json == nil or type(json.decode) ~= "function" then
    self.last_error = "CET JSON decoder is unavailable"
    self.diagnostics:log("presence_json_unavailable", self.last_error, now_ms, true)
    return false
  end
  self.available = true
  self.next_poll_at = now_ms
  return true
end

function PresenceClient:set_config_module(value)
  self.config_module = value
end

function PresenceClient:set_enabled(enabled, now_ms)
  self.enabled = enabled == true
  self.config.enabled = self.enabled
  self.next_poll_at = now_ms
  if not self.enabled then
    self.in_flight = nil
    self.snapshot = nil
    self.snapshot_received_at = 0
    self.connected = false
  end
end

function PresenceClient:_session_changed(session_id)
  if session_id ~= nil and session_id ~= self.current_session then
    self.snapshot = nil
    self.snapshot_received_at = 0
    self.snapshot_expires_at = 0
    self.last_revision = 0
  end
  if self.current_session ~= nil then self.last_session = self.current_session end
  self.current_session = session_id
end

function PresenceClient:update(now_ms, session_id)
  self.now_ms = now_ms
  if session_id ~= self.current_session then self:_session_changed(session_id) end
  if self.snapshot ~= nil and now_ms >= self.snapshot_expires_at then self.snapshot = nil end
  if not self.available or not self.enabled then return end
  if self.in_flight ~= nil then
    if now_ms - self.in_flight.started_at >= self.config.http_timeout_ms then
      self.request_token = self.request_token + 1
      self.in_flight = nil
      self:_failure("presence request timeout", now_ms)
    end
    return
  end
  if now_ms < self.next_poll_at then return end
  self:_dispatch(now_ms)
end

function PresenceClient:_dispatch(now_ms)
  self.request_token = self.request_token + 1
  local token = self.request_token
  local owner = self
  local listener = NewProxy({
    OnResponse = {
      args = { "handle:HttpResponse" },
      callback = function(response) owner:_on_response(token, response) end
    }
  })
  local callback = HttpCallback.Create(listener:Target(), listener:Function("OnResponse"))
  self.in_flight = { token = token, started_at = now_ms, listener = listener, callback = callback }
  self.poll_count = self.poll_count + 1
  local url = endpoint(self.backend_url, self.config.endpoint_path, self.last_revision)
  local ok, message = pcall(function() AsyncHttpClient.Get(callback, url) end)
  if not ok then
    self.in_flight = nil
    self:_failure("presence GET dispatch failed: " .. tostring(message), now_ms)
  end
end

function PresenceClient:_on_response(token, response)
  local now_ms = self.now_ms or 0
  local request = self.in_flight
  if request == nil or request.token ~= token then return end
  self.in_flight = nil
  local status = 0
  if response ~= nil then
    local ok, value = pcall(function() return response:GetStatusCode() end)
    if ok and type(value) == "number" then status = value end
  end
  self.last_http_status = status
  self.last_latency_ms = math.max(0, now_ms - request.started_at)
  if status == 204 then
    self.unchanged_count = self.unchanged_count + 1
    self:_success(now_ms)
    return
  end
  if status ~= 200 or response == nil then
    self:_failure("presence request failed with HTTP " .. tostring(status), now_ms)
    return
  end
  local ok_text, body = pcall(function() return response:GetText() end)
  local ok_json, value = false, nil
  if ok_text and type(body) == "string" then
    ok_json, value = pcall(function() return json.decode(body) end)
  end
  if not ok_json or not valid_snapshot(value) then
    self.parse_failures = self.parse_failures + 1
    self:_failure("presence response validation failed", now_ms)
    return
  end
  if value.revision <= self.last_revision then
    self.unchanged_count = self.unchanged_count + 1
    self:_success(now_ms)
    return
  end
  local accepted_session = self.current_session or self.last_session
  if value.active and (value.session_id == nil or value.session_id ~= accepted_session) then
    self:_success(now_ms)
    return
  end
  value.text = sanitize_text(value.text, self.config.max_text_chars)
  if value.active and (value.state == "reaction" or value.state == "fallback") and value.text == "" then
    self.parse_failures = self.parse_failures + 1
    self:_failure("presence response has empty display text", now_ms)
    return
  end
  self.last_revision = value.revision
  if value.active and value.state ~= "hidden" then
    self.snapshot = value
    self.snapshot_received_at = now_ms
    local remaining = tonumber(value.display_duration_ms) or 0
    local ok_header, header = pcall(function() return response:GetHeader("X-Siena-Presence-Remaining-Ms") end)
    if ok_header and tonumber(header) ~= nil then remaining = tonumber(header) end
    self.snapshot_expires_at = now_ms + math.max(0, remaining)
  else
    self.snapshot = nil
    self.snapshot_expires_at = 0
  end
  self:_success(now_ms)
end

function PresenceClient:_success(now_ms)
  local reconnected = not self.connected
  self.connected = true
  self.last_error = nil
  self.current_backoff_ms = self.config.error_backoff_ms
  self.next_poll_at = now_ms + self.config.poll_interval_ms
  if reconnected then self.diagnostics:log("presence_reconnected", "presence backend connected", now_ms, true) end
end

function PresenceClient:_failure(message, now_ms)
  self.connected = false
  self.last_error = message
  self.next_poll_at = now_ms + self.current_backoff_ms
  self.current_backoff_ms = math.min(self.current_backoff_ms * 2, self.config.max_backoff_ms)
  self.diagnostics:log("presence_failed", message, now_ms, false)
end

function PresenceClient:metrics()
  return {
    enabled = self.enabled,
    connected = self.connected,
    in_flight = self.in_flight ~= nil,
    poll_count = self.poll_count,
    unchanged_count = self.unchanged_count,
    parse_failures = self.parse_failures,
    current_revision = self.last_revision,
    current_backoff_ms = self.current_backoff_ms,
    last_latency_ms = self.last_latency_ms,
    last_error = self.last_error
  }
end

function PresenceClient:shutdown()
  self.request_token = self.request_token + 1
  self.in_flight = nil
  self.snapshot = nil
  self.snapshot_received_at = 0
  self.available = false
  self.connected = false
end

return PresenceClient
