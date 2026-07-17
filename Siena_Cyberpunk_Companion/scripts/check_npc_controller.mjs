import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";

const root=path.resolve(import.meta.dirname,"..");
const require=createRequire(path.join(root,"frontend","package.json"));
const luaparse=require("luaparse");
const mod=path.join(root,"bridge","cet","siena_npc_controller");
const files=fs.readdirSync(mod).filter(name=>name.endsWith(".lua")).sort();
const source=new Map(files.map(name=>[name,fs.readFileSync(path.join(mod,name),"utf8")]));
const joined=[...source.values()].join("\n");
for(const [name,text] of source){try{luaparse.parse(text,{luaVersion:"5.1",locations:true})}catch(error){throw new Error(`${name}: ${error.message}`)}}

for(const [pattern,label] of [
  [/WebSocket|\bHttpClient\s*[.:]|require\s*\(["'][^"']*(http|socket)/i,"second transport/client library"],
  [/GiveItem|RemoveItem|EquipItem|UnequipItem|TransactionSystem/i,"inventory write"],
  [/[.:]Teleport\s*\(\s*player|TeleportPlayer|AITeleportCommand/i,"player teleport"],
  [/SetFact|JournalManager|QuestsSystem|ChangeEntryState/i,"quest write"],
  [/TweakDB\s*[:.]\s*(Set|Create|Clone|Delete|Update)/i,"TweakDB mutation"],
  [/AttackCommand|CombatCommand|WeaponCommand|DrawWeapon|StartCombat/i,"combat/weapon command"],
  [/GetEntities|GetAllEntities|GetNPCs|GetTargets|GetNearby|GetTargetingSystem/i,"world scan"],
  [/loadstring|\bload\s*\(/i,"arbitrary code dispatch"],
  [/persistState\s*=\s*true|persistSpawn\s*=\s*true|alwaysSpawned\s*=\s*true/i,"persistence"],
]) if(pattern.test(joined)) throw new Error(`forbidden NPC controller pattern (${label}): ${pattern}`);

const entity=source.get("npc_entity.lua")??""; const init=source.get("init.lua")??""; const client=source.get("npc_client.lua")??"";
for(const [evidence,label] of [
  ['entity_record = "Siena.SienaCompanion"','stable Siena record'],
  ['TweakDB.GetRecord','standalone record existence check'],
  ['standalone_record_unavailable','fail-closed missing record result'],
  ['GetCurrentAppearanceName','runtime appearance read'],
  ['appearance_validation_failed','requested appearance validation'],
  ['current_appearance=self.current_appearance','runtime appearance status'],
  ['temporary_appearance=false','packaged appearance status'],
  ['npc_presence_enabled = false','safe presence default'],
  ['rescue_enabled = false','safe rescue default'],
  ['GetTaggedID','one-entity duplicate guard'],
  ['npc_duplicate_blocked','duplicate trace'],
  ['resolved_npc_puppet_required','unresolved movement block'],
  ['self.mode=="follow" and self.movement_command~=nil','idempotent follow'],
  ['if self.look_at_active then return "success",nil end','idempotent look-at'],
  ['function NpcEntity:clear_look_at()','harmless clear look-at'],
  ['function NpcEntity:stay()','harmless stay'],
  ['already_missing=','harmless despawn'],
  ['dynamic_entity_system_unavailable','system-loss cleanup'],
  ['Observe("PlayerPuppet","OnDetach"','player detach cleanup'],
  ['registerForEvent("onShutdown"','shutdown cleanup'],
  ['returned_id','CreateEntity return path'],
]) if(!joined.includes(evidence)) throw new Error(`missing safety evidence: ${label}`);
if(!/spec\.persistState=false/.test(entity)||!/spec\.persistSpawn=false/.test(entity)||!/spec\.alwaysSpawned=false/.test(entity)) throw new Error("non-persistence flags missing");
if((entity.match(/CreateEntity\(/g)??[]).length!==1) throw new Error("controller must have exactly one CreateEntity call");
if(!/local record_available, record_id = self:_record_available\(\); if not record_available then[\s\S]{0,240}return "unavailable", "standalone_record_unavailable"/.test(entity)) throw new Error("CreateEntity must be gated by standalone record availability");
if(/spec\.appearanceName|DynamicEntitySpec[\s\S]{0,200}appearanceName/.test(entity)) throw new Error("unconfirmed DynamicEntitySpec appearance field is forbidden");
if(/value\.(record|record_id|position|entity_id|class|method|parameters)/i.test(client)) throw new Error("HTTP response may not supply game identifiers or methods");
const onInit=init.match(/registerForEvent\("onInit"[\s\S]*?\nend\)/)?.[0]??"";
if(/:spawn\(|CreateEntity/.test(onInit)) throw new Error("automatic spawn during onInit is forbidden");
if(!/local allowed=\{spawn=true,despawn=true,follow=true,stay=true,come_here=true,look_at_player=true,clear_look_at=true,status=true,configure=true,suspend=true,resume=true,speech_start=true,speech_end=true\}/.test(client)) throw new Error("fixed command allowlist missing");
if(!joined.includes('origin=="llm"')||!joined.includes('utterance_overlap'))throw new Error('origin or utterance arbitration missing');
if(!joined.includes('registerHotkey("siena_toggle_presence"')||!joined.includes('registerForEvent("onDraw"'))throw new Error('in-game controls or subtitle draw missing');
if(!joined.includes('npc_stuck_detected')||!joined.includes('rescue=false')||!joined.includes('backend_disconnected'))throw new Error('bounded stuck or disconnect cleanup missing');
if((joined.match(/CreateEntity\(/g)??[]).length!==1)throw new Error('one entity creation path required');
if((joined.match(/AsyncHttpClient/g)??[]).length<2||!client.includes("in_flight")) throw new Error("bounded existing RedHttpClient path missing");
const frontendHook=fs.readFileSync(path.join(root,"frontend","src","useObserver.ts"),"utf8");
const frontendAll=fs.readdirSync(path.join(root,"frontend","src")).filter(name=>name.endsWith(".ts")||name.endsWith(".tsx")).map(name=>fs.readFileSync(path.join(root,"frontend","src",name),"utf8")).join("\n");
if((frontendAll.match(/new WebSocket\s*\(/g)??[]).length!==1) throw new Error("frontend must retain exactly one WebSocket construction");
if(!frontendHook.includes("JSON.stringify({command})")||/npc\/commands[\s\S]{0,200}(record_id|position|entity_id|game_method|parameters)/i.test(frontendHook)) throw new Error("frontend NPC request boundary is not command-only");
const packageJson=fs.readFileSync(path.join(root,"frontend","package.json"),"utf8");
if(/axios|socket\.io|ws["']\s*:|node-fetch/i.test(packageJson)) throw new Error("new frontend HTTP/WebSocket dependency detected");
const llmFiles=["contextual_companion.py","reaction_dispatch.py","reaction_planner.py","siena_core_reaction_provider.py"];
for(const name of llmFiles){const text=fs.readFileSync(path.join(root,"backend","app","services",name),"utf8");if(/npc_commands|\/api\/v1\/npc/i.test(text)) throw new Error(`LLM/reaction path may not access NPC queue: ${name}`)}
console.log(`NPC controller static checks passed for ${files.length} Lua files.`);
