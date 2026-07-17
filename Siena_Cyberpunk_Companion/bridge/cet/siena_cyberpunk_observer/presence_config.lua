local PresenceConfig = {}

local defaults = {
  enabled = false,
  endpoint_path = "/api/v1/in-game-presence/current",
  allow_remote_presence_url = false,
  poll_interval_ms = 500,
  http_timeout_ms = 1000,
  error_backoff_ms = 2000,
  max_backoff_ms = 30000,
  position = "top_right",
  margin_x = 32,
  margin_y = 48,
  max_width = 520,
  font_scale = 1.0,
  font_path = "",
  font_size = 20,
  font_cyrillic_ready = false,
  background_alpha = 0.72,
  show_voice_status = true,
  show_fallback_label = true,
  show_provider = false,
  show_disconnect_indicator = false,
  fade_in_ms = 180,
  fade_out_ms = 450,
  max_text_chars = 320
}

local positions = {
  top_left = true, top_center = true, top_right = true,
  bottom_left = true, bottom_center = true, bottom_right = true
}

local function copy_defaults()
  local result = {}
  for key, value in pairs(defaults) do result[key] = value end
  return result
end

local function clamp(value, minimum, maximum, fallback)
  if type(value) ~= "number" then return fallback end
  return math.max(minimum, math.min(maximum, value))
end

function PresenceConfig.is_loopback_url(url)
  if type(url) ~= "string" then return false end
  return url:match("^http://127%.0%.0%.1[:/]") ~= nil
    or url:match("^http://localhost[:/]") ~= nil
end

function PresenceConfig.validate(settings, backend_url)
  if not settings.allow_remote_presence_url and not PresenceConfig.is_loopback_url(backend_url) then
    return false, "presence URL must use http://127.0.0.1 or http://localhost"
  end
  if not positions[settings.position] then return false, "invalid presence position" end
  if settings.poll_interval_ms < 100 then return false, "presence poll interval is too low" end
  if settings.http_timeout_ms < 250 then return false, "presence timeout is too low" end
  return true, nil
end

function PresenceConfig.read()
  local settings = copy_defaults()
  if io == nil or json == nil or type(json.decode) ~= "function" then return settings end
  local handle = io.open("presence_settings.json", "r")
  if handle == nil then return settings end
  local body = handle:read("*a")
  handle:close()
  local ok, saved = pcall(function() return json.decode(body) end)
  if ok and type(saved) == "table" then
    for key, default in pairs(defaults) do
      if type(saved[key]) == type(default) then settings[key] = saved[key] end
    end
  end
  settings.poll_interval_ms = clamp(settings.poll_interval_ms, 100, 60000, defaults.poll_interval_ms)
  settings.http_timeout_ms = clamp(settings.http_timeout_ms, 250, 30000, defaults.http_timeout_ms)
  settings.error_backoff_ms = clamp(settings.error_backoff_ms, 250, 60000, defaults.error_backoff_ms)
  settings.max_backoff_ms = clamp(settings.max_backoff_ms, 1000, 300000, defaults.max_backoff_ms)
  settings.margin_x = clamp(settings.margin_x, 0, 1000, defaults.margin_x)
  settings.margin_y = clamp(settings.margin_y, 0, 1000, defaults.margin_y)
  settings.max_width = clamp(settings.max_width, 240, 1200, defaults.max_width)
  settings.font_scale = clamp(settings.font_scale, 0.6, 2.5, defaults.font_scale)
  settings.background_alpha = clamp(settings.background_alpha, 0.1, 1.0, defaults.background_alpha)
  if not positions[settings.position] then settings.position = defaults.position end
  return settings
end

function PresenceConfig.save(settings)
  if io == nil or json == nil or type(json.encode) ~= "function" then
    return false, "CET JSON/file API is unavailable"
  end
  local ok, body = pcall(function() return json.encode(settings) end)
  if not ok then return false, "presence settings encode failed" end
  local handle = io.open("presence_settings.json", "w")
  if handle == nil then return false, "presence settings file is not writable" end
  handle:write(body)
  handle:close()
  return true, nil
end

return PresenceConfig
