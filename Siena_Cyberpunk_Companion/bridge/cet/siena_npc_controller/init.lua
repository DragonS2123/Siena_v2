local Config=dofile("config.lua")
local Logger=dofile("logger.lua")
local SafeCall=dofile("safe_call.lua")
local NpcEntity=dofile("npc_entity.lua")
local NpcClient=dofile("npc_client.lua")
local StateMachine=dofile("state_machine.lua")
local SubtitleOverlay=dofile("subtitle_overlay.lua")
local logger=Logger.new(); local safe=SafeCall.new(logger); local entity=NpcEntity.new(Config,logger,safe); local subtitle=SubtitleOverlay.new(Config); local machine=StateMachine.new(Config,logger,entity,subtitle)
local app={elapsed_ms=0,initialized=false,entity=entity,machine=machine,subtitle=subtitle,client=nil,backend_was_connected=false}

local function execute(command,payload,origin,now_ms) return machine:handle(command,payload,origin,now_ms) end
local function status(connected) return machine:status(connected) end
local function local_command(command) if app.initialized then local result,error=machine:handle(command,nil,"player",app.elapsed_ms); logger:trace("npc_hotkey","command="..command.." result="..tostring(result).." error="..tostring(error)) end end

registerHotkey("siena_toggle_presence","Toggle Siena presence",function() if machine.entity.entity_id then local_command("despawn") else Config.npc_presence_enabled=true; machine:_transition("absent","hotkey"); local_command("spawn") end end)
registerHotkey("siena_follow","Siena: Follow",function() local_command("follow") end)
registerHotkey("siena_stay","Siena: Stay",function() local_command("stay") end)
registerHotkey("siena_come_here","Siena: Come Here",function() local_command("come_here") end)
registerHotkey("siena_talk_focus","Siena: Talk / focus",function() local_command("look_at_player"); logger:trace("npc_talk_focus","conversation_entry=external_existing_voice_ui") end)
registerHotkey("siena_dismiss","Siena: Dismiss",function() local_command("despawn") end)
registerHotkey("siena_status","Siena: Show status",function() local value=machine:status(app.client and app.client.connected or false); logger:trace("npc_status","state="..tostring(value.lifecycle_state).." body="..tostring(value.resolved).." mode="..tostring(value.effective_mode)) end)

registerForEvent("onInit",function()
  local valid,error=Config.validate(); if not valid then logger:trace("npc_command_rejected","reason=invalid_config error="..tostring(error)); return end
  app.client=NpcClient.new(Config,logger,execute,status); app.client:start(); app.initialized=true
  logger:trace("npc_controller_initialized","enabled="..tostring(Config.enabled).." auto_spawn="..tostring(Config.npc_auto_spawn).." record="..Config.entity_record)
  pcall(function() Observe("PlayerPuppet","OnDetach",function() if app.initialized then machine:handle("despawn",nil,"system",app.elapsed_ms) end end) end)
end)

registerForEvent("onUpdate",function(delta_time)
  if not app.initialized then return end
  app.elapsed_ms=app.elapsed_ms+math.max(0,(delta_time or 0)*1000)
  local cleaned,result,error=machine:update(app.elapsed_ms); if cleaned and app.client.current_command then app.client:complete_current("error","session_cleanup") elseif result and app.client.current_command then app.client:complete_current(result,error) end
  local player=select(1,entity:_session())
  app.client:update(app.elapsed_ms,player~=nil)
  if app.backend_was_connected and not app.client.connected then machine:backend_disconnected(app.elapsed_ms) end
  app.backend_was_connected=app.client.connected
end)

registerForEvent("onDraw",function() if app.initialized then subtitle:draw() end end)

registerForEvent("onShutdown",function()
  if app.initialized then machine:handle("despawn",nil,"system",app.elapsed_ms) end
  if app.client then app.client:shutdown() end
  app.initialized=false
end)

return app
