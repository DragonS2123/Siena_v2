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

local function stable_tdbid(value, prefix)
  if value == nil then return nil end
  local text = tostring(value)
  local stable = text:match("%-%-%[%[%s*([%w_%.]+)%s*%-%-%]%]")
  if stable == nil and text:match("^[%w_%.]+$") then stable = text end
  if stable == nil or (prefix ~= nil and stable:sub(1, #prefix) ~= prefix) then return nil end
  return stable
end

local function program_slot(value)
  local stable = stable_tdbid(value, "AttachmentSlots.CyberdeckProgram")
  if stable == nil then return nil, nil end
  local order = tonumber(stable:match("CyberdeckProgram([1-8])$"))
  if order == nil then return nil, nil end
  return stable, order
end

local function quality_name(value)
  if value == nil then return nil end
  local text = tostring(value)
  return text:match("gamedataQuality%s*:%s*([%w_]+)") or text:match("^([%w_]+)$")
end

local function sorted_programs(programs)
  table.sort(programs, function(left, right)
    if left._order == right._order then return left.record_id < right.record_id end
    return left._order < right._order
  end)
  for _, program in ipairs(programs) do program._order = nil end
  return programs
end

local function empty_deep_state(max_status_effects)
  return {
    capabilities = {
      player = false, stats = false, stat_pools = false,
      weapon = false, status_effects = false,
      cyberdeck_identity = false, cyberdeck_metadata = false,
      cyberdeck_programs = false, cyberdeck_capacity = false
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
    status_effects = { observed_count = nil, truncated = nil, limit = max_status_effects },
    cyberdeck = nil
  }
end

function StateReader.new(diagnostics, config)
  return setmetatable({
    diagnostics = diagnostics,
    config = config,
    last_static_stats_at = -math.huge,
    last_weapon_at = -math.huge,
    last_status_effects_at = -math.huge,
    last_cyberdeck_at = -math.huge,
    static_stats_cache = nil,
    static_stats_available = false,
    weapon_cache = nil,
    weapon_available = false,
    status_effects_cache = nil,
    cyberdeck_cache = nil
  }, StateReader)
end

function StateReader:_clear_session_cache()
  self.last_static_stats_at = -math.huge
  self.last_weapon_at = -math.huge
  self.last_status_effects_at = -math.huge
  self.last_cyberdeck_at = -math.huge
  self.static_stats_cache = nil
  self.static_stats_available = false
  self.weapon_cache = nil
  self.weapon_available = false
  self.status_effects_cache = nil
  self.cyberdeck_cache = nil
end

function StateReader:_read_cyberdeck(player, now_ms)
  if self.cyberdeck_cache ~= nil and now_ms - self.last_cyberdeck_at < self.config.deep_cyberdeck_interval_ms then
    return self.cyberdeck_cache.value, self.cyberdeck_cache.capabilities
  end
  self.last_cyberdeck_at = now_ms
  local caps = { identity = false, metadata = false, programs = false, capacity = false }
  local candidates = {}
  local data_ok, equipment_data = protected(function() return EquipmentSystem.GetData(player) end)
  if data_ok and equipment_data ~= nil then
    local active_ok, active = protected(function()
      return equipment_data:GetActiveItem(gamedataEquipmentArea.SystemReplacementCW)
    end)
    if active_ok then
      caps.identity = true
      if active ~= nil then candidates[#candidates + 1] = active end
    end
  end

  local system_ok, equipment_system = protected(function()
    return Game.GetScriptableSystemsContainer():Get("EquipmentSystem")
  end)
  if system_ok and equipment_system ~= nil then
    local area_ok, area_items = protected(function()
      return equipment_system.GetItemsInArea(player, gamedataEquipmentArea.SystemReplacementCW)
    end)
    if area_ok and type(area_items) == "table" then
      caps.identity = true
      for index = 1, self.config.deep_status_effects_max_items + 1 do
        if area_items[index] == nil or index > self.config.deep_status_effects_max_items then break end
        candidates[#candidates + 1] = area_items[index]
      end
    end
  end

  local transaction_ok, transaction = protected(function() return Game.GetTransactionSystem() end)
  local seen_decks = {}
  local deck = nil
  if transaction_ok and transaction ~= nil then
    local candidate_limit = math.min(#candidates, self.config.deep_status_effects_max_items)
    for index = 1, candidate_limit do
      local item_id = candidates[index]
      local record_ok, record_value = protected(function() return item_id.id end)
      local record_id = record_ok and stable_tdbid(record_value, "Items.") or nil
      if record_id ~= nil and not seen_decks[record_id] then
        seen_decks[record_id] = true
        local item_data_ok, item_data = protected(function() return transaction:GetItemData(player, item_id) end)
        local cyberdeck_ok, is_cyberdeck = false, false
        if item_data_ok and item_data ~= nil then
          cyberdeck_ok, is_cyberdeck = protected(function() return item_data:HasTag(CName.new("Cyberdeck")) end)
        end
        if cyberdeck_ok and is_cyberdeck == true and deck == nil then
          deck = { item_id = item_id, item_data = item_data, record_value = record_value, record_id = record_id }
        end
      end
    end
  end

  if deck == nil then
    self.cyberdeck_cache = { value = nil, capabilities = caps }
    return nil, caps
  end

  local type_ok = protected(function() return deck.item_data:GetItemType() end)
  local quality_ok, quality_value = protected(function() return RPGManager.GetItemDataQuality(deck.item_data) end)
  local iconic_ok, iconic_value = protected(function() return RPGManager.IsItemDataIconic(deck.item_data) end)
  local record_ok, record = protected(function() return TweakDBInterface.GetItemRecord(deck.record_value) end)
  local tags_ok, record_tags = false, nil
  if record_ok and record ~= nil then tags_ok, record_tags = protected(function() return record:Tags() end) end
  caps.metadata = type_ok and quality_ok and iconic_ok and tags_ok and type(record_tags) == "table"

  local tags = {}
  for _, tag_name in ipairs({ "Cyberdeck", "Cyberware", "Iconic_OS_CW" }) do
    local tag_ok, present = protected(function() return deck.item_data:HasTag(CName.new(tag_name)) end)
    if tag_ok and present == true then tags[#tags + 1] = tag_name end
  end

  local programs = {}
  local seen_programs = {}
  local parts_truncated = false
  local parts_ok, parts = protected(function() return deck.item_data:GetItemParts() end)
  if parts_ok and type(parts) == "table" then
    caps.programs = true
    local scan_limit = self.config.deep_status_effects_max_items
    for index = 1, scan_limit + 1 do
      local part = parts[index]
      if part == nil then break end
      if index > scan_limit then parts_truncated = true; break end
      local slot_ok, slot_value = protected(function() return InnerItemData.GetSlotID(part) end)
      local slot_id, slot_order = nil, nil
      if slot_ok then slot_id, slot_order = program_slot(slot_value) end
      if slot_id ~= nil then
        local item_ok, program_item_id = protected(function() return InnerItemData.GetItemID(part) end)
        local program_record_ok, program_record_value = false, nil
        if item_ok and program_item_id ~= nil then
          program_record_ok, program_record_value = protected(function() return program_item_id.id end)
        end
        local program_record_id = program_record_ok and stable_tdbid(program_record_value, "Items.") or nil
        local key = program_record_id ~= nil and (slot_id .. "|" .. program_record_id) or nil
        if key ~= nil and not seen_programs[key] then
          seen_programs[key] = true
          local program_quality_ok, program_quality = protected(function() return RPGManager.GetInnerItemDataQuality(part) end)
          local program_iconic_ok, program_iconic = protected(function() return RPGManager.IsInnerItemDataIconic(part) end)
          programs[#programs + 1] = {
            slot_id = slot_id,
            record_id = program_record_id,
            quality = program_quality_ok and quality_name(program_quality) or nil,
            iconic = program_iconic_ok and program_iconic or nil,
            _order = slot_order
          }
        end
      end
    end
  end
  sorted_programs(programs)
  local program_limit = math.min(8, self.config.deep_status_effects_max_items)
  local truncated = parts_truncated or #programs > program_limit
  while #programs > program_limit do table.remove(programs) end

  local function read_slot_set(method_name)
    local ok, values = protected(function()
      if method_name == "used" then return deck.item_data:GetUsedSlotsOnItem() end
      return deck.item_data:GetEmptySlotsOnItem()
    end)
    if not ok or type(values) ~= "table" then return nil end
    local result = {}
    for index = 1, self.config.deep_status_effects_max_items + 1 do
      if values[index] == nil then break end
      if index > self.config.deep_status_effects_max_items then return nil end
      local slot_id = program_slot(values[index])
      if slot_id ~= nil then result[slot_id] = true end
    end
    return result
  end

  local used_slots = read_slot_set("used")
  local empty_slots = read_slot_set("empty")
  local used_count, empty_count, total_count = nil, nil, nil
  if used_slots ~= nil then used_count = 0; for _ in pairs(used_slots) do used_count = used_count + 1 end end
  if empty_slots ~= nil then empty_count = 0; for _ in pairs(empty_slots) do empty_count = empty_count + 1 end end
  local consistent = used_slots ~= nil and empty_slots ~= nil
  local union = {}
  if consistent then
    for slot_id in pairs(used_slots) do union[slot_id] = true end
    for slot_id in pairs(empty_slots) do
      if used_slots[slot_id] then consistent = false end
      union[slot_id] = true
    end
  end
  if consistent then
    total_count = 0
    for _ in pairs(union) do total_count = total_count + 1 end
    caps.capacity = true
  elseif used_slots ~= nil and empty_slots ~= nil then
    used_count, empty_count = nil, nil
  end

  local value = {
    record_id = deck.record_id,
    quality = quality_ok and quality_name(quality_value) or nil,
    iconic = iconic_ok and iconic_value or nil,
    tags = tags,
    program_capacity = { used = used_count, empty = empty_count, total = total_count },
    programs = programs,
    truncated = truncated
  }
  deck = nil
  candidates = nil
  self.cyberdeck_cache = { value = value, capabilities = caps }
  return value, caps
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
      deep_status_effects = false,
      cyberdeck_identity = false,
      cyberdeck_metadata = false,
      cyberdeck_programs = false,
      cyberdeck_capacity = false
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

  local cyberdeck, cyberdeck_caps = self:_read_cyberdeck(player, now_ms)
  deep.cyberdeck = cyberdeck
  deep.capabilities.cyberdeck_identity = cyberdeck_caps.identity
  deep.capabilities.cyberdeck_metadata = cyberdeck_caps.metadata
  deep.capabilities.cyberdeck_programs = cyberdeck_caps.programs
  deep.capabilities.cyberdeck_capacity = cyberdeck_caps.capacity
  result.capabilities.cyberdeck_identity = cyberdeck_caps.identity
  result.capabilities.cyberdeck_metadata = cyberdeck_caps.metadata
  result.capabilities.cyberdeck_programs = cyberdeck_caps.programs
  result.capabilities.cyberdeck_capacity = cyberdeck_caps.capacity

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
