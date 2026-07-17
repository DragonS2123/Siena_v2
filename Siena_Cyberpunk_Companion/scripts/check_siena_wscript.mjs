import fs from 'node:fs';
import path from 'node:path';

const root=path.resolve(import.meta.dirname,'..');
const file=path.join(root,'wolvenkit','siena_npc','scripts','CreateSienaStandaloneAssets.wscript');
const source=fs.readFileSync(file,'utf8');
const parseable=source.replace(/^import .*;\s*$/m,'');
new Function(parseable);

for(const [evidence,label] of [
  ["const BASE_ENTITY_PATH = 'ep1\\\\characters\\\\entities\\\\citizen\\\\citizen__ep1_rich_wa.ent';",'confirmed source entity'],
  ["const BASE_APPEARANCE_PATH = 'base\\\\characters\\\\appearances\\\\citizen\\\\citizen__rich_wa.app';",'confirmed source appearance'],
  ["const SIENA_ENTITY_PATH = 'siena\\\\entities\\\\siena_default.ent';",'Siena target entity'],
  ["const SIENA_APPEARANCE_PATH = 'siena\\\\appearances\\\\siena_default.app';",'Siena target appearance'],
  ['const SAFE_REPLACE = false','collision-safe default'],
  ['FileExistsInProject(destination) && !SAFE_REPLACE','destination collision guard'],
  ['SaveToRaw(`siena_authoring_backups/','raw replacement backup'],
  ['Restored previous destination from memory','rollback path'],
  ['appearanceAppearanceResource','appearance type validation'],
  ['entEntityTemplate','entity type validation'],
  ['entity_entry_display_label=','display-label diagnostic'],
  ['entity_appearance_name=','derived appearance-name diagnostic'],
  ['entity_appearance_resource=','entity resource diagnostic'],
  ['matched_app_definition_name=','matched definition diagnostic'],
  ['definitionMatches.length!==1','unique app definition guard'],
  ['confirmed_source_fallback=','bounded confirmed-source fallback'],
]) if(!source.includes(evidence)) throw new Error(`WScript missing ${label}`);

const observedCalls=['GetProjectFiles','FileExistsInProject','GetFileFromProject','GameFileToJson','JsonToCR2W','SaveToProject','SaveToRaw'];
for(const call of source.matchAll(/wkit\.([A-Za-z0-9_]+)\s*\(/g))if(!observedCalls.includes(call[1]))throw new Error(`Unconfirmed WKit API used: ${call[1]}`);
for(const forbidden of [/Cyberpunk2077/i,/archive\\pc\\mod/i,/r6\\tweaks/i,/RemoveFromProject|DeleteFile|TweakDB/i])if(forbidden.test(source))throw new Error(`WScript crosses its authoring-only boundary: ${forbidden}`);

const baseEnt='ep1\\characters\\entities\\citizen\\citizen__ep1_rich_wa.ent';
const baseApp='base\\characters\\appearances\\citizen\\citizen__rich_wa.app';
const targetEnt='siena\\entities\\siena_default.ent';
const targetApp='siena\\appearances\\siena_default.app';
const cname=value=>({$type:'CName',$storage:'string',$value:value});
const resource=value=>({DepotPath:{$type:'ResourcePath',$storage:'string',$value:value},Flags:'Soft'});
const mapping=(display,name,app=baseApp)=>({$type:'entTemplateAppearance',name:cname(display),appearanceName:cname(name),appearanceResource:resource(app),untouched:'entity-dependency'});
const definition=name=>({Data:{$type:'appearanceAppearanceDefinition',name:cname(name),components:[{mesh:'preserved.mesh'}]}});
const entityRoot=entries=>({Data:{RootChunk:{$type:'entEntityTemplate',appearances:entries,components:[{name:'preserved'}]}}});
const appRoot=definitions=>({Data:{RootChunk:{$type:'appearanceAppearanceResource',appearances:definitions}}});

function execute(entity,appearance,{destinationExists=false}={}){
  const files=new Map([[baseEnt,structuredClone(entity)],[baseApp,structuredClone(appearance)]]);
  if(destinationExists){files.set(targetEnt,entityRoot([]));files.set(targetApp,appRoot([]));}
  const raw=[];const logs=[];
  const wkit={
    GetProjectFiles:()=>[baseEnt,baseApp],
    FileExistsInProject:value=>files.has(value),
    GetFileFromProject:value=>files.get(value),
    GameFileToJson:value=>JSON.stringify(value),
    JsonToCR2W:value=>JSON.parse(value),
    SaveToProject:(value,data)=>files.set(value,data),
    SaveToRaw:(value,data)=>raw.push({value,data}),
  };
  const Logger={Info:value=>logs.push(String(value)),Warning:value=>logs.push(String(value)),Error:value=>logs.push(String(value)),Success:value=>logs.push(String(value))};
  new Function('wkit','Logger','OpenAs',parseable)(wkit,Logger,{GameFile:'GameFile'});
  return {files,raw,logs};
}

const sourceEntity=entityRoot([mapping('citizen__rich_wa_rich_23_casual','rich_23_casual')]);
const sourceAppearance=appRoot([definition('keep_other'),definition('rich_23_casual')]);
const normal=execute(sourceEntity,sourceAppearance);
const outputEnt=normal.files.get(targetEnt),outputApp=normal.files.get(targetApp);
const outputMapping=outputEnt.Data.RootChunk.appearances[0];
const outputDefinitions=outputApp.Data.RootChunk.appearances.map(item=>item.Data.name.$value);
if('citizen__rich_wa_rich_23_casual'==='rich_23_casual')throw new Error('fixture must prove composite label differs from definition name');
if(outputMapping.appearanceName.$value!=='siena_default'||outputMapping.appearanceResource.DepotPath.$value!==targetApp||outputMapping.name.$value!=='siena_default')throw new Error('entity mapping was not converted to Siena identity');
if(outputDefinitions.filter(value=>value==='siena_default').length!==1||!outputDefinitions.includes('keep_other'))throw new Error('app definition conversion did not preserve unrelated definitions');
for(const evidence of ['entity_entry_display_label=citizen__rich_wa_rich_23_casual','entity_appearance_name=rich_23_casual',`entity_appearance_resource=${baseApp}`,'matched_app_definition_name=rich_23_casual'])if(!normal.logs.some(value=>value.includes(evidence)))throw new Error(`derived mapping log missing: ${evidence}`);
if(normal.raw.length!==2)throw new Error('planned recovery JSON was not retained for both destinations');

const derived=execute(entityRoot([mapping('citizen__rich_wa_rich_23_casual','derived_from_ent')]),appRoot([definition('derived_from_ent')]));
if(!derived.logs.some(value=>value.includes('matched_app_definition_name=derived_from_ent')))throw new Error('normal matching path hardcodes rich_23_casual instead of deriving from .ent');
const fallback=execute(entityRoot([mapping('different_display','rich_23_casual')]),appRoot([definition('rich_23_casual')]));
if(!fallback.logs.some(value=>value.includes('confirmed_source_fallback=rich_23_casual')))throw new Error('confirmed-source fallback was not explicitly logged');

for(const [definitions,expected] of [[[],/found 0/],[[definition('rich_23_casual'),definition('rich_23_casual')],/found 2/]]){
  let error=null;try{execute(sourceEntity,appRoot(definitions))}catch(reason){error=reason}
  if(!error||!expected.test(String(error.message)))throw new Error(`app definition cardinality did not fail closed: ${expected}`);
}
let collision=null;try{execute(sourceEntity,sourceAppearance,{destinationExists:true})}catch(reason){collision=reason}
if(!collision||!/Destination exists/.test(String(collision.message)))throw new Error('destination collision did not fail closed');

console.log('Siena WolvenKit WScript syntax, mapping regression, and static safety checks passed.');
