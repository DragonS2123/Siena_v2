local StateBuilder = {}
StateBuilder.__index = StateBuilder

local function captured_at()
  if os.date then return os.date("!%Y-%m-%dT%H:%M:%SZ") end
  return "1970-01-01T00:00:00Z"
end

function StateBuilder.new(bridge_version)
  return setmetatable({ bridge_version = bridge_version }, StateBuilder)
end

function StateBuilder:build(raw, session_id, sequence)
  return {
    schema_version = "1.0",
    session_id = session_id,
    sequence = sequence,
    captured_at = captured_at(),
    source = "cet",
    bridge_version = self.bridge_version,
    game = { running = raw.running, loaded = raw.loaded, paused = raw.paused },
    player = {
      health = raw.health, max_health = raw.max_health,
      in_combat = raw.in_combat, in_vehicle = raw.in_vehicle,
      position = raw.position
    },
    companion = {
      present = false, health = 0, max_health = 1, distance_to_player = 0,
      current_intent = "follow", moving = false
    },
    environment = { district = "Unavailable", visible_hostiles = 0, highest_threat_id = nil },
    deep_game_state = raw.deep_game_state
  }
end

return StateBuilder
