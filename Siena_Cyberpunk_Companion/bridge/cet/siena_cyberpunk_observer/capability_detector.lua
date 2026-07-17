local CapabilityDetector = {}
CapabilityDetector.__index = CapabilityDetector

local names = {
  "player_health", "player_position", "combat_state", "vehicle_state", "pause_state", "district",
  "deep_player", "deep_stats", "deep_stat_pools", "deep_weapon", "deep_status_effects",
  "cyberdeck_identity", "cyberdeck_metadata", "cyberdeck_programs", "cyberdeck_capacity"
}

function CapabilityDetector.new(diagnostics)
  return setmetatable({
    diagnostics = diagnostics,
    capabilities = {
      player_health = false, player_position = false, combat_state = false,
      vehicle_state = false, pause_state = false, district = false,
      deep_player = false, deep_stats = false, deep_stat_pools = false,
      deep_weapon = false, deep_status_effects = false,
      cyberdeck_identity = false, cyberdeck_metadata = false,
      cyberdeck_programs = false, cyberdeck_capacity = false
    },
    announced = {}
  }, CapabilityDetector)
end

function CapabilityDetector:update(detected, now_ms)
  local changed = false
  for _, name in ipairs(names) do
    if detected[name] and not self.capabilities[name] then
      self.capabilities[name] = true
      changed = true
    end
    if not self.announced[name] and (detected[name] or now_ms >= 1000) then
      self.announced[name] = true
      self.diagnostics:capability(name, self.capabilities[name], now_ms)
    end
  end
  return self.capabilities, changed
end

function CapabilityDetector:get()
  return self.capabilities
end

return CapabilityDetector
