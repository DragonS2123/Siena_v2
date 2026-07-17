local Probes = {}
Probes.__index = Probes

local function player_or_log(self, probe)
  local player, ok = self.safe:call(probe, "Game.GetPlayer", function() return Game.GetPlayer() end)
  if not ok or player == nil then
    self.logger:unavailable(probe, "player exists only in an active loaded session")
    return nil
  end
  return player
end

local function item_summary(self, probe, label, item)
  if item == nil then self.logger:unavailable(probe, label .. " is empty"); return end
  self.logger:ok(probe, label .. " type=" .. self.safe:type_name(item) .. " value=" .. tostring(item))
  local item_id, ok = self.safe:call(probe, label .. ":GetItemID", function() return item:GetItemID() end)
  if ok and item_id ~= nil then
    self.safe:call(probe, label .. ":GetItemID().id", function() return item_id.id end)
  end
end

local function bounded_each(self, probe, label, values, callback)
  if type(values) ~= "table" then
    self.logger:unavailable(probe, label .. " did not return a Lua table")
    return
  end
  local count = math.min(#values, self.config.max_items)
  self.logger:ok(probe, label .. " bounded_count=" .. tostring(count))
  for index = 1, count do callback(values[index], index) end
  if #values > count then
    self.logger:unavailable(probe, label .. " truncated_at=" .. tostring(count))
  end
end

local function item_id_summary(self, probe, label, item_id)
  if item_id == nil then self.logger:unavailable(probe, label .. " is nil"); return nil end
  self.logger:ok(probe, label .. " type=" .. self.safe:type_name(item_id) .. " value=" .. tostring(item_id))
  self.safe:call(probe, label .. ".id", function() return item_id.id end)
  local tdbid = self.safe:call(probe, label .. ":GetTDBID", function() return item_id:GetTDBID() end)
  return tdbid
end

local function static_item_metadata(self, probe, label, tdbid)
  if tdbid == nil then self.logger:unavailable(probe, label .. " has no TweakDBID"); return end
  self.logger:info(probe, label .. " metadata_source=static_tweakdb")
  local record, ok = self.safe:call(probe, "TweakDBInterface.GetItemRecord(" .. label .. ")", function()
    return TweakDBInterface.GetItemRecord(tdbid)
  end)
  if not ok or record == nil then return end
  self.safe:call(probe, label .. ".record:DisplayName", function() return record:DisplayName() end)
  self.safe:call(probe, label .. ".record:ItemType", function() return record:ItemType() end)
  self.safe:call(probe, label .. ".record:EquipArea", function() return record:EquipArea() end)
  self.safe:call(probe, label .. ".record:Quality", function() return record:Quality() end)
  local tags = self.safe:call(probe, label .. ".record:Tags", function() return record:Tags() end)
  bounded_each(self, probe, label .. ".record.tags", tags, function(tag, index)
    self.logger:ok(probe, label .. ".record.tags[" .. tostring(index) .. "]=" .. tostring(tag))
  end)
end

local function item_data_summary(self, probe, label, item_data)
  if item_data == nil then self.logger:unavailable(probe, label .. " item data is nil"); return end
  local item_id = self.safe:call(probe, label .. ":GetID", function() return item_data:GetID() end)
  local tdbid = item_id_summary(self, probe, label .. ".item_id", item_id)
  self.safe:call(probe, label .. ":GetItemType", function() return item_data:GetItemType() end)
  self.safe:call(probe, label .. ":HasTag(Cyberware)", function() return item_data:HasTag("Cyberware") end)
  self.safe:call(probe, label .. ":HasTag(SoftwareShard)", function() return item_data:HasTag("SoftwareShard") end)
  self.safe:call(probe, label .. ":HasTag(QuickhackCraftingPart)", function() return item_data:HasTag("QuickhackCraftingPart") end)
  self.safe:call(probe, "RPGManager.GetItemDataQuality(" .. label .. ")", function() return RPGManager.GetItemDataQuality(item_data) end)
  self.safe:call(probe, "RPGManager.IsItemDataIconic(" .. label .. ")", function() return RPGManager.IsItemDataIconic(item_data) end)
  static_item_metadata(self, probe, label, tdbid)
end

local function equipped_deck_data(self, probe)
  local player = player_or_log(self, probe); if not player then return nil, {} end
  self.logger:info(probe, "metadata_source=runtime_equipment")
  local candidates = {}
  local data = self.safe:call(probe, "EquipmentSystem.GetData", function() return EquipmentSystem.GetData(player) end)
  if data ~= nil then
    local active = self.safe:call(probe, "EquipmentSystemPlayerData:GetActiveItem(SystemReplacementCW)", function()
      return data:GetActiveItem(gamedataEquipmentArea.SystemReplacementCW)
    end)
    if active ~= nil then candidates[#candidates + 1] = active end
  end
  local equipment = self.safe:call(probe, "ScriptableSystemsContainer:Get(EquipmentSystem)", function()
    return Game.GetScriptableSystemsContainer():Get("EquipmentSystem")
  end)
  if equipment ~= nil then
    local area_items = self.safe:call(probe, "EquipmentSystem:GetItemsInArea(SystemReplacementCW)", function()
      return equipment.GetItemsInArea(player, gamedataEquipmentArea.SystemReplacementCW)
    end)
    bounded_each(self, probe, "SystemReplacementCW.items", area_items, function(item_id)
      candidates[#candidates + 1] = item_id
    end)
  end
  local transaction = self.safe:call(probe, "Game.GetTransactionSystem", function() return Game.GetTransactionSystem() end)
  local results = {}
  if transaction ~= nil then
    local count = math.min(#candidates, self.config.max_items)
    for index = 1, count do
      local item_id = candidates[index]
      item_id_summary(self, probe, "deck_candidate[" .. tostring(index) .. "]", item_id)
      local item_data = self.safe:call(probe, "TransactionSystem:GetItemData(deck_candidate[" .. tostring(index) .. "])", function()
        return transaction:GetItemData(player, item_id)
      end)
      if item_data ~= nil then results[#results + 1] = item_data end
    end
  end
  if #results == 0 then self.logger:unavailable(probe, "no SystemReplacementCW item data was resolved") end
  return player, results
end

function Probes.new(config, logger, safe)
  return setmetatable({ config = config, logger = logger, safe = safe }, Probes)
end

function Probes:probe_player()
  local probe = "probe_player"
  local player = player_or_log(self, probe); if not player then return end
  self.safe:call(probe, "PlayerPuppet:GetEntityID", function() return player:GetEntityID() end)
  self.safe:call(probe, "PlayerPuppet:GetWorldPosition", function() return player:GetWorldPosition() end)
  self.safe:call(probe, "PlayerPuppet:GetMountedVehicle", function() return player:GetMountedVehicle() end)
  self.safe:call(probe, "SystemRequestsHandler:IsPreGame", function() return Game.GetSystemRequestsHandler():IsPreGame() end)
end

function Probes:probe_stats()
  local probe = "probe_stats"
  local player = player_or_log(self, probe); if not player then return end
  local system, ok = self.safe:call(probe, "Game.GetStatsSystem", function() return Game.GetStatsSystem() end)
  if not ok or system == nil then return end
  local entity = player:GetEntityID()
  -- "Memory" added as a candidate: final.redscripts (compiled bytecode string
  -- pool) shows GetPermanentMemoryBonus alongside GetPermanentHealthBonus and
  -- GetPermanentStaminaBonus (symmetrical family), plus a MemoryRegenRate /
  -- MemoryCostModifier / MemoryCostReduction stat-name cluster -- strong
  -- textual evidence "Memory" is the internal name for the UI's "RAM", but
  -- CET Lua accessibility of this exact name is NOT yet runtime-confirmed.
  for _, stat in ipairs({ "Health", "Level", "StreetCred", "Armor", "PowerLevel", "Memory" }) do
    self.safe:call(probe, "StatsSystem:GetStatValue(" .. stat .. ")", function() return system:GetStatValue(entity, stat) end)
  end
end

function Probes:probe_stat_pools()
  local probe = "probe_stat_pools"
  local player = player_or_log(self, probe); if not player then return end
  local system, ok = self.safe:call(probe, "Game.GetStatPoolsSystem", function() return Game.GetStatPoolsSystem() end)
  if not ok or system == nil then return end
  local entity = player:GetEntityID()
  self.safe:call(probe, "StatPoolsSystem:GetStatPoolValue(Health)", function() return system:GetStatPoolValue(entity, gamedataStatPoolType.Health, false) end)
  self.safe:call(probe, "StatPoolsSystem:GetStatPoolMaxPointValue(Health)", function() return system:GetStatPoolMaxPointValue(entity, gamedataStatPoolType.Health) end)
  -- "Memory" candidate (RAM): final.redscripts' compiled string pool shows a
  -- StatPoolsSystem method family (GetStatPoolValue/GetStatPoolCurrentValue/
  -- GetStatPoolMaxPoints/GetStatPoolPercentage/IsStatPoolValid) plus a
  -- MemoryRegenRate/MemoryRegenRateBase/MemoryRegenRateAdd/MemoryRegenRateMult
  -- stat cluster -- this is an educated, textually-evidenced attempt, not a
  -- memorized/invented call. pcall (via safe:call) makes a wrong enum member
  -- name a logged "error", never a crash.
  self.safe:call(probe, "StatPoolsSystem:GetStatPoolValue(Memory)", function() return system:GetStatPoolValue(entity, gamedataStatPoolType.Memory, false) end)
  self.safe:call(probe, "StatPoolsSystem:GetStatPoolMaxPointValue(Memory)", function() return system:GetStatPoolMaxPointValue(entity, gamedataStatPoolType.Memory) end)
  self.logger:unavailable(probe, "RAM regeneration rate getter (candidate stat names MemoryRegenRate/MemoryRegenRateBase) was not attempted; no confirmed resolved-regen getter method name found, only stat-name evidence")
end

function Probes:probe_development()
  local probe = "probe_development"
  local player = player_or_log(self, probe); if not player then return end
  local system, ok = self.safe:call(probe, "PlayerDevelopmentSystem.GetInstance", function() return PlayerDevelopmentSystem.GetInstance(player) end)
  if not ok or system == nil then return end
  self.safe:call(probe, "PlayerDevelopmentSystem:GetDevelopmentData", function() return system:GetDevelopmentData(player) end)
  self.logger:unavailable(probe, "read-only attribute/perk/skill point getters were not confirmed for game 3.0.80; mutating console methods are intentionally excluded")
end

function Probes:probe_equipment()
  local probe = "probe_equipment"
  local player = player_or_log(self, probe); if not player then return end
  local data, ok = self.safe:call(probe, "EquipmentSystem.GetData", function() return EquipmentSystem.GetData(player) end)
  if not ok or data == nil then return end
  local active = self.safe:call(probe, "EquipmentSystemPlayerData:GetActiveWeaponObject(40)", function() return data:GetActiveWeaponObject(40) end)
  item_summary(self, probe, "active_weapon", active)
end

function Probes:probe_inventory()
  local probe = "probe_inventory"
  local player = player_or_log(self, probe); if not player then return end
  local system, ok = self.safe:call(probe, "Game.GetTransactionSystem", function() return Game.GetTransactionSystem() end)
  if not ok or system == nil then return end
  self.safe:call(probe, "TransactionSystem:GetItemQuantity(Items.money)", function()
    return system:GetItemQuantity(player, ItemID.new(TweakDBID.new("Items.money")))
  end)
  self.logger:unavailable(probe, "no bounded inventory-list call was confirmed; unbounded GetItemList is intentionally not executed")
end

function Probes:probe_cyberware()
  local probe = "probe_cyberware"
  local player = player_or_log(self, probe); if not player then return end
  self.safe:call(probe, "EquipmentSystem.GetData", function() return EquipmentSystem.GetData(player) end)
  self.logger:unavailable(probe, "cyberware slot enumeration and capacity getters require confirmed 2.x/3.x REDscript bridge or runtime type evidence")
end

function Probes:probe_quickhacks()
  local probe = "probe_quickhacks"
  local player = player_or_log(self, probe); if not player then return end
  self.safe:call(probe, "EquipmentSystem.GetData", function() return EquipmentSystem.GetData(player) end)
  local transaction, ok = self.safe:call(probe, "Game.GetTransactionSystem", function() return Game.GetTransactionSystem() end)
  if ok and transaction ~= nil then
    -- Cyberdeck program slot lead: final.redscripts' string pool confirms
    -- exactly 8 real symbols CyberdeckProgram1..CyberdeckProgram8. The
    -- "AttachmentSlots." group prefix is an educated guess by pattern match
    -- with the ALREADY-CONFIRMED probe_weapons call
    -- (TransactionSystem:GetItemInSlot(player, TweakDBID.new("AttachmentSlots.WeaponRight")))
    -- -- the method itself is confirmed, only this specific slot-path string
    -- is untested. A wrong path yields a safe nil, never a crash.
    for i = 1, 8 do
      local slot = "AttachmentSlots.CyberdeckProgram" .. tostring(i)
      local item = self.safe:call(probe, "TransactionSystem:GetItemInSlot(" .. slot .. ")", function()
        return transaction:GetItemInSlot(player, TweakDBID.new(slot))
      end)
      item_summary(self, probe, slot, item)
    end
  end
  self.logger:unavailable(probe, "RAM cost, upload time, duration, spread, cooldown, trace and target availability have no confirmed direct CET getter; MinigameActionType_Record.MemoryCost (final.redscripts) is a static-TDB candidate for base RAM cost only, after a program item/record is discovered")
end

function Probes:probe_equipment_areas()
  local probe = "probe_equipment_areas"
  local _, decks = equipped_deck_data(self, probe)
  self.logger:ok(probe, "SystemReplacementCW resolved_item_data_count=" .. tostring(math.min(#decks, self.config.max_items)))
end

function Probes:probe_cyberdeck_identity()
  local probe = "probe_cyberdeck_identity"
  local _, decks = equipped_deck_data(self, probe)
  local count = math.min(#decks, self.config.max_items)
  for index = 1, count do
    item_data_summary(self, probe, "cyberdeck[" .. tostring(index) .. "]", decks[index])
  end
end

function Probes:probe_cyberdeck_slots()
  local probe = "probe_cyberdeck_slots"
  local _, decks = equipped_deck_data(self, probe)
  local count = math.min(#decks, self.config.max_items)
  for index = 1, count do
    local deck = decks[index]
    local label = "cyberdeck[" .. tostring(index) .. "]"
    local empty = self.safe:call(probe, label .. ":GetEmptySlotsOnItem", function() return deck:GetEmptySlotsOnItem() end)
    bounded_each(self, probe, label .. ".empty_slots", empty, function(slot, slot_index)
      self.logger:ok(probe, label .. ".empty_slots[" .. tostring(slot_index) .. "]=" .. tostring(slot))
    end)
    local used = self.safe:call(probe, label .. ":GetUsedSlotsOnItem", function() return deck:GetUsedSlotsOnItem() end)
    bounded_each(self, probe, label .. ".used_slots", used, function(slot, slot_index)
      self.logger:ok(probe, label .. ".used_slots[" .. tostring(slot_index) .. "]=" .. tostring(slot))
    end)
    self.logger:unavailable(probe, "compiled GetSlotsForCyberdeckFromItemData is not exposed as a CET Lua global; live-tested unavailable and no longer called")
  end
end

local function probe_deck_parts(self, probe, decks, include_metadata)
  local count = math.min(#decks, self.config.max_items)
  for deck_index = 1, count do
    local deck = decks[deck_index]
    local label = "cyberdeck[" .. tostring(deck_index) .. "]"
    local parts = self.safe:call(probe, label .. ":GetItemParts", function() return deck:GetItemParts() end)
    bounded_each(self, probe, label .. ".parts", parts, function(part, part_index)
      local part_label = label .. ".parts[" .. tostring(part_index) .. "]"
      self.logger:info(probe, part_label .. " metadata_source=runtime_deck_part")
      local slot_id = self.safe:call(probe, "InnerItemData.GetSlotID(" .. part_label .. ")", function()
        return InnerItemData.GetSlotID(part)
      end)
      self.logger:ok(probe, part_label .. ".slot_id=" .. tostring(slot_id))
      local item_id = self.safe:call(probe, "InnerItemData.GetItemID(" .. part_label .. ")", function()
        return InnerItemData.GetItemID(part)
      end)
      item_id_summary(self, probe, part_label .. ".item_id", item_id)
      if include_metadata then
        local item_data = self.safe:call(probe, "TransactionSystem:GetItemData(" .. part_label .. ")", function()
          return Game.GetTransactionSystem():GetItemData(Game.GetPlayer(), item_id)
        end)
        item_data_summary(self, probe, part_label, item_data)
        self.safe:call(probe, "RPGManager.GetInnerItemDataQuality(" .. part_label .. ")", function()
          return RPGManager.GetInnerItemDataQuality(part)
        end)
        self.safe:call(probe, "RPGManager.IsInnerItemDataIconic(" .. part_label .. ")", function()
          return RPGManager.IsInnerItemDataIconic(part)
        end)
      end
    end)
  end
end

function Probes:probe_quickhack_programs()
  local probe = "probe_quickhack_programs"
  local _, decks = equipped_deck_data(self, probe)
  self.logger:unavailable(probe, "compiled GetPlayerQuickHackInCyberDeck is not exposed as a CET Lua global; live-tested unavailable and no longer called")
  probe_deck_parts(self, probe, decks, false)
end

function Probes:probe_quickhack_metadata()
  local probe = "probe_quickhack_metadata"
  local _, decks = equipped_deck_data(self, probe)
  probe_deck_parts(self, probe, decks, true)
  self.logger:unavailable(probe, "base RAM cost remains blocked until an installed program can be linked to a MinigameActionType_Record; static MemoryCost must not be reported as resolved runtime cost")
  self.logger:unavailable(probe, "upload time, duration, cooldown, spread and trace remain blocked without a confirmed program-to-action record path")
end

function Probes:probe_weapons()
  local probe = "probe_weapons"
  local player = player_or_log(self, probe); if not player then return end
  local transaction, ok = self.safe:call(probe, "Game.GetTransactionSystem", function() return Game.GetTransactionSystem() end)
  if not ok or transaction == nil then return end
  for _, slot in ipairs({ "AttachmentSlots.WeaponRight", "AttachmentSlots.WeaponLeft" }) do
    local item = self.safe:call(probe, "TransactionSystem:GetItemInSlot(" .. slot .. ")", function()
      return transaction:GetItemInSlot(player, TweakDBID.new(slot))
    end)
    item_summary(self, probe, slot, item)
  end
  self.logger:unavailable(probe, "third weapon-slot identity, magazine/reserve ammo and derived damage require runtime confirmation")
end

function Probes:probe_status_effects()
  local probe = "probe_status_effects"
  local player = player_or_log(self, probe); if not player then return end
  if StatusEffectHelper == nil or StatusEffectHelper.GetAppliedEffects == nil then
    self.logger:unavailable(probe, "StatusEffectHelper.GetAppliedEffects unavailable")
    return
  end
  local effects, ok = self.safe:call(probe, "StatusEffectHelper.GetAppliedEffects", function() return StatusEffectHelper.GetAppliedEffects(player) end)
  if ok and type(effects) == "table" then
    local count = math.min(#effects, self.config.max_items)
    self.logger:ok(probe, "bounded_effect_count=" .. tostring(count))
  end
end

function Probes:probe_quests()
  local probe = "probe_quests"
  player_or_log(self, probe)
  self.safe:call(probe, "Game.GetJournalManager", function() return Game.GetJournalManager() end)
  self.safe:call(probe, "Game.GetQuestsSystem", function() return Game.GetQuestsSystem() end)
  self.logger:unavailable(probe, "tracked quest/objective getter signature was not confirmed; quest mutation APIs are intentionally excluded")
end

function Probes:probe_vehicle()
  local probe = "probe_vehicle"
  local player = player_or_log(self, probe); if not player then return end
  local vehicle = self.safe:call(probe, "PlayerPuppet:GetMountedVehicle", function() return player:GetMountedVehicle() end)
  if vehicle ~= nil then
    self.safe:call(probe, "VehicleObject:GetRecordID", function() return vehicle:GetRecordID() end)
    self.safe:call(probe, "VehicleObject:GetEntityID", function() return vehicle:GetEntityID() end)
  end
end

function Probes:probe_target()
  local probe = "probe_target"
  local player = player_or_log(self, probe); if not player then return end
  local system, ok = self.safe:call(probe, "Game.GetTargetingSystem", function() return Game.GetTargetingSystem() end)
  if not ok or system == nil then return end
  -- KNOWN BREAK on this build (CET 1.37.1 / game 3.0.80.51928): the installed
  -- GameEntityExaminerTool mod calls this exact 2-argument form in its own
  -- main update loop and its OWN log (GameEntityExaminerTool.1.log,
  -- 2026-07-15 23:15:51) shows it failing with "Function
  -- 'GetComponentClosestToCrosshair' parameter 3 must be gameTargetSearchQuery."
  -- The native signature has drifted to require a third gameTargetSearchQuery
  -- argument on this build. No redscript/CET source declaring that type was
  -- found locally, so no constructor is guessed here -- this call is kept
  -- exactly as every other installed mod calls it (for direct comparability)
  -- and is expected to be logged as an "error", not a crash, via pcall.
  local component = self.safe:call(probe, "TargetingSystem:GetComponentClosestToCrosshair", function() return system:GetComponentClosestToCrosshair(player, nil) end)
  if component ~= nil then
    local entity = self.safe:call(probe, "TargetingComponent:GetEntity", function() return component:GetEntity() end)
    if entity ~= nil then
      self.safe:call(probe, "target:GetEntityID", function() return entity:GetEntityID() end)
      self.safe:call(probe, "target:GetRecordID", function() return entity:GetRecordID() end)
    end
  end
  -- Wanted-level lead: final.redscripts' string pool confirms a real
  -- GetPreventionSystem function name plus CurrentWantedLevel /
  -- GetWantedLevelFact / RegisterWantedLevelListener / OnCurrentWantedLevelChanged
  -- symbols, but static string extraction cannot show which class owns them.
  -- This only proves the global getter exists and logs its returned type --
  -- it does not call any further unconfirmed method on the result.
  self.safe:call(probe, "Game.GetPreventionSystem", function() return Game.GetPreventionSystem() end)
end

function Probes:probe_nearby_entities()
  local probe = "probe_nearby_entities"
  player_or_log(self, probe)
  self.logger:unavailable(probe, "no bounded read-only spatial query was confirmed; scanning world entities or using spawner APIs is intentionally forbidden")
end

return Probes
