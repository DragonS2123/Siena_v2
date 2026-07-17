local root = ""

local config = dofile(root .. "config.lua")
local Logger = dofile(root .. "logger.lua")
local SafeProbe = dofile(root .. "safe_probe.lua")
local Probes = dofile(root .. "probes.lua")

local logger = Logger.new(config)
local safe = SafeProbe.new(config, logger)
local probes = Probes.new(config, logger, safe)

local commands = {
  "probe_player",
  "probe_stats",
  "probe_stat_pools",
  "probe_development",
  "probe_equipment",
  "probe_inventory",
  "probe_cyberware",
  "probe_quickhacks",
  "probe_equipment_areas",
  "probe_cyberdeck_identity",
  "probe_cyberdeck_slots",
  "probe_quickhack_programs",
  "probe_quickhack_metadata",
  "probe_weapons",
  "probe_status_effects",
  "probe_quests",
  "probe_vehicle",
  "probe_target",
  "probe_nearby_entities"
}

registerForEvent("onInit", function()
  logger:info(
    "init",
    "loaded; enabled="
      .. tostring(config.enabled)
      .. "; no HTTP, backend, game writes or automatic probes"
  )
end)

for _, name in ipairs(commands) do
  local currentName = name

  registerHotkey(
    "siena_research_" .. currentName,
    "Siena Research: " .. currentName,
    function()
      safe:run(currentName, function()
        probes[currentName](probes)
      end)
    end
  )
end

return {
  enabled = config.enabled,
  commands = commands
}
