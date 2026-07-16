local function load_module(name)
  return dofile(name .. ".lua")
end

local Config = load_module("config")
local Diagnostics = load_module("diagnostics")
local StateReader = load_module("state_reader")
local StateBuilder = load_module("state_builder")
local SessionManager = load_module("session_manager")
local CapabilityDetector = load_module("capability_detector")
local TelemetryClient = load_module("telemetry_client")
local PresenceConfig = load_module("presence_config")
local PresenceClient = load_module("presence_client")
local PresenceOverlay = load_module("presence_overlay")

local app = {
  elapsed_ms = 0,
  sample_accumulator_ms = 0,
  initialized = false,
  last_state = nil,
  config = Config
}

function app:_hello_payload()
  local cet_version = "unknown"
  if GetVersion ~= nil then
    local ok, value = pcall(GetVersion)
    if ok and value ~= nil then cet_version = tostring(value) end
  end
  local capabilities = {}
  for name, available in pairs(self.capability_detector:get()) do capabilities[name] = available end
  if self.presence_settings ~= nil then
    capabilities.presence_overlay_supported = true
    capabilities.presence_overlay_enabled = self.presence_settings.enabled
    capabilities.presence_overlay_version = "0.7.0"
    capabilities.presence_font_cyrillic_ready = self.presence_settings.font_cyrillic_ready
    capabilities.presence_poll_interval_ms = self.presence_settings.poll_interval_ms
  end
  return {
    bridge_id = Config.bridge_id,
    bridge_version = Config.bridge_version,
    protocol_version = Config.protocol_version,
    transport = "red_http_client",
    game_version = "unknown-live-check",
    cet_version = cet_version,
    capabilities = capabilities
  }
end

function app:_heartbeat_payload()
  return {
    bridge_id = Config.bridge_id,
    session_id = self.session_manager.session_id,
    sequence = self.session_manager.sequence,
    dropped_stale_states = self.transport.dropped_stale_states,
    telemetry_rate = self.transport:telemetry_rate(self.elapsed_ms)
  }
end

function app:_sample(delta_ms)
  local raw = self.state_reader:read(self.elapsed_ms)
  local _, capabilities_changed = self.capability_detector:update(raw.capabilities, self.elapsed_ms)
  if capabilities_changed then
    self.transport:set_hello_payload(self:_hello_payload())
    self.transport:refresh_hello()
  end
  self.session_manager:update(raw.loaded, delta_ms, self.elapsed_ms)
  if self.session_manager.session_id == nil then return end
  local sequence = self.session_manager:next_sequence()
  local state = self.state_builder:build(raw, self.session_manager.session_id, sequence)
  self.last_state = state
  self.transport:queue_state(state)
end

function app:ResetSession()
  if not self.initialized then return end
  self.transport:drop_pending_state()
  self.session_manager:reset(self.elapsed_ms)
end

function app:_draw_overlay()
  if not Config.enable_debug_overlay or ImGui == nil then return end
  local visible = ImGui.Begin("Siena Cyberpunk Observer v0.2")
  if visible then
    ImGui.Text("Backend: " .. (self.transport.connected and "Connected" or "Disconnected"))
    ImGui.Text("Game loaded: " .. (self.last_state and self.last_state.game.loaded and "Yes" or "No"))
    ImGui.Text("Session ID: " .. tostring(self.session_manager.session_id or "-"))
    ImGui.Text("Sequence: " .. tostring(self.session_manager.sequence))
    ImGui.Text("Last HTTP status: " .. tostring(self.transport.last_http_status))
    ImGui.Text("Requests in flight: " .. (self.transport.in_flight and "1" or "0"))
    ImGui.Text("Dropped stale states: " .. tostring(self.transport.dropped_stale_states))
    ImGui.Text(string.format("Telemetry rate: %.2f/s", self.transport:telemetry_rate(self.elapsed_ms)))
    if self.last_state then
      ImGui.Text(string.format("Player health: %.1f / %.1f", self.last_state.player.health, self.last_state.player.max_health))
      local p = self.last_state.player.position
      ImGui.Text(string.format("Position: %.2f, %.2f, %.2f", p.x, p.y, p.z))
    end
    ImGui.Separator()
    for name, available in pairs(self.capability_detector:get()) do
      ImGui.Text(name .. ": " .. (available and "Available" or "Unavailable"))
    end
    if self.presence_client ~= nil and self.presence_overlay ~= nil then
      local metrics = self.presence_client:metrics()
      ImGui.Separator()
      ImGui.Text("In-Game Presence v0.7")
      ImGui.Text("Enabled: " .. (metrics.enabled and "Yes" or "No"))
      ImGui.Text("Connected: " .. (metrics.connected and "Yes" or "No"))
      ImGui.Text("Request in flight: " .. (metrics.in_flight and "Yes" or "No"))
      ImGui.Text("Polls / unchanged: " .. tostring(metrics.poll_count) .. " / " .. tostring(metrics.unchanged_count))
      ImGui.Text("Parse failures: " .. tostring(metrics.parse_failures))
      ImGui.Text("Revision: " .. tostring(metrics.current_revision))
      ImGui.Text("Backoff: " .. tostring(metrics.current_backoff_ms) .. " ms")
      ImGui.Text("Draw count: " .. tostring(self.presence_overlay.draw_count))
      ImGui.Text("Input passthrough flag: " .. (self.presence_overlay:input_passthrough_ready() and "Available" or "Unavailable"))
      if metrics.last_error ~= nil then ImGui.Text("Presence error: " .. metrics.last_error) end
      self.presence_overlay:draw_debug_controls()
    end
  end
  ImGui.End()
end

registerForEvent("onInit", function()
  local valid, config_error = Config.validate()
  app.diagnostics = Diagnostics.new(Config.enable_diagnostics)
  if not valid then
    app.diagnostics:log("invalid_config", "mod disabled: " .. config_error, 0, true)
    return
  end
  app.state_reader = StateReader.new(app.diagnostics, Config)
  app.state_builder = StateBuilder.new(Config.bridge_version)
  app.session_manager = SessionManager.new(Config.world_disconnect_reset_ms, app.diagnostics)
  app.capability_detector = CapabilityDetector.new(app.diagnostics)
  app.transport = TelemetryClient.new(Config, app.diagnostics)
  app.presence_settings = PresenceConfig.read()
  local presence_valid, presence_error = PresenceConfig.validate(app.presence_settings, Config.backend_url)
  if not presence_valid then
    app.diagnostics:log("presence_config", presence_error, 0, true)
    app.presence_settings.enabled = false
  end
  app.presence_client = PresenceClient.new(app.presence_settings, Config.backend_url, app.diagnostics)
  app.presence_client:set_config_module(PresenceConfig)
  app.presence_client:start(0)
  app.presence_overlay = PresenceOverlay.new(app.presence_settings, PresenceConfig, app.diagnostics)
  app.transport:set_hello_payload(app:_hello_payload())
  app.transport:start(0)
  app.initialized = true
  app.diagnostics:log("initialized", "mod initialized", 0, true)
end)

registerForEvent("onUpdate", function(delta_time)
  if not app.initialized then return end
  local delta_ms = math.max(0, (delta_time or 0) * 1000)
  app.elapsed_ms = app.elapsed_ms + delta_ms
  app.sample_accumulator_ms = app.sample_accumulator_ms + delta_ms
  if app.sample_accumulator_ms >= Config.telemetry_interval_ms then
    app.sample_accumulator_ms = app.sample_accumulator_ms % Config.telemetry_interval_ms
    app:_sample(Config.telemetry_interval_ms)
  end
  app.transport:update(app.elapsed_ms, app:_heartbeat_payload())
  app.presence_client:update(app.elapsed_ms, app.session_manager.session_id)
end)

local overlay_open = false

registerForEvent("onOverlayOpen", function()
  overlay_open = true
end)

registerForEvent("onOverlayClose", function()
  overlay_open = false
end)

registerForEvent("onDraw", function()
  if app.initialized then
    app.presence_overlay:draw(
      app.elapsed_ms,
      app.presence_client.snapshot,
      app.presence_client.snapshot_received_at,
      app.presence_client.snapshot_expires_at,
      app.presence_client.connected
    )
    if overlay_open then app:_draw_overlay() end
  end
end)

registerForEvent("onShutdown", function()
  if app.transport then app.transport:shutdown() end
  if app.presence_client then app.presence_client:shutdown() end
  app.last_state = nil
  app.state_reader = nil
  app.initialized = false
end)

if registerHotkey ~= nil then
  registerHotkey("siena_observer_reset_session", "Siena Observer: reset session", function() app:ResetSession() end)
  registerHotkey("siena_presence_toggle", "Toggle Siena Presence", function()
    if not app.initialized or app.presence_settings == nil then return end
    app.presence_settings.enabled = not app.presence_settings.enabled
    app.presence_client:set_enabled(app.presence_settings.enabled, app.elapsed_ms)
    PresenceConfig.save(app.presence_settings)
    app.transport:set_hello_payload(app:_hello_payload())
    app.transport:refresh_hello()
  end)
end

return app
