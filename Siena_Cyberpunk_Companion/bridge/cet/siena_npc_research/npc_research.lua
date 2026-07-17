local NpcResearch = {}
NpcResearch.__index = NpcResearch

local function finite_number(value)
  return type(value) == "number" and value == value and value ~= math.huge and value ~= -math.huge
end

local function runtime_type(entity)
  if entity == nil then return "nil" end
  local checks = { "NPCPuppet", "ScriptedPuppet", "gamePuppet", "Entity" }
  for _, name in ipairs(checks) do
    local ok, result = pcall(function() return entity:IsA(name) end)
    if ok and result then return name end
  end
  return "unknown"
end

function NpcResearch.new(config, logger, safe)
  return setmetatable({
    config = config, logger = logger, safe = safe,
    entity_id = nil, movement_command = nil, mode = "idle"
  }, NpcResearch)
end

function NpcResearch:_session(gate, command)
  local player_ok, player = self.safe:call(gate, command, "Game.GetPlayer", function() return Game.GetPlayer() end)
  local pre_game = nil
  local pre_game_path = "SystemRequestsHandler:IsPreGame_unavailable"
  local handler_ok, handler = self.safe:call(gate, command, "Game.GetSystemRequestsHandler", function() return Game.GetSystemRequestsHandler() end)
  if handler_ok and handler ~= nil then
    local pre_ok, value = self.safe:call(gate, command, "SystemRequestsHandler:IsPreGame", function() return handler:IsPreGame() end)
    if pre_ok and type(value) == "boolean" then pre_game = value; pre_game_path = "SystemRequestsHandler:IsPreGame" end
  end
  local available = player_ok and player ~= nil
  local session_available = available and pre_game ~= true
  local path = pre_game == nil and "Game.GetPlayer_only_pre_game_nullable" or "Game.GetPlayer+SystemRequestsHandler:IsPreGame"
  self.logger:info(gate, command,
    "session_detection_path=" .. path .. " pre_game_path=" .. pre_game_path
    .. " player_available=" .. tostring(available) .. " session_available=" .. tostring(session_available)
    .. " pre_game=" .. tostring(pre_game))
  return { player = available and player or nil, player_available = available, session_available = session_available, pre_game = pre_game, path = path }
end

function NpcResearch:_player(gate, command)
  local session = self:_session(gate, command)
  if not session.player_available then self.logger:unavailable(gate, command, "spawn_guard=no_player"); return nil, session end
  if not session.session_available then self.logger:unavailable(gate, command, "spawn_guard=confirmed_pre_game"); return nil, session end
  return session.player, session
end

function NpcResearch:_system(gate, command, require_ready)
  local ok, system = self.safe:call(gate, command, "Game.GetDynamicEntitySystem", function() return Game.GetDynamicEntitySystem() end)
  if not ok or system == nil then self.logger:unavailable(gate, command, "dynamic_entity_system=false"); return nil end
  if require_ready then
    local ready_ok, ready = self.safe:call(gate, command, "DynamicEntitySystem.IsReady", function() return system:IsReady() end)
    if not ready_ok or ready ~= true then self.logger:unavailable(gate, command, "dynamic_entity_system_ready=false"); return nil end
    local restored_ok, restored = self.safe:call(gate, command, "DynamicEntitySystem.IsRestored", function() return system:IsRestored() end)
    if not restored_ok or restored ~= true then self.logger:unavailable(gate, command, "dynamic_entity_system_restored=false"); return nil end
  end
  return system
end

function NpcResearch:_nearby_position(player, gate, command)
  local position_ok, position = self.safe:call(gate, command, "PlayerPuppet.GetWorldPosition", function() return player:GetWorldPosition() end)
  local forward_ok, forward = self.safe:call(gate, command, "PlayerPuppet.GetWorldForward", function() return player:GetWorldForward() end)
  if not position_ok or position == nil or not forward_ok or forward == nil
    or not finite_number(position.x) or not finite_number(position.y) or not finite_number(position.z) or not finite_number(position.w)
    or not finite_number(forward.x) or not finite_number(forward.y) then
    self.logger:unavailable(gate, command, "spawn_transform_unavailable position_path=GetWorldPosition+GetWorldForward")
    return nil
  end
  local built_ok, nearby = self.safe:call(gate, command, "Vector4.new_AMM_Util_475_478", function()
    local distance = self.config.spawn_distance
    return Vector4.new(position.x + forward.x * distance, position.y + forward.y * distance, position.z + self.config.spawn_vertical_offset, position.w)
  end)
  if not built_ok or nearby == nil then self.logger:unavailable(gate, command, "spawn_transform_unavailable vector_copy_failed=true"); return nil end
  self.logger:info(gate, command, "position_path=GetWorldPosition+GetWorldForward+Vector4.new_AMM_Util_475_478 distance=" .. tostring(self.config.spawn_distance) .. " vertical_offset=" .. tostring(self.config.spawn_vertical_offset))
  return nearby
end

function NpcResearch:_orientation(player, gate, command)
  local ok, orientation = self.safe:call(gate, command, "PlayerPuppet.GetWorldOrientation", function() return player:GetWorldOrientation() end)
  if not ok or orientation == nil then self.logger:unavailable(gate, command, "orientation_unavailable path=PlayerPuppet.GetWorldOrientation"); return nil end
  self.logger:info(gate, command, "orientation_path=PlayerPuppet.GetWorldOrientation direct_quaternion=true")
  return orientation
end

function NpcResearch:_tag()
  local ok, tag = pcall(function() return CName.new(self.config.tag) end)
  return ok and tag or self.config.tag
end

function NpcResearch:_tagged_id(system)
  local ok, id = self.safe:call("gate1", "tag_lookup", "GetTaggedID", function() return system:GetTaggedID(self:_tag()) end)
  if not ok or id == nil then return nil end
  local managed_ok, managed = self.safe:call("gate1", "tag_lookup", "IsManaged", function() return system:IsManaged(id) end)
  return managed_ok and managed and id or nil
end

function NpcResearch:_resolve(system)
  local id = self.entity_id or self:_tagged_id(system)
  if id == nil then return nil, nil end
  local ok, entity = self.safe:call("gate2", "resolve", "GetEntity", function() return system:GetEntity(id) end)
  return id, ok and entity or nil
end

function NpcResearch:probe_runtime()
  local command = "probe_runtime"
  local session = self:_session("gate0", command)
  local player = session.player
  local system = self:_system("gate0", command, false)
  if system == nil then return end
  local _, ready = self.safe:call("gate0", command, "IsReady", function() return system:IsReady() end)
  local _, restored = self.safe:call("gate0", command, "IsRestored", function() return system:IsRestored() end)
  local spec_ok, spec = self.safe:call("gate0", command, "DynamicEntitySpec.new", function() return DynamicEntitySpec.new() end)
  local vector_ok = false
  local quaternion_ok = false
  if player then
    vector_ok = self:_nearby_position(player, "gate0", command) ~= nil
    quaternion_ok = self:_orientation(player, "gate0", command) ~= nil
  end
  local tag_ok = self.safe:call("gate0", command, "CName.new", function() return CName.new(self.config.tag) end)
  self.logger:ok("gate0", command,
    "system=true ready=" .. tostring(ready) .. " restored=" .. tostring(restored)
    .. " spec_constructible=" .. tostring(spec_ok and spec ~= nil)
    .. " entity_id_helper_required=false create_entity_return_id_path=true"
    .. " vector_path=" .. tostring(vector_ok == true) .. " quaternion_path=" .. tostring(quaternion_ok == true)
    .. " tag_path=" .. tostring(tag_ok == true)
    .. " session_detection_path=" .. session.path
    .. " lifecycle_listener_source_confirmed=true cet_listener_target_constructible=false listener_deferred_not_gate1_blocker=true")
end

function NpcResearch:spawn()
  local command = "spawn"
  local player = self:_player("gate1", command); if not player then return end
  local system = self:_system("gate1", command, true); if not system then return end
  local existing = self:_tagged_id(system)
  if existing then self.entity_id = existing; self.logger:unavailable("gate1", command, "duplicate_blocked tagged_entity_exists=true id=" .. tostring(existing)); return end
  if self.config.candidate_record_id == nil then self.logger:error("gate1", command, "candidate_record_id_required"); return end
  local spec_ok, spec = self.safe:call("gate1", command, "DynamicEntitySpec.new", function() return DynamicEntitySpec.new() end)
  if not spec_ok or spec == nil then return end
  local position = self:_nearby_position(player, "gate1", command)
  local orientation = self:_orientation(player, "gate1", command)
  if position == nil or orientation == nil then self.logger:unavailable("gate1", command, "CreateEntity blocked spawn_transform_or_orientation_unavailable=true"); return end
  spec.recordID = TweakDBID.new(self.config.candidate_record_id)
  spec.position = position; spec.orientation = orientation
  spec.persistState = false; spec.persistSpawn = false; spec.alwaysSpawned = false
  spec.spawnInView = self.config.spawn_in_view; spec.active = true; spec.tags = { self:_tag() }
  local created_ok, id = self.safe:call("gate1", command, "CreateEntity", function() return system:CreateEntity(spec) end)
  if not created_ok or id == nil then self.logger:error("gate1", command, "create_returned_no_entity_id"); return end
  self.entity_id = id; self.mode = "spawn_requested"
  self.logger:ok("gate1", command, "CreateEntity submitted returned_id_used_directly=true id=" .. tostring(id) .. " record=" .. tostring(self.config.candidate_record_id) .. " persistState=false persistSpawn=false alwaysSpawned=false spawnInView=" .. tostring(self.config.spawn_in_view))
end

function NpcResearch:_cancel_movement(entity, reason)
  if self.movement_command == nil or entity == nil then self.movement_command = nil; return true end
  local ok = self.safe:call("gate3", reason, "StopExecutingCommand+CancelCommand", function()
    local ai = entity:GetAIControllerComponent(); ai:StopExecutingCommand(self.movement_command, true); return ai:CancelCommand(self.movement_command)
  end)
  self.movement_command = nil
  return ok == true
end

function NpcResearch:despawn(reason)
  local command = reason or "despawn"
  local system = self:_system("gate1", command, false)
  if not system then self.entity_id = nil; self.movement_command = nil; self.mode = "idle"; return end
  local id, entity = self:_resolve(system)
  self:_cancel_movement(entity, command)
  if entity then pcall(function() local reaction = entity:FindComponentByName("ReactionManager"); if reaction then reaction:DeactiveLookAt() end end) end
  if id == nil then self.logger:ok("gate1", command, "already_missing=true"); self.entity_id = nil; self.mode = "idle"; return end
  local ok, deleted = self.safe:call("gate1", command, "DeleteEntity", function() return system:DeleteEntity(id) end)
  self.logger:write(ok and "success" or "error", "gate1", command, "cleanup_result=" .. tostring(deleted) .. " id=" .. tostring(id))
  self.entity_id = nil; self.movement_command = nil; self.mode = "idle"
end

function NpcResearch:status()
  local command = "status"; local player = self:_player("gate2", command); local system = self:_system("gate2", command, false)
  if not system then return end
  local id, entity = self:_resolve(system)
  local managed = id and select(2, self.safe:call("gate2", command, "IsManaged", function() return system:IsManaged(id) end)) or false
  local spawning = id and select(2, self.safe:call("gate2", command, "IsSpawning", function() return system:IsSpawning(id) end)) or false
  local spawned = id and select(2, self.safe:call("gate2", command, "IsSpawned", function() return system:IsSpawned(id) end)) or false
  local appearance, position, alive, ai, reaction = nil, nil, nil, false, false
  if entity then
    pcall(function() appearance = NameToString(entity:GetCurrentAppearanceName()) end)
    pcall(function() position = entity:GetWorldPosition() end)
    pcall(function() alive = not entity:IsDead() end)
    pcall(function() ai = entity:GetAIControllerComponent() ~= nil end)
    pcall(function() reaction = entity:FindComponentByName("ReactionManager") ~= nil end)
  end
  self.logger:ok("gate2", command,
    "enabled=" .. tostring(self.config.enabled) .. " player=" .. tostring(player ~= nil) .. " ready=" .. tostring(select(2, pcall(function() return system:IsReady() end)))
    .. " tagged=" .. tostring(id ~= nil) .. " id=" .. tostring(id) .. " managed=" .. tostring(managed) .. " spawning=" .. tostring(spawning)
    .. " spawned=" .. tostring(spawned) .. " resolved=" .. tostring(entity ~= nil) .. " runtime_class=" .. runtime_type(entity)
    .. " appearance=" .. tostring(appearance) .. " position=" .. tostring(position) .. " alive=" .. tostring(alive)
    .. " ai_controller=" .. tostring(ai) .. " reaction_manager=" .. tostring(reaction)
    .. " mode=" .. self.mode .. " movement_command=" .. tostring(self.movement_command ~= nil)
    .. " candidate_record=" .. tostring(self.config.candidate_record_id)
    .. " attitude=unavailable_not_source_confirmed combat_behavior=unavailable_not_source_confirmed")
end

function NpcResearch:_entity_for_command(command)
  local player = self:_player("gate3", command); if not player then return nil, nil end
  local system = self:_system("gate3", command, true); if not system then return nil, nil end
  local _, entity = self:_resolve(system)
  if entity == nil or not entity:IsA("NPCPuppet") then self.logger:unavailable("gate3", command, "resolved_npc_puppet_required"); return nil, nil end
  return player, entity
end

function NpcResearch:follow()
  local player, entity = self:_entity_for_command("follow"); if not entity then return end
  self:_cancel_movement(entity, "follow_replace")
  local ok, cmd = self.safe:call("gate3", "follow", "AIFollowTargetCommand", function()
    local value = NewObject("handle:AIFollowTargetCommand"); value.desiredDistance = self.config.follow_distance; value.matchSpeed = true
    value.stopWhenDestinationReached = false; value.target = player; value.movementType = self.config.movement_type
    value.teleport = false; value.tolerance = 2; value.lookAtTarget = player; entity:GetAIControllerComponent():SendCommand(value); return value
  end)
  if ok then self.movement_command = cmd; self.mode = "follow"; self.logger:ok("gate3", "follow", "native_ai_command_submitted teleport=false target=player") end
end

function NpcResearch:stay()
  local _, entity = self:_entity_for_command("stay"); if not entity then return end
  local ok = self:_cancel_movement(entity, "stay")
  if ok then self.mode = "stay"; self.logger:ok("gate3", "stay", "active_movement_cancelled; current_position_preserved") end
end

function NpcResearch:come_here()
  local player, entity = self:_entity_for_command("come_here"); if not entity then return end
  self:_cancel_movement(entity, "come_here_replace")
  local ok, cmd = self.safe:call("gate3", "come_here", "AIMoveToCommand", function()
    local safe_pos = self:_nearby_position(player, "gate3", "come_here")
    if safe_pos == nil then error("safe nearby position unavailable") end
    local world = NewObject("WorldPosition"); world:SetVector4(world, safe_pos)
    local target = NewObject("AIPositionSpec"); target:SetWorldPosition(target, world)
    local value = NewObject("handle:AIMoveToCommand"); value.movementTarget = target; value.rotateEntityTowardsFacingTarget = false
    value.ignoreNavigation = false; value.desiredDistanceFromTarget = 2; value.movementType = self.config.movement_type
    value.finishWhenDestinationReached = true; value.alwaysUseStealth = false; entity:GetAIControllerComponent():SendCommand(value); return value
  end)
  if ok then self.movement_command = cmd; self.mode = "come_here"; self.logger:ok("gate3", "come_here", "native_navigation_submitted ignoreNavigation=false") end
end

function NpcResearch:look_at_player()
  local player, entity = self:_entity_for_command("look_at_player"); if not entity then return end
  local ok = self.safe:call("gate4", "look_at_player", "ReactionManager.ActivateReactionLookAt", function()
    local reaction = entity:FindComponentByName("ReactionManager"); if reaction == nil then error("ReactionManager unavailable") end
    reaction:ActivateReactionLookAt(player, false, 1, true, true); return true
  end)
  if ok then self.mode = "look_at"; self.logger:ok("gate4", "look_at_player", "temporary_reaction_look_at_active=true") end
end

function NpcResearch:clear_look_at()
  local _, entity = self:_entity_for_command("clear_look_at"); if not entity then return end
  local ok = self.safe:call("gate4", "clear_look_at", "ReactionManager.DeactiveLookAt", function()
    local reaction = entity:FindComponentByName("ReactionManager"); if reaction == nil then error("ReactionManager unavailable") end
    reaction:DeactiveLookAt(); return true
  end)
  if ok then self.mode = "stay"; self.logger:ok("gate4", "clear_look_at", "look_at_cleared=true") end
end

return NpcResearch
