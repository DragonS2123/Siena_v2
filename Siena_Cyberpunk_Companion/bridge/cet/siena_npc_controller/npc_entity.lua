local NpcEntity = {}
NpcEntity.__index = NpcEntity

local function finite(value) return type(value) == "number" and value == value and value ~= math.huge and value ~= -math.huge end
local function runtime_class(entity)
  if entity == nil then return nil end
  for _, name in ipairs({ "NPCPuppet", "ScriptedPuppet", "gamePuppet", "Entity" }) do
    local ok, result = pcall(function() return entity:IsA(name) end)
    if ok and result then return name end
  end
  return "unknown"
end

function NpcEntity.new(config, logger, safe)
  return setmetatable({ config=config, logger=logger, safe=safe, entity_id=nil, entity=nil, movement_command=nil, mode="absent", look_at_active=false, pending_spawn=nil, had_player=false, last_command=nil, last_result=nil, last_error=nil, current_appearance=nil }, NpcEntity)
end

function NpcEntity:_record_available()
  local id_ok, record_id = self.safe:call("TweakDBID.new", function() return TweakDBID.new(self.config.entity_record) end)
  if not id_ok or record_id == nil then return false, nil end
  local record_ok, record = self.safe:call("TweakDB.GetRecord", function() return TweakDB:GetRecord(record_id) end)
  return record_ok and record ~= nil, record_id
end

function NpcEntity:_read_appearance(entity)
  if entity == nil then return nil, false end
  local name_ok, name = self.safe:call("NPCPuppet.GetCurrentAppearanceName", function() return entity:GetCurrentAppearanceName() end)
  if not name_ok or name == nil then return nil, false end
  local text_ok, value = self.safe:call("NameToString", function() return NameToString(name) end)
  if not text_ok or type(value) ~= "string" or value == "" then return nil, false end
  self.current_appearance = value
  return value, true
end

function NpcEntity:_session()
  local ok, player = self.safe:call("Game.GetPlayer", function() return Game.GetPlayer() end)
  if not ok or player == nil then return nil, nil end
  local pre_game = nil
  local handler_ok, handler = self.safe:call("Game.GetSystemRequestsHandler", function() return Game.GetSystemRequestsHandler() end)
  if handler_ok and handler then local pre_ok, value = self.safe:call("SystemRequestsHandler.IsPreGame", function() return handler:IsPreGame() end); if pre_ok and type(value)=="boolean" then pre_game=value end end
  if pre_game == true then return nil, pre_game end
  return player, pre_game
end

function NpcEntity:_system(require_ready)
  local ok, system = self.safe:call("Game.GetDynamicEntitySystem", function() return Game.GetDynamicEntitySystem() end)
  if not ok or system == nil then return nil end
  if require_ready then
    local ready_ok, ready = self.safe:call("DynamicEntitySystem.IsReady", function() return system:IsReady() end)
    local restored_ok, restored = self.safe:call("DynamicEntitySystem.IsRestored", function() return system:IsRestored() end)
    if not ready_ok or ready ~= true or not restored_ok or restored ~= true then return nil end
  end
  return system
end

function NpcEntity:_tag()
  local ok, tag = self.safe:call("CName.new", function() return CName.new(self.config.tag) end)
  return ok and tag or self.config.tag
end

function NpcEntity:_tagged_id(system)
  local ok, id = self.safe:call("DynamicEntitySystem.GetTaggedID", function() return system:GetTaggedID(self:_tag()) end)
  if not ok or id == nil then return nil end
  local managed_ok, managed = self.safe:call("DynamicEntitySystem.IsManaged", function() return system:IsManaged(id) end)
  return managed_ok and managed and id or nil
end

function NpcEntity:_nearby_position(player)
  local pos_ok, pos = self.safe:call("PlayerPuppet.GetWorldPosition", function() return player:GetWorldPosition() end)
  local fwd_ok, forward = self.safe:call("PlayerPuppet.GetWorldForward", function() return player:GetWorldForward() end)
  if not pos_ok or not fwd_ok or pos == nil or forward == nil or not finite(pos.x) or not finite(pos.y) or not finite(pos.z) or not finite(pos.w) or not finite(forward.x) or not finite(forward.y) then return nil end
  local ok, value = self.safe:call("Vector4.new", function() return Vector4.new(pos.x + forward.x*self.config.spawn_distance, pos.y + forward.y*self.config.spawn_distance, pos.z+self.config.vertical_offset, pos.w) end)
  return ok and value or nil
end

function NpcEntity:_resolve(system)
  local id = self.entity_id or self:_tagged_id(system)
  if id == nil then return nil end
  self.entity_id = id
  local ok, entity = self.safe:call("DynamicEntitySystem.GetEntity", function() return system:GetEntity(id) end)
  if ok then self.entity = entity end
  return ok and entity or nil
end

function NpcEntity:_validate(system)
  local entity = self:_resolve(system); if entity == nil then return false, "entity_unresolved" end
  local managed_ok, managed = self.safe:call("DynamicEntitySystem.IsManaged", function() return system:IsManaged(self.entity_id) end)
  local spawned_ok, spawned = self.safe:call("DynamicEntitySystem.IsSpawned", function() return system:IsSpawned(self.entity_id) end)
  local class = runtime_class(entity)
  local alive_ok, alive = self.safe:call("NPCPuppet.IsDead", function() return not entity:IsDead() end)
  local ai_ok, ai = self.safe:call("NPCPuppet.GetAIControllerComponent", function() return entity:GetAIControllerComponent() end)
  local reaction_ok, reaction = self.safe:call("NPCPuppet.ReactionManager", function() return entity:FindComponentByName("ReactionManager") end)
  if not managed_ok or managed ~= true or not spawned_ok or spawned ~= true or class ~= "NPCPuppet" or not alive_ok or alive ~= true or not ai_ok or ai == nil or not reaction_ok or reaction == nil then return false, "spawn_failed_validation" end
  local appearance, appearance_readable = self:_read_appearance(entity)
  if appearance_readable and appearance ~= self.config.appearance then return false, "appearance_validation_failed" end
  return true, nil
end

function NpcEntity:_cancel_movement(reason)
  if self.movement_command == nil then return true end
  local entity = self.entity
  local command = self.movement_command
  self.movement_command = nil
  if entity == nil then return true end
  local ok = self.safe:call("AI.CancelCommand", function() local ai=entity:GetAIControllerComponent(); ai:StopExecutingCommand(command,true); return ai:CancelCommand(command) end)
  self.logger:trace("npc_movement_stopped", "reason=" .. reason)
  return ok == true
end

function NpcEntity:_clear_look_at()
  if not self.look_at_active then return true end
  self.look_at_active = false
  if self.entity == nil then return true end
  local ok = self.safe:call("ReactionManager.DeactiveLookAt", function() local reaction=self.entity:FindComponentByName("ReactionManager"); if reaction then reaction:DeactiveLookAt() end; return true end)
  self.logger:trace("npc_look_at_cleared", "success=" .. tostring(ok))
  return ok == true
end

function NpcEntity:cleanup(reason)
  self:_cancel_movement(reason); self:_clear_look_at()
  local system = self:_system(false)
  local id = self.entity_id or (system and self:_tagged_id(system) or nil)
  local deleted = false
  if system and id then local ok, value = self.safe:call("DynamicEntitySystem.DeleteEntity", function() return system:DeleteEntity(id) end); deleted = ok and value == true end
  self.entity_id=nil; self.entity=nil; self.pending_spawn=nil; self.mode="absent"; self.look_at_active=false; self.current_appearance=nil
  self.logger:trace(reason == "despawn" and "npc_despawned" or "npc_session_cleanup", "reason=" .. reason .. " deleted=" .. tostring(deleted) .. " already_missing=" .. tostring(id==nil))
  return "success", nil
end

function NpcEntity:spawn(now_ms)
  local player = self:_session(); if player == nil then return "unavailable", "player_or_session_unavailable" end
  local system = self:_system(true); if system == nil then return "unavailable", "dynamic_entity_system_unavailable" end
  local record_available, record_id = self:_record_available(); if not record_available then self.logger:trace("npc_spawn_failed", "reason=standalone_record_unavailable"); return "unavailable", "standalone_record_unavailable" end
  local existing = self:_tagged_id(system)
  if existing then self.entity_id=existing; self.logger:trace("npc_duplicate_blocked", "tag=" .. self.config.tag); local valid,error=self:_validate(system); if valid then self.mode="stay"; return "success",nil end; self:cleanup("spawn_failed_validation"); return "error",error end
  local position=self:_nearby_position(player); if position==nil then return "unavailable","spawn_transform_unavailable" end
  local orientation_ok,orientation=self.safe:call("PlayerPuppet.GetWorldOrientation",function() return player:GetWorldOrientation() end); if not orientation_ok or orientation==nil then return "unavailable","orientation_unavailable" end
  local spec_ok,spec=self.safe:call("DynamicEntitySpec.new",function() return DynamicEntitySpec.new() end); if not spec_ok or spec==nil then return "error","spec_unavailable" end
  spec.recordID=record_id; spec.position=position; spec.orientation=orientation
  spec.persistState=false; spec.persistSpawn=false; spec.alwaysSpawned=false; spec.active=true; spec.spawnInView=true; spec.tags={self:_tag()}
  self.logger:trace("npc_spawn_started", "record="..self.config.entity_record)
  local created_ok,id=self.safe:call("DynamicEntitySystem.CreateEntity",function() return system:CreateEntity(spec) end)
  if not created_ok or id==nil then self.logger:trace("npc_spawn_failed","reason=create_failed"); return "error","create_failed" end
  self.entity_id=id; self.mode="spawn_pending"; self.pending_spawn={deadline=now_ms+self.config.spawn_validation_timeout_ms}; self.logger:trace("npc_spawn_started","returned_id_used_directly=true")
  return "pending",nil
end

function NpcEntity:update_pending(now_ms)
  if self.pending_spawn==nil then return nil,nil end
  local system=self:_system(true); if system==nil then if now_ms>=self.pending_spawn.deadline then self:cleanup("spawn_failed_validation"); return "error","dynamic_entity_system_unavailable" end; return nil,nil end
  local spawning_ok,spawning=self.safe:call("DynamicEntitySystem.IsSpawning",function() return system:IsSpawning(self.entity_id) end)
  local spawned_ok,spawned=self.safe:call("DynamicEntitySystem.IsSpawned",function() return system:IsSpawned(self.entity_id) end)
  if spawned_ok and spawned==true then local valid,error=self:_validate(system); self.pending_spawn=nil; if valid then self.mode="stay"; self.logger:trace("npc_spawn_succeeded","runtime_class=NPCPuppet"); return "success",nil end; self:cleanup("spawn_failed_validation"); self.logger:trace("npc_spawn_failed","reason="..tostring(error)); return "error",error end
  if now_ms>=self.pending_spawn.deadline then self:cleanup("spawn_failed_validation"); self.logger:trace("npc_spawn_failed","reason=validation_timeout spawning="..tostring(spawning_ok and spawning)); return "error","spawn_failed_validation" end
  return nil,nil
end

function NpcEntity:_command_entity()
  local player=self:_session(); local system=self:_system(true); if player==nil or system==nil then return nil,nil end
  local entity=self:_resolve(system); if entity==nil or runtime_class(entity)~="NPCPuppet" then return nil,nil end
  return player,entity
end

function NpcEntity:distance_to_player()
  local player,entity=self:_command_entity(); if entity==nil then return nil end
  local pok,p=self.safe:call("PlayerPuppet.GetWorldPosition",function() return player:GetWorldPosition() end); local eok,e=self.safe:call("NPCPuppet.GetWorldPosition",function() return entity:GetWorldPosition() end)
  if not pok or not eok or p==nil or e==nil or not finite(p.x) or not finite(e.x) then return nil end
  local dx,dy,dz=e.x-p.x,e.y-p.y,e.z-p.z; return math.sqrt(dx*dx+dy*dy+dz*dz)
end

function NpcEntity:follow()
  local player,entity=self:_command_entity(); if entity==nil then return "unavailable","resolved_npc_puppet_required" end
  if self.mode=="follow" and self.movement_command~=nil then return "success",nil end
  self:_cancel_movement("follow_replace")
  local ok,command=self.safe:call("AIFollowTargetCommand",function() local value=NewObject("handle:AIFollowTargetCommand"); value.desiredDistance=self.config.follow_distance; value.matchSpeed=true; value.stopWhenDestinationReached=false; value.target=player; value.movementType=self.config.movement_type; value.teleport=false; value.tolerance=2; value.lookAtTarget=player; entity:GetAIControllerComponent():SendCommand(value); return value end)
  if not ok then return "error","follow_failed" end
  self.movement_command=command; self.mode="follow"; self.logger:trace("npc_follow_started","teleport=false"); return "success",nil
end

function NpcEntity:stay() self:_cancel_movement("stay"); self.mode=self.entity_id and "stay" or "absent"; return "success",nil end
function NpcEntity:come_here()
  local player,entity=self:_command_entity(); if entity==nil then return "unavailable","resolved_npc_puppet_required" end
  local target=self:_nearby_position(player); if target==nil then return "unavailable","target_unavailable" end
  self:_cancel_movement("come_here_replace")
  local ok,command=self.safe:call("AIMoveToCommand",function() local world=NewObject("WorldPosition"); world:SetVector4(world,target); local spec=NewObject("AIPositionSpec"); spec:SetWorldPosition(spec,world); local value=NewObject("handle:AIMoveToCommand"); value.movementTarget=spec; value.rotateEntityTowardsFacingTarget=false; value.ignoreNavigation=false; value.desiredDistanceFromTarget=2; value.movementType=self.config.movement_type; value.finishWhenDestinationReached=true; value.alwaysUseStealth=false; entity:GetAIControllerComponent():SendCommand(value); return value end)
  if not ok then return "error","come_here_failed" end
  self.movement_command=command; self.mode="come_here"; self.logger:trace("npc_come_here_started","ignoreNavigation=false"); return "success",nil
end
function NpcEntity:look_at_player()
  local player,entity=self:_command_entity(); if entity==nil then return "unavailable","resolved_npc_puppet_required" end
  if self.look_at_active then return "success",nil end
  local ok=self.safe:call("ReactionManager.ActivateReactionLookAt",function() local reaction=entity:FindComponentByName("ReactionManager"); if not reaction then error("reaction unavailable") end; reaction:ActivateReactionLookAt(player,false,1,true,true); return true end)
  if not ok then return "error","look_at_failed" end
  self.look_at_active=true; self.logger:trace("npc_look_at_started","target=player"); return "success",nil
end
function NpcEntity:clear_look_at() self:_clear_look_at(); return "success",nil end

function NpcEntity:execute(command,now_ms)
  local handlers={spawn=function() return self:spawn(now_ms) end,despawn=function() return self:cleanup("despawn") end,follow=function() return self:follow() end,stay=function() return self:stay() end,come_here=function() return self:come_here() end,look_at_player=function() return self:look_at_player() end,clear_look_at=function() return self:clear_look_at() end,status=function() return "success",nil end}
  local handler=handlers[command]; if handler==nil then self.logger:trace("npc_command_rejected","reason=unknown_command"); return "rejected","unknown_command" end
  self.last_command=command; local result,error=handler(); if result~="pending" then self.last_result=result; self.last_error=error end; return result,error
end

function NpcEntity:update_lifecycle()
  local player=self:_session()
  if player then
    self.had_player=true
    if self.entity_id~=nil and self:_system(false)==nil then self:cleanup("dynamic_entity_system_unavailable"); return true end
    return false
  end
  if self.had_player or self.entity_id~=nil then self.had_player=false; self:cleanup("missing_player"); return true end
  return false
end

function NpcEntity:status(backend_connected)
  local player=self:_session(); local system=self:_system(false); local ready=nil; local managed=nil; local spawned=nil; local resolved=self.entity~=nil
  if system then local ok,value=self.safe:call("DynamicEntitySystem.IsReady",function() return system:IsReady() end); ready=ok and value or nil; if self.entity_id then local mok,m=self.safe:call("DynamicEntitySystem.IsManaged",function() return system:IsManaged(self.entity_id) end); managed=mok and m or nil; local sok,s=self.safe:call("DynamicEntitySystem.IsSpawned",function() return system:IsSpawned(self.entity_id) end); spawned=sok and s or nil end end
  if self.entity then self:_read_appearance(self.entity) end
  return {enabled=self.config.enabled,backend_connected=backend_connected,player_available=player~=nil,dynamic_entity_system_ready=ready,entity_exists=self.entity_id~=nil,managed=managed,spawned=spawned,resolved=resolved,runtime_class=runtime_class(self.entity),entity_record=self.config.entity_record,appearance=self.config.appearance,current_appearance=self.current_appearance,temporary_appearance=false,temporary_body_record=self.config.entity_record,mode=self.mode,movement_active=self.movement_command~=nil,look_at_active=self.look_at_active,last_command=self.last_command,last_command_result=self.last_result,last_error=self.last_error}
end

return NpcEntity
