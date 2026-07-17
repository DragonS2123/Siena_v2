local PresenceOverlay = {}
PresenceOverlay.__index = PresenceOverlay

local positions = { "top_left", "top_center", "top_right", "bottom_left", "bottom_center", "bottom_right" }

local function clamp(value, minimum, maximum)
  return math.max(minimum, math.min(maximum, value))
end

local function add_flag(flags, name)
  if ImGuiWindowFlags ~= nil and ImGuiWindowFlags[name] ~= nil then return flags + ImGuiWindowFlags[name] end
  return flags
end

local function safe_wrapped_text(value)
  local safe = tostring(value or ""):gsub("%%", "%%%%")
  ImGui.TextWrapped(safe)
end

local function window_flags()
  local flags = 0
  for _, name in ipairs({
    "NoTitleBar", "NoResize", "NoMove", "NoScrollbar", "NoScrollWithMouse",
    "NoSavedSettings", "NoFocusOnAppearing", "NoBringToFrontOnFocus",
    "AlwaysAutoResize"
  }) do
    flags = add_flag(flags, name)
  end
  if ImGuiWindowFlags ~= nil and ImGuiWindowFlags.NoInputs ~= nil then
    flags = add_flag(flags, "NoInputs")
  else
    flags = add_flag(flags, "NoMouseInputs")
    flags = add_flag(flags, "NoNavInputs")
    flags = add_flag(flags, "NoNavFocus")
  end
  return flags
end

function PresenceOverlay.new(config, config_module, diagnostics)
  return setmetatable({
    config = config,
    config_module = config_module,
    diagnostics = diagnostics,
    preview = false,
    last_height = 150,
    draw_count = 0,
    last_save_error = nil
  }, PresenceOverlay)
end

function PresenceOverlay:_layout(screen_width, screen_height)
  local dpi = clamp(screen_height / 1080, 0.75, 2.0)
  local scale = dpi * self.config.font_scale
  local margin_x = self.config.margin_x * dpi
  local margin_y = self.config.margin_y * dpi
  local width = math.min(self.config.max_width * dpi, math.max(240, screen_width - margin_x * 2))
  local height = self.last_height
  local x = margin_x
  local y = margin_y
  if self.config.position:find("center", 1, true) then x = (screen_width - width) / 2 end
  if self.config.position:find("right", 1, true) then x = screen_width - width - margin_x end
  if self.config.position:find("bottom", 1, true) then y = screen_height - height - margin_y end
  return x, y, width, scale
end

function PresenceOverlay:_alpha(now_ms, received_at, expires_at, state)
  local fade_in = math.max(1, self.config.fade_in_ms)
  local fade_out = math.max(1, self.config.fade_out_ms)
  local alpha = clamp((now_ms - received_at) / fade_in, 0, 1)
  if expires_at > 0 then alpha = math.min(alpha, clamp((expires_at - now_ms) / fade_out, 0, 1)) end
  if state == "generating" then alpha = alpha * (0.94 + 0.06 * (0.5 + 0.5 * math.sin(now_ms / 650))) end
  return alpha
end

function PresenceOverlay:draw(now_ms, snapshot, received_at, expires_at, connected)
  if ImGui == nil or GetDisplayResolution == nil then return end
  local value = snapshot
  received_at = received_at or (now_ms - self.config.fade_in_ms)
  if self.preview then
    value = {
      active = true,
      state = "reaction",
      text = "Здоровье критическое. Найди укрытие.\nШанс выжить — 20%.",
      fallback_used = false,
      provider = "preview",
      metadata = {}
    }
    expires_at = 0
    received_at = now_ms - self.config.fade_in_ms
  elseif not self.config.enabled or value == nil or not value.active then
    if not connected and self.config.enabled and self.config.show_disconnect_indicator then
      value = { active = true, state = "unavailable", text = "Сиена временно недоступна", metadata = {} }
      expires_at = 0
      received_at = now_ms - self.config.fade_in_ms
    else
      return
    end
  end
  local screen_width, screen_height = GetDisplayResolution()
  if type(screen_width) ~= "number" or type(screen_height) ~= "number" then return end
  local x, y, width, scale = self:_layout(screen_width, screen_height)
  local alpha = self:_alpha(now_ms, received_at, expires_at or 0, value.state)
  if alpha <= 0 then return end
  ImGui.SetNextWindowPos(x, y, ImGuiCond.Always)
  ImGui.SetNextWindowSizeConstraints(width, 0, width, screen_height)
  ImGui.SetNextWindowBgAlpha(self.config.background_alpha * alpha)
  local pushed_alpha = ImGuiStyleVar ~= nil and ImGuiStyleVar.Alpha ~= nil and ImGui.PushStyleVar ~= nil and ImGui.PopStyleVar ~= nil
  if pushed_alpha then ImGui.PushStyleVar(ImGuiStyleVar.Alpha, alpha) end
  local visible = ImGui.Begin("##SienaInGamePresence", window_flags())
  if visible then
    ImGui.SetWindowFontScale(scale)
    safe_wrapped_text(value.text or "")
    local metadata = value.metadata or {}
    if value.fallback_used and self.config.show_fallback_label then safe_wrapped_text(metadata.fallback_label or "Резервная реакция") end
    if self.config.show_voice_status and metadata.voice_label ~= nil then safe_wrapped_text(metadata.voice_label) end
    if self.config.show_provider and value.provider ~= nil then safe_wrapped_text(value.provider) end
    local _, height = ImGui.GetWindowSize()
    if type(height) == "number" and height > 0 then self.last_height = height end
    ImGui.SetWindowFontScale(1)
  end
  ImGui.End()
  if pushed_alpha then ImGui.PopStyleVar() end
  self.draw_count = self.draw_count + 1
end

function PresenceOverlay:draw_debug_controls()
  local changed = false
  self.preview, changed = ImGui.Checkbox("Presence preview", self.preview)
  ImGui.Text("Anchor: " .. self.config.position)
  for _, position in ipairs(positions) do
    if ImGui.Button(position) then self.config.position = position end
    ImGui.SameLine()
  end
  ImGui.NewLine()
  self.config.margin_x, changed = ImGui.SliderFloat("Presence margin X", self.config.margin_x, 0, 300)
  self.config.margin_y, changed = ImGui.SliderFloat("Presence margin Y", self.config.margin_y, 0, 300)
  self.config.max_width, changed = ImGui.SliderFloat("Presence max width", self.config.max_width, 240, 1200)
  self.config.background_alpha, changed = ImGui.SliderFloat("Presence opacity", self.config.background_alpha, 0.1, 1.0)
  self.config.font_scale, changed = ImGui.SliderFloat("Presence font scale", self.config.font_scale, 0.6, 2.5)
  safe_wrapped_text("Проверка кириллицы: Здоровье 8%. Осторожно — 20%.")
  if not self.config.font_cyrillic_ready then
    ImGui.Text("Cyrillic atlas is not confirmed; configure CET font.path if glyphs are missing.")
  end
  if ImGui.Button("Save Siena presence settings") then
    local ok, message = self.config_module.save(self.config)
    self.last_save_error = ok and nil or message
  end
  if self.last_save_error ~= nil then ImGui.Text("Save failed: " .. self.last_save_error) end
end

function PresenceOverlay:input_passthrough_ready()
  return ImGuiWindowFlags ~= nil and (
    ImGuiWindowFlags.NoInputs ~= nil or
    (ImGuiWindowFlags.NoMouseInputs ~= nil and ImGuiWindowFlags.NoNavInputs ~= nil and ImGuiWindowFlags.NoNavFocus ~= nil)
  )
end

return PresenceOverlay
