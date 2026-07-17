import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const root = path.resolve(import.meta.dirname, "..");
const require = createRequire(path.join(root, "frontend", "package.json"));
const luaparse = require("luaparse");
const mod = path.join(root, "bridge", "cet", "siena_game_state_research");
const files = fs.readdirSync(mod).filter((name) => name.endsWith(".lua")).sort();
const joined = files.map((name) => fs.readFileSync(path.join(mod, name), "utf8")).join("\n");

for (const name of files) {
  luaparse.parse(fs.readFileSync(path.join(mod, name), "utf8"), { luaVersion: "5.1" });
}

const required = [
  "probe_player", "probe_stats", "probe_stat_pools", "probe_development",
  "probe_equipment", "probe_inventory", "probe_cyberware", "probe_quickhacks",
  "probe_equipment_areas", "probe_cyberdeck_identity", "probe_cyberdeck_slots",
  "probe_quickhack_programs", "probe_quickhack_metadata",
  "probe_weapons", "probe_status_effects", "probe_quests", "probe_vehicle",
  "probe_target", "probe_nearby_entities"
];
for (const name of required) {
  if (!joined.includes(name)) throw new Error(`missing research probe: ${name}`);
}

const forbidden = [
  /AsyncHttpClient|HttpClient\.|RedHttpClient|\/api\//,
  /GiveItem|RemoveItem|SetFactStr|ChangeEntryState|ApplyStatusEffect|RemoveStatusEffect/,
  /Spawn|Despawn|Kill\(|Teleport\(|EquipItem|UnequipItem|SetAttribute|AddDevelopmentPoints/,
  /io\.open|io\.output|os\.execute|loadfile|:GetItemList\s*\(/,
  /registerForEvent\(["']onUpdate|registerForEvent\(["']onDraw/,
  /debug\.getinfo|DumpType|GameDump|\bDump\(/,
  /item:GetRecordID|TweakDB:(Set|Create|Clone|Delete|Update)|TweakDBInterface\.(Set|Create|Clone|Delete|Update)/
  , /return\s+GetSlotsForCyberdeckFromItemData\s*\(|return\s+GetPlayerQuickHackInCyberDeck\s*\(/
];
for (const pattern of forbidden) {
  if (pattern.test(joined)) throw new Error(`forbidden research Lua pattern: ${pattern}`);
}
if (!/enabled\s*=\s*false/.test(joined)) throw new Error("research mod must default disabled");
if (!joined.includes("pcall")) throw new Error("research probes must recover with pcall");
if (!joined.includes("minimum_probe_interval_ms")) throw new Error("research probes need a rate limit");
if (!joined.includes("math.min")) throw new Error("research collections must be bounded");
if (!joined.includes("GetItemsInArea") || !joined.includes("GetItemParts")) throw new Error("cyberdeck research paths are missing");
if (joined.includes('registerForEvent("onUpdate"') || joined.includes("registerForEvent('onUpdate'")) throw new Error("research cannot poll automatically");

console.log(`Research Lua static checks passed for ${files.length} files.`);
