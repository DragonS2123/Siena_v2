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

local function nullable_number(value)
  if finite_number(value) then return value end
  return nil
end

local function empty_deep_state(max_status_effects)
  return {
    capabilities = {
      player = false, stats = false, stat_pools = false,
      weapon = false, status_effects = false
    },
    player = {
      entity_available = nil, session_available = nil, is_pre_game = nil,
      position = nil, mounted_vehicle = nil
    },
    stats = {
      level = nil, street_cred = nil, armor = nil,
      power_level = nil, health = nil, memory = nil
    },
    stat_pools = {
      current_health = nil, maximum_health = nil,
      current_memory = nil, maximum_memory = nil
    },
    weapon = { drawn = nil, record_id = nil, source = nil },
    status_effects = { observed_count = nil, truncated = nil, limit = max_status_effects }
  }
end

function StateReader.new(diagnostics, config)
  return setmetatable({
    diagnostics = diagnostics,
    config = config,
    last_static_stats_at = -math.huge,
    last_weapon_at = -math.huge,
    last_status_effects_at = -math.huge,
    static_stats_cache = nil,
    static_stats_available = false,
    weapon_cache = nil,
    weapon_available = false,
    status_effects_cache = nil
  }, StateReader)
end

function StateReader:_clear_session_cache()
  self.last_static_stats_at = -math.huge
  self.last_weapon_at = -math.huge
  self.last_status_effects_at = -math.huge
  self.static_stats_cache = nil
  self.static_stats_available = false
  self.weapon_cache = nil
  self.weapon_available = false
  self.status_effects_cache = nil
end

function StateReader:_read_static_stats(player, entity_id, now_ms)
  if self.static_stats_cache ~= nil and now_ms - self.last_static_stats_at < self.config.deep_static_stats_interval_ms then
    return self.static_stats_cache, self.static_stats_available
  end

  self.last_static_stats_at = now_ms
  local system_ok, system = protected(function() return Game.GetStatsSystem() end)
  if not system_ok or system == nil or entity_id == nil then return nil, false end

  local values = {}
  local complete = true
  local fields = {
    { "level", "Level" }, { "street_cred", "StreetCred" },
    { "armor", "Armor" }, { "power_level", "PowerLevel" },
    { "health", "Health" }, { "memory", "Memory" }
  }
  for _, field in ipairs(fields) do
    local ok, value = protected(function() return system:GetStatValue(entity_id, field[2]) end)
    values[field[1]] = nullable_number(value)
    if not ok or values[field[1]] == nil then complete = false end
  end
  self.static_stats_cache = values
  self.static_stats_available = complete
  return values, complete
end

function StateReader:_read_weapon(player, now_ms)
  if self.weapon_cache ~= nil and now_ms - self.last_weapon_at < self.config.deep_weapon_interval_ms then
    return self.weapon_cache, self.weapon_available
  end

  self.last_weapon_at = now_ms
  local active = nil
  local active_call_ok = false
  local data_ok, data = protected(function() return EquipmentSystem.GetData(player) end)
  if data_ok and data ~= nil then
    active_call_ok, active = protected(function() return data:GetActiveWeaponObject(40) end)
  end

  local slot_item = nil
  local transaction_ok, transaction = protected(function() return Game.GetTransactionSystem() end)
  local slot_call_ok = false
  if transaction_ok and transaction ~= nil then
    slot_call_ok, slot_item = protected(function()
      return transaction:GetItemInSlot(player, TweakDBID.new("AttachmentSlots.WeaponRight"))
    end)
  end

  local selected = active or slot_item
  local source = "none"
  if active ~= nil then source = "active" elseif slot_item ~= nil then source = "weapon_right" end
  local record_id = nil
  if selected ~= nil then
    local item_id_ok, item_id = protected(function() return selected:GetItemID() end)
    if item_id_ok and item_id ~= nil then
      local record_ok, value = protected(function()
        if item_id.id == nil then return nil end
        return tostring(item_id.id)
      end)
      if record_ok and type(value) == "string" and value ~= "" then record_id = value end
    end
  end

  local value = { drawn = active ~= nil, record_id = record_id, source = source }
  self.weapon_cache = value
  self.weapon_available = active_call_ok or slot_call_ok
  return value, self.weapon_available
end

function StateReader:_read_status_effects(player, now_ms)
  if self.status_effects_cache ~= nil and now_ms - self.last_status_effects_at < self.config.deep_status_effects_interval_ms then
    return self.status_effects_cache, true
  end

  self.last_status_effects_at = now_ms
  if StatusEffectHelper == nil or StatusEffectHelper.GetAppliedEffects == nil then return nil, false end
  local effects_ok, effects = protected(function() return StatusEffectHelper.GetAppliedEffects(player) end)
  if not effects_ok or type(effects) ~= "table" then return nil, false end

  local observed = 0
  local truncated = false
  for index = 1, self.config.deep_status_effects_max_items + 1 do
    if effects[index] == nil then break end
    if index > self.config.deep_status_effects_max_items then
      truncated = true
      break
    end
    observed = observed + 1
  end
  local value = {
    observed_count = observed,
    truncated = truncated,
    limit = self.config.deep_status_effects_max_items
  }
  self.status_effects_cache = value
  return value, true
end

function StateReader:read(now_ms)
  local deep = empty_deep_state(self.config.deep_status_effects_max_items)
  local result = {
    running = true,
    loaded = false,
    paused = false,
    health = 0,
    max_health = 1,
    position = { x = 0, y = 0, z = 0 },
    in_combat = false,
    in_vehicle = false,
    deep_game_state = deep,
    capabilities = {
      player_health = false,
      player_position = false,
      combat_state = false,
      vehicle_state = false,
      pause_state = false,
      district = false,
      deep_player = false,
      deep_stats = false,
      deep_stat_pools = false,
      deep_weapon = false,
      deep_status_effects = false
    }
  }

  local pre_game_ok, is_pre_game = protected(function()
    local handler = Game.GetSystemRequestsHandler()
    if handler == nil then return nil end
    return handler:IsPreGame()
  end)
  if pre_game_ok and type(is_pre_game) == "boolean" then deep.player.is_pre_game = is_pre_game end

  local player_ok, player = protected(function() return Game.GetPlayer() end)
  result.capabilities.deep_player = player_ok
  deep.capabilities.player = player_ok
  deep.player.entity_available = player_ok and player ~= nil
  deep.player.session_available = player_ok and player ~= nil
  if not player_ok or player == nil then
    self:_clear_session_cache()
    self.diagnostics:log("player_unavailable", "player unavailable", now_ms, false)
    return result
  end
  result.loaded = true

  local entity_ok, entity_id = protected(function() return player:GetEntityID() end)
  if not entity_ok then entity_id = nil end

  local position_ok, position = protected(function() return player:GetWorldPosition() end)
  if position_ok and position ~= nil and finite_number(position.x) and finite_number(position.y) and finite_number(position.z) then
    result.position = { x = position.x, y = position.y, z = position.z }
    deep.player.position = { x = position.x, y = position.y, z = position.z }
    result.capabilities.player_position = true
  end

  local pools_ok, pools = protected(function() return Game.GetStatPoolsSystem() end)
  local health_ok, health = false, nil
  local max_health_ok, max_health = false, nil
  local memory_ok, memory = false, nil
  local max_memory_ok, max_memory = false, nil
  if pools_ok and pools ~= nil and entity_id ~= nil then
    health_ok, health = protected(function() return pools:GetStatPoolValue(entity_id, gamedataStatPoolType.Health, false) end)
    max_health_ok, max_health = protected(function() return pools:GetStatPoolMaxPointValue(entity_id, gamedataStatPoolType.Health) end)
    memory_ok, memory = protected(function() return pools:GetStatPoolValue(entity_id, gamedataStatPoolType.Memory, false) end)
    max_memory_ok, max_memory = protected(function() return pools:GetStatPoolMaxPointValue(entity_id, gamedataStatPoolType.Memory) end)
  end
  if health_ok and max_health_ok and finite_number(health) and finite_number(max_health) and max_health > 0 and health >= 0 and health <= max_health then
    result.health = health
    result.max_health = max_health
    result.capabilities.player_health = true
    deep.stat_pools.current_health = health
    deep.stat_pools.maximum_health = max_health
  end
  if memory_ok and finite_number(memory) and memory >= 0 then deep.stat_pools.current_memory = memory end
  if max_memory_ok and finite_number(max_memory) and max_memory > 0 then deep.stat_pools.maximum_memory = max_memory end
  local stat_pools_available = health_ok and max_health_ok and memory_ok and max_memory_ok
  deep.capabilities.stat_pools = stat_pools_available
  result.capabilities.deep_stat_pools = stat_pools_available

  local stats, stats_available = self:_read_static_stats(player, entity_id, now_ms)
  if stats ~= nil then deep.stats = stats end
  deep.capabilities.stats = stats_available
  result.capabilities.deep_stats = stats_available

  local weapon, weapon_available = self:_read_weapon(player, now_ms)
  if weapon ~= nil then deep.weapon = weapon end
  deep.capabilities.weapon = weapon_available
  result.capabilities.deep_weapon = weapon_available

  local effects, effects_available = self:_read_status_effects(player, now_ms)
  if effects ~= nil then deep.status_effects = effects end
  deep.capabilities.status_effects = effects_available
  result.capabilities.deep_status_effects = effects_available

  local combat_ok, in_combat = protected(function() return player:IsInCombat() end)
  if combat_ok and type(in_combat) == "boolean" then
    result.in_combat = in_combat
    result.capabilities.combat_state = true
  end

  local vehicle_ok, vehicle = protected(function() return player:GetMountedVehicle() end)
  if vehicle_ok then
    result.in_vehicle = vehicle ~= nil
    deep.player.mounted_vehicle = vehicle ~= nil
    result.capabilities.vehicle_state = true
  end

  player = nil
  return result
end

return StateReader
