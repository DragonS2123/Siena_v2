local StateReader = {}
StateReader.__index = StateReader

local function finite_number(value)
  return type(value) == "number" and value == value and value ~= math.huge and value ~= -math.huge
end

local function protected(callable)
  local ok, value_a, value_b = pcall(callable)
  if not ok then return false, nil, nil end
  return true, value_a, value_b
end

function StateReader.new(diagnostics)
  return setmetatable({ diagnostics = diagnostics }, StateReader)
end

function StateReader:read(now_ms)
  local result = {
    running = true,
    loaded = false,
    paused = false,
    health = 0,
    max_health = 1,
    position = { x = 0, y = 0, z = 0 },
    in_combat = false,
    in_vehicle = false,
    capabilities = {
      player_health = false,
      player_position = false,
      combat_state = false,
      vehicle_state = false,
      pause_state = false,
      district = false
    }
  }

  local player_ok, player = protected(function() return Game.GetPlayer() end)
  if not player_ok or player == nil then
    self.diagnostics:log("player_unavailable", "player unavailable", now_ms, false)
    return result
  end
  result.loaded = true

  local position_ok, position = protected(function() return player:GetWorldPosition() end)
  if position_ok and position ~= nil and finite_number(position.x) and finite_number(position.y) and finite_number(position.z) then
    result.position = { x = position.x, y = position.y, z = position.z }
    result.capabilities.player_position = true
  end

  local health_ok, health, max_health = protected(function()
    local pools = Game.GetStatPoolsSystem()
    local entity_id = player:GetEntityID()
    if pools == nil or entity_id == nil then return nil, nil end
    return pools:GetStatPoolValue(entity_id, gamedataStatPoolType.Health, false),
      pools:GetStatPoolMaxPointValue(entity_id, gamedataStatPoolType.Health)
  end)
  if health_ok and finite_number(health) and finite_number(max_health) and max_health > 0 and health >= 0 and health <= max_health then
    result.health = health
    result.max_health = max_health
    result.capabilities.player_health = true
  end

  local combat_ok, in_combat = protected(function() return player:IsInCombat() end)
  if combat_ok and type(in_combat) == "boolean" then
    result.in_combat = in_combat
    result.capabilities.combat_state = true
  end

  local vehicle_ok, vehicle = protected(function() return player:GetMountedVehicle() end)
  if vehicle_ok then
    result.in_vehicle = vehicle ~= nil
    result.capabilities.vehicle_state = true
  end

  player = nil
  return result
end

return StateReader
