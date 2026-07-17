local StateMachine={}
StateMachine.__index=StateMachine
local allowed={disabled=true,unavailable=true,absent=true,spawning=true,idle=true,following=true,staying=true,returning=true,conversing=true,suspended=true,cleaning_up=true,error=true}
function StateMachine.new(config,logger,entity,subtitle)
  return setmetatable({config=config,logger=logger,entity=entity,subtitle=subtitle,state=config.npc_presence_enabled and "absent" or "disabled",requested_mode=config.npc_auto_follow and "follow" or "stay",previous_mode="stay",suspended_reason=nil,speech_restore_suspended=false,active_utterance=nil,utterance_until_ms=0,session_since_ms=nil,auto_spawn_attempted=false,cleanup_reason=nil,last_distance=nil,last_progress_ms=0,next_distance_sample_ms=0,stuck=false},StateMachine)
end
function StateMachine:_transition(next_state,reason)
  if not allowed[next_state] or self.state==next_state then return end
  self.logger:trace("npc_state_transition","from="..self.state.." to="..next_state.." reason="..tostring(reason)); self.state=next_state
end
function StateMachine:_configure(payload)
  if type(payload)~="table" then return "rejected","invalid_settings" end
  local booleans={"npc_presence_enabled","npc_auto_spawn","npc_auto_follow","npc_rescue_enabled","npc_suspend_during_combat","npc_suspend_in_vehicle","npc_in_game_subtitles_enabled","npc_voice_embodiment_enabled"}
  for _,key in ipairs(booleans) do if type(payload[key])~="boolean" then return "rejected","invalid_settings" end end
  if payload.npc_rescue_enabled then return "rejected","rescue_not_live_validated" end
  self.config.npc_presence_enabled=payload.npc_presence_enabled; self.config.npc_auto_spawn=payload.npc_auto_spawn; self.config.npc_auto_follow=payload.npc_auto_follow
  self.config.spawn_delay_seconds=math.min(math.max(tonumber(payload.npc_spawn_delay_seconds) or 3,1),30); self.config.follow_distance=math.min(math.max(tonumber(payload.npc_follow_distance) or 2.5,1.5),8); self.config.return_distance=math.min(math.max(tonumber(payload.npc_return_distance) or 12,5),40); self.config.rescue_distance=math.min(math.max(tonumber(payload.npc_rescue_distance) or 40,15),100); self.config.rescue_enabled=false
  self.config.suspend_during_combat=payload.npc_suspend_during_combat; self.config.suspend_in_vehicle=payload.npc_suspend_in_vehicle; self.config.in_game_subtitles_enabled=payload.npc_in_game_subtitles_enabled; self.config.voice_embodiment_enabled=payload.npc_voice_embodiment_enabled
  if not self.config.npc_presence_enabled then self:handle("despawn",nil,"system",0); self:_transition("disabled","settings") elseif self.state=="disabled" then self:_transition("absent","settings") end
  return "success",nil
end
function StateMachine:handle(command,payload,origin,now_ms)
  if origin=="llm" then return "rejected","llm_origin_forbidden" end
  if command=="configure" then return self:_configure(payload) end
  if command=="spawn" then if self.state~="absent" and self.state~="disabled" and self.entity.entity_id~=nil then return "success",nil end; self:_transition("spawning","spawn"); local r,e=self.entity:spawn(now_ms); if r=="success" then self:_transition("idle","spawned"); if self.config.npc_auto_follow then self:handle("follow",nil,"system",now_ms) end elseif r~="pending" then self:_transition(r=="unavailable" and "unavailable" or "error",e) end; return r,e end
  if command=="despawn" then self:_transition("cleaning_up","despawn"); self.subtitle:clear(); self.active_utterance=nil; self.utterance_until_ms=0; self.stuck=false; local r,e=self.entity:cleanup("despawn"); self.cleanup_reason="despawn"; self:_transition(self.config.npc_presence_enabled and "absent" or "disabled","cleanup"); return r,e end
  if command=="follow" then self.requested_mode="follow"; local r,e=self.entity:follow(); if r=="success" then self:_transition("following","follow") end; return r,e end
  if command=="stay" then self.requested_mode="stay"; local r,e=self.entity:stay(); self:_transition(self.entity.entity_id and "staying" or "absent","stay"); return r,e end
  if command=="come_here" then self.requested_mode="follow"; local r,e=self.entity:come_here(); if r=="success" then self:_transition("returning","come_here") end; return r,e end
  if command=="look_at_player" then return self.entity:look_at_player() end
  if command=="clear_look_at" then return self.entity:clear_look_at() end
  if command=="suspend" then self.previous_mode=self.requested_mode; self.suspended_reason=type(payload)=="table" and payload.reason or "system"; self.entity:stay(); self:_transition("suspended",self.suspended_reason); return "success",nil end
  if command=="resume" then self.suspended_reason=nil; local mode=type(payload)=="table" and payload.mode or self.previous_mode; if mode=="follow" then return self:handle("follow",nil,"system",now_ms) end; return self:handle("stay",nil,"system",now_ms) end
  if command=="speech_start" then if type(payload)~="table" or type(payload.utterance_id)~="string" or #payload.utterance_id>64 then return "rejected","invalid_utterance" end; if self.entity.entity==nil then return "unavailable","resolved_body_required" end; if self.active_utterance and self.active_utterance~=payload.utterance_id then return "rejected","utterance_overlap" end; self.speech_restore_suspended=self.state=="suspended"; self.previous_mode=self.requested_mode; self.entity:stay(); self.entity:look_at_player(); self.active_utterance=payload.utterance_id; self.utterance_until_ms=now_ms+math.min(math.max(tonumber(payload.duration_ms) or 1000,250),30000)+2000; if payload.subtitles_enabled then self.subtitle:show(payload.subtitle,payload.duration_ms,now_ms) end; self:_transition("conversing","speech"); return "success",nil end
  if command=="speech_end" then if type(payload)=="table" and self.active_utterance and payload.utterance_id~=self.active_utterance then return "rejected","stale_utterance" end; self.subtitle:clear(); self.entity:clear_look_at(); self.active_utterance=nil; self.utterance_until_ms=0; if self.speech_restore_suspended then self.speech_restore_suspended=false; self.entity:stay(); self:_transition("suspended","speech_complete"); return "success",nil end; local mode=type(payload)=="table" and payload.restore_mode or self.previous_mode; if mode=="follow" then return self:handle("follow",nil,"speech",now_ms) end; return self:handle("stay",nil,"speech",now_ms) end
  if command=="status" then return "success",nil end
  return "rejected","unknown_command"
end
function StateMachine:update(now_ms)
  self.subtitle:update(now_ms)
  if self.active_utterance and now_ms>=self.utterance_until_ms then self:handle("speech_end",{utterance_id=self.active_utterance,restore_mode=self.previous_mode},"system",now_ms); self.logger:trace("npc_speech_cleanup","reason=max_lifetime") end
  local cleaned=self.entity:update_lifecycle(); if cleaned then self.subtitle:clear(); self.active_utterance=nil; self.cleanup_reason="session_cleanup"; self.session_since_ms=nil; self.auto_spawn_attempted=false; self:_transition(self.config.npc_presence_enabled and "absent" or "disabled","session_cleanup"); return true end
  local player=self.entity:_session()
  if player then if self.session_since_ms==nil then self.session_since_ms=now_ms end else self.session_since_ms=nil; self.auto_spawn_attempted=false end
  -- Auto-spawn eligibility is derived by the backend from existing vehicle/combat
  -- telemetry. CET still re-validates player, pre-game, DES ready/restored and the
  -- duplicate tag when the semantic spawn command arrives.
  if self.state=="following" and now_ms>=self.next_distance_sample_ms then local distance=self.entity:distance_to_player(); self.next_distance_sample_ms=now_ms+1000; if distance then if self.last_distance==nil or self.last_distance-distance>=0.25 or distance<=self.config.return_distance then self.last_progress_ms=now_ms; self.stuck=false elseif distance>self.config.return_distance and now_ms-self.last_progress_ms>=self.config.rescue_stuck_seconds*1000 then if not self.stuck then self.logger:trace("npc_stuck_detected","distance="..tostring(distance).." rescue=false") end; self.stuck=true end; self.last_distance=distance end end
  local result,error=self.entity:update_pending(now_ms); if result then if result=="success" then self:_transition("idle","spawn_validated"); if self.config.npc_auto_follow then self:handle("follow",nil,"system",now_ms) end else self:_transition("error",error) end end
  return false,result,error
end
function StateMachine:backend_disconnected(now_ms)
  if self.active_utterance then self:handle("speech_end",{utterance_id=self.active_utterance,restore_mode=self.previous_mode},"system",now_ms); self.logger:trace("npc_speech_cleanup","reason=backend_disconnected") end
end
function StateMachine:status(connected)
  local value=self.entity:status(connected); value.lifecycle_state=self.state; value.requested_mode=self.requested_mode; value.effective_mode=self.state; value.conversation_state=self.active_utterance and "speaking" or "idle"; value.active_utterance_id=self.active_utterance; value.subtitle_visible=self.subtitle.visible; value.suspended_reason=self.suspended_reason; value.cleanup_reason=self.cleanup_reason; value.rescue_state=self.config.rescue_enabled and "armed" or "disabled_unvalidated"; value.stuck=self.stuck; return value
end
return StateMachine
