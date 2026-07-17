import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const root = path.resolve(import.meta.dirname, "..");
const require = createRequire(path.join(root, "frontend", "package.json"));
const luaparse = require("luaparse");
const modRoot = path.join(root, "bridge", "cet", "siena_npc_research");
const files = fs.readdirSync(modRoot).filter((name) => name.endsWith(".lua")).sort();
const sources = new Map(files.map((name) => [name, fs.readFileSync(path.join(modRoot, name), "utf8")]));
const joined = [...sources.values()].join("\n");

const asts = new Map();
for (const [name, source] of sources) {
  asts.set(name, luaparse.parse(source, { luaVersion: "5.1", locations: true }));
}

const forbidden = [
  [/AsyncHttpClient|RedHttpClient|\bHttpClient\s*[.:]|WebSocket\s*[.:]|\/api\//i, "network client or backend endpoint"],
  [/GiveItem|RemoveItem|AddItem|SetItem|EquipItem|UnequipItem|TransactionSystem/i, "inventory mutation"],
  [/GetTeleportationFacility|AITeleportCommand|[.:]Teleport\s*\(|SetPlayerPosition|player[^\n]{0,80}:SetPosition\s*\(/i, "player or entity teleport"],
  [/SetFact|JournalManager|QuestsSystem|ChangeEntryState/i, "quest or journal mutation"],
  [/TweakDB\s*[:.]\s*(Set|Create|Clone|Delete|Update)|TweakDBInterface\s*\.\s*(Set|Create|Clone|Delete|Update)/i, "TweakDB mutation"],
  [/AttackCommand|CombatCommand|WeaponCommand|VehicleCommand|DrawWeapon|StartCombat/i, "combat, weapon, or vehicle command"],
  [/GetEntities|GetAllEntities|GetNPCs|GetTargets|GetTargetingSystem|FindEntities|GetNearby/i, "unrestricted world or target scan"],
  [/persistState\s*=\s*true|persistSpawn\s*=\s*true|alwaysSpawned\s*=\s*true/i, "persistent entity setting"],
];
for (const [pattern, label] of forbidden) {
  if (pattern.test(joined)) throw new Error(`forbidden NPC research pattern (${label}): ${pattern}`);
}

let createCalls = 0;
function walk(node, ancestors = []) {
  if (!node || typeof node !== "object") return;
  if (node.type === "WhileStatement" || node.type === "RepeatStatement") {
    throw new Error(`${node.loc.start.line}: unbounded loop form is forbidden`);
  }
  if (node.type === "CallExpression" || node.type === "TableCallExpression" || node.type === "StringCallExpression") {
    const base = node.base;
    const method = base?.type === "MemberExpression" ? base.identifier?.name : base?.name;
    if (method === "CreateEntity") {
      createCalls += 1;
      if (ancestors.some((item) => item.type === "ForNumericStatement" || item.type === "ForGenericStatement")) {
        throw new Error(`${node.loc.start.line}: CreateEntity inside a loop is forbidden`);
      }
    }
  }
  for (const value of Object.values(node)) {
    if (Array.isArray(value)) value.forEach((child) => child?.type && walk(child, [...ancestors, node]));
    else if (value?.type) walk(value, [...ancestors, node]);
  }
}
for (const ast of asts.values()) walk(ast);

if (createCalls !== 1) throw new Error(`expected exactly one bounded CreateEntity call, found ${createCalls}`);
if (!/enabled\s*=\s*false/.test(joined)) throw new Error("research mod must default to enabled=false");
if (/registerForEvent\(["']onUpdate/.test(joined)) throw new Error("automatic polling is forbidden");
if (!joined.includes("GetTaggedID") || !joined.includes("IsManaged")) throw new Error("duplicate/tag guards are missing");
if (!joined.includes("DeleteEntity") || !joined.includes("StopExecutingCommand")) throw new Error("cleanup paths are missing");
if (!joined.includes("persistState = false") || !joined.includes("persistSpawn = false") || !joined.includes("alwaysSpawned = false")) {
  throw new Error("non-persistence defaults are missing");
}
const init = sources.get("init.lua") ?? "";
const npc = sources.get("npc_research.lua") ?? "";
const onInit = init.match(/registerForEvent\(["']onInit["'][\s\S]*?\nend\)/)?.[0] ?? "";
if (/CreateEntity|research:spawn\s*\(/.test(onInit)) throw new Error("automatic spawn during onInit is forbidden");
if (/player:IsPreGame\s*\(/.test(npc)) throw new Error("IsPreGame must not be called on the player runtime object");
for (const [evidence, label] of [
  ['session_available = available and pre_game ~= true', 'nullable pre-game session fallback'],
  ['Game.GetSystemRequestsHandler', 'confirmed pre-game handler path'],
  ['spawn_guard=no_player', 'main-menu/no-player spawn block'],
  ['dynamic_entity_system=false', 'missing DynamicEntitySystem block'],
  ['dynamic_entity_system_ready=false', 'not-ready block'],
  ['dynamic_entity_system_restored=false', 'not-restored block'],
  ['spawn_transform_unavailable', 'transform-unavailable block'],
  ['orientation_unavailable', 'orientation-unavailable block'],
  ['PlayerPuppet.GetWorldOrientation', 'direct player quaternion path'],
  ['returned_id_used_directly=true', 'direct CreateEntity EntityID path'],
  ['self.entity_id = id', 'stable returned EntityID storage'],
  ['duplicate_blocked', 'duplicate guard'],
  ['resolved_npc_puppet_required', 'command block without resolved NPC'],
]) {
  if (!npc.includes(evidence)) throw new Error(`missing v0.9.0.1 safety evidence: ${label}`);
}
if (/EulerAngles\.ToQuat|entEntityID/.test(npc)) throw new Error("deprecated guessed transform/EntityID helper path remains");

console.log(`NPC research safety checks passed for ${files.length} Lua files.`);
