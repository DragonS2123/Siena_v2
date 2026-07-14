local Config = {
  backend_url = "http://127.0.0.1:8765",
  telemetry_interval_ms = 250,
  heartbeat_interval_ms = 2000,
  request_timeout_ms = 5000,
  max_backoff_ms = 10000,
  world_disconnect_reset_ms = 10000,
  enable_debug_overlay = true,
  enable_diagnostics = true,
  protocol_version = "1.0",
  bridge_id = "siena-cyberpunk-cet",
  bridge_version = "0.2.0"
}

function Config.is_loopback_url(url)
  if type(url) ~= "string" then return false end
  return url:match("^http://127%.0%.0%.1[:/]") ~= nil
    or url:match("^http://localhost[:/]") ~= nil
end

function Config.validate()
  if not Config.is_loopback_url(Config.backend_url) then
    return false, "backend_url must use http://127.0.0.1 or http://localhost"
  end
  if Config.telemetry_interval_ms < 100 then return false, "telemetry_interval_ms is too low" end
  if Config.heartbeat_interval_ms < 500 then return false, "heartbeat_interval_ms is too low" end
  if Config.request_timeout_ms < 1000 then return false, "request_timeout_ms is too low" end
  if Config.max_backoff_ms > 10000 then return false, "max_backoff_ms must not exceed 10000" end
  return true, nil
end

return Config
