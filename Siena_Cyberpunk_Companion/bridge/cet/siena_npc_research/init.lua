local config = dofile("config.lua")
local Logger = dofile("logger.lua")
local SafeCall = dofile("safe_call.lua")
local NpcResearch = dofile("npc_research.lua")

local logger = Logger.new(config)
local safe = SafeCall.new(config, logger)
local research = NpcResearch.new(config, logger, safe)

local hotkeys = {
  { "probe_runtime", "Siena NPC Research: Probe Runtime", "gate0", function() research:probe_runtime() end },
  { "spawn", "Siena NPC Research: Spawn", "gate1", function() research:spawn() end },
  { "despawn", "Siena NPC Research: Despawn", "gate1", function() research:despawn("despawn") end },
  { "status", "Siena NPC Research: Status", "gate2", function() research:status() end },
  { "follow", "Siena NPC Research: Follow", "gate3", function() research:follow() end },
  { "stay", "Siena NPC Research: Stay", "gate3", function() research:stay() end },
  { "come_here", "Siena NPC Research: Come Here", "gate3", function() research:come_here() end },
  { "look_at", "Siena NPC Research: Look At Player", "gate4", function() research:look_at_player() end },
  { "clear_look_at", "Siena NPC Research: Clear Look At", "gate4", function() research:clear_look_at() end }
}

for _, item in ipairs(hotkeys) do
  registerHotkey("siena_npc_research_" .. item[1], item[2], function()
    safe:manual(item[3], item[1], item[4])
  end)
end

registerForEvent("onInit", function()
  logger:info("init", "load", "loaded enabled=" .. tostring(config.enabled) .. " automatic_spawn=false http=false websocket=false backend=false")
  local ok, err = pcall(function()
    Observe("PlayerPuppet", "OnDetach", function()
      if config.enabled and research.entity_id ~= nil then research:despawn("player_detach_cleanup") end
    end)
  end)
  if not ok then logger:error("gate5", "register_player_detach", "Observe failed=" .. tostring(err)) end
end)

registerForEvent("onShutdown", function()
  if research.entity_id ~= nil then research:despawn("mod_shutdown_cleanup") end
  logger:info("shutdown", "unload", "cleanup_requested=true")
end)
