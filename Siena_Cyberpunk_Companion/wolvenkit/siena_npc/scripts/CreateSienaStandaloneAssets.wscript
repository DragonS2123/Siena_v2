// @version 1.1
// WolvenKit 8.19.0 project script. It writes only project CR2W resources.
import * as Logger from 'Logger.wscript';

const BASE_ENTITY_PATH = 'ep1\\characters\\entities\\citizen\\citizen__ep1_rich_wa.ent';
const BASE_APPEARANCE_PATH = 'base\\characters\\appearances\\citizen\\citizen__rich_wa.app';
const ENTITY_ENTRY_DISPLAY_LABEL = 'citizen__rich_wa_rich_23_casual';
const CONFIRMED_SOURCE_FALLBACK_NAME = 'rich_23_casual';
const SIENA_ENTITY_PATH = 'siena\\entities\\siena_default.ent';
const SIENA_APPEARANCE_PATH = 'siena\\appearances\\siena_default.app';
const SIENA_APPEARANCE_NAME = 'siena_default';
const SAFE_REPLACE = false; // Explicitly set true only after reviewing raw backups.
const RUN_ID = Date.now().toString(); // Keeps recovery JSON from earlier failed runs.

function fail(message) { Logger.Error(`[SienaAssets] ABORT: ${message}`); throw new Error(message); }
function assertSafeBase(path, extension) {
  const lower=path.toLowerCase();
  if (!lower.endsWith(extension)) fail(`Expected ${extension} path, got ${path}`);
  if (lower.includes('playerpuppet') || lower.includes('player_') || lower.includes('\\quest\\') || lower.includes('judy') || lower.includes('panam') || lower.includes('songbird') || lower.includes('rogue')) fail(`Forbidden player/quest base path: ${path}`);
}
function clone(value) { return JSON.parse(JSON.stringify(value)); }
function redString(value) {
  if (typeof value === 'string') return value;
  if (value && typeof value === 'object' && typeof value.$value === 'string') return value.$value;
  return null;
}
function setRedString(value, replacement, label) {
  if (typeof value === 'string') return replacement;
  if (value && typeof value === 'object' && typeof value.$value === 'string') { value.$value=replacement; return value; }
  fail(`Expected string-backed ${label}.`);
}
function entityAppearances(root) {
  const values=root?.Data?.RootChunk?.appearances;
  if (!Array.isArray(values)) fail('Source .ent has no bounded RootChunk.appearances array.');
  return values;
}
function appDefinitions(root) {
  const values=root?.Data?.RootChunk?.appearances;
  if (!Array.isArray(values)) fail('Source .app has no bounded RootChunk.appearances array.');
  return values;
}
function entityData(entry) { return entry?.Data ?? entry; }
function definitionData(entry) { return entry?.Data ?? entry; }
function entityDisplay(entry) { return redString(entityData(entry)?.name); }
function entityAppearanceName(entry) { return redString(entityData(entry)?.appearanceName); }
function entityAppearanceResource(entry) { return redString(entityData(entry)?.appearanceResource?.DepotPath); }
function definitionName(entry) { return redString(definitionData(entry)?.name); }
function matchingIndexes(values, reader, expected) {
  const matches=[];
  for (let index=0;index<values.length;index+=1) if (reader(values[index])===expected) matches.push(index);
  return matches;
}
function loadProjectJson(path) {
  Logger.Info(`[SienaAssets] Verify/load project CR2W: ${path}`);
  if (!wkit.FileExistsInProject(path)) fail(`Required project source is missing: ${path}`);
  const file=wkit.GetFileFromProject(path,OpenAs.GameFile); if (!file) fail(`Could not deserialize project CR2W: ${path}`);
  Logger.Info(`[SienaAssets] Deserialized project CR2W: ${path}`);
  return JSON.parse(wkit.GameFileToJson(file));
}
function prepare(source,destination,original,converted,expectedType) {
  Logger.Info(`[SienaAssets] Prepare independent copy: ${source} -> ${destination}`);
  if (wkit.FileExistsInProject(destination) && !SAFE_REPLACE) fail(`Destination exists. Review it or set SAFE_REPLACE=true explicitly: ${destination}`);
  if (!JSON.stringify(original).includes(expectedType)) fail(`Unexpected CR2W type for ${source}; expected ${expectedType}`);
  wkit.SaveToRaw(`siena_authoring_backups/${RUN_ID}_planned_${destination.replaceAll('\\','_')}.json`,JSON.stringify(converted));
  Logger.Info(`[SienaAssets] Prepared CR2W JSON and raw recovery copy: ${destination}`);
  return {source,destination,original,converted,expectedType,previous:wkit.FileExistsInProject(destination)?loadProjectJson(destination):null};
}
function save(item) {
  if (item.previous) wkit.SaveToRaw(`siena_authoring_backups/${RUN_ID}_previous_${item.destination.replaceAll('\\','_')}.json`,JSON.stringify(item.previous));
  const output=wkit.JsonToCR2W(JSON.stringify(item.converted)); if (!output) fail(`CR2W conversion failed: ${item.source}`);
  try { Logger.Info(`[SienaAssets] Save binary CR2W through WolvenKit: ${item.destination}`); wkit.SaveToProject(item.destination,output); }
  catch (error) {
    Logger.Error(`[SienaAssets] Save failed: ${item.destination}: ${error}`);
    if (item.previous) { const restore=wkit.JsonToCR2W(JSON.stringify(item.previous)); wkit.SaveToProject(item.destination,restore); Logger.Warning('[SienaAssets] Restored previous destination from memory; raw JSON backup retained.'); }
    else Logger.Warning('[SienaAssets] Destination was new. If a partial file is visible, delete only that Siena destination before retrying.');
    throw error;
  }
  const saved=loadProjectJson(item.destination);if (!JSON.stringify(saved).includes(item.expectedType)) fail(`Saved CR2W type verification failed: ${item.destination}`);
  Logger.Success(`[SienaAssets] Saved and re-read ${item.expectedType}: ${item.destination}`);
  return saved;
}

const projectFiles=wkit.GetProjectFiles('archive');if (!projectFiles || projectFiles.length===0) fail('No WolvenKit project is open or the project archive tree is empty.');
assertSafeBase(BASE_ENTITY_PATH,'.ent');assertSafeBase(BASE_APPEARANCE_PATH,'.app');
Logger.Info(`[SienaAssets] Project open; ${projectFiles.length} archive resources visible.`);
Logger.Info('[SienaAssets] Original ep1/base resources remain untouched; unchanged dependencies remain base-game references.');

const sourceEnt=loadProjectJson(BASE_ENTITY_PATH);
const sourceApp=loadProjectJson(BASE_APPEARANCE_PATH);
const sourceEntityEntries=entityAppearances(sourceEnt);
const sourceDefinitions=appDefinitions(sourceApp);
let entityMatches=matchingIndexes(sourceEntityEntries,entityDisplay,ENTITY_ENTRY_DISPLAY_LABEL);
let confirmedFallback=false;
if (entityMatches.length>1) fail(`Source .ent has multiple entries with display label ${ENTITY_ENTRY_DISPLAY_LABEL}.`);
if (entityMatches.length===0) {
  const exactSources=BASE_ENTITY_PATH==='ep1\\characters\\entities\\citizen\\citizen__ep1_rich_wa.ent' && BASE_APPEARANCE_PATH==='base\\characters\\appearances\\citizen\\citizen__rich_wa.app';
  const fallbackDefinitions=matchingIndexes(sourceDefinitions,definitionName,CONFIRMED_SOURCE_FALLBACK_NAME);
  const fallbackMappings=[];
  for (let index=0;index<sourceEntityEntries.length;index+=1) if (entityAppearanceName(sourceEntityEntries[index])===CONFIRMED_SOURCE_FALLBACK_NAME && entityAppearanceResource(sourceEntityEntries[index])===BASE_APPEARANCE_PATH) fallbackMappings.push(index);
  if (!exactSources || fallbackDefinitions.length!==1 || fallbackMappings.length!==1) fail(`Source .ent lacks unique display mapping ${ENTITY_ENTRY_DISPLAY_LABEL}; confirmed-source fallback refused.`);
  entityMatches=fallbackMappings;confirmedFallback=true;
  Logger.Warning(`[SienaAssets] confirmed_source_fallback=${CONFIRMED_SOURCE_FALLBACK_NAME}`);
}
const entityIndex=entityMatches[0];
const sourceEntityEntry=sourceEntityEntries[entityIndex];
const extractedDisplay=entityDisplay(sourceEntityEntry);
const extractedName=entityAppearanceName(sourceEntityEntry);
const extractedResource=entityAppearanceResource(sourceEntityEntry);
Logger.Info(`[SienaAssets] entity_entry_display_label=${extractedDisplay ?? ENTITY_ENTRY_DISPLAY_LABEL}`);
Logger.Info(`[SienaAssets] entity_appearance_name=${extractedName}`);
Logger.Info(`[SienaAssets] entity_appearance_resource=${extractedResource}`);
if (!extractedName) fail('Selected source .ent mapping has no string-backed appearanceName.');
if (extractedResource!==BASE_APPEARANCE_PATH) fail(`Selected source .ent mapping points to unexpected appearanceResource: ${extractedResource}`);
if (confirmedFallback && extractedName!==CONFIRMED_SOURCE_FALLBACK_NAME) fail('Confirmed-source fallback resolved an unexpected appearanceName.');
const definitionMatches=matchingIndexes(sourceDefinitions,definitionName,extractedName);
if (definitionMatches.length!==1) fail(`Source .app requires exactly one appearanceDefinition named ${extractedName}; found ${definitionMatches.length}.`);
const definitionIndex=definitionMatches[0];
Logger.Info(`[SienaAssets] matched_app_definition_name=${definitionName(sourceDefinitions[definitionIndex])}`);

const convertedApp=clone(sourceApp);
const convertedDefinition=definitionData(appDefinitions(convertedApp)[definitionIndex]);
convertedDefinition.name=setRedString(convertedDefinition.name,SIENA_APPEARANCE_NAME,'appearanceDefinition.name');
const convertedEnt=clone(sourceEnt);
const convertedEntityEntry=entityData(entityAppearances(convertedEnt)[entityIndex]);
convertedEntityEntry.name=setRedString(convertedEntityEntry.name,SIENA_APPEARANCE_NAME,'entTemplateAppearance.name');
convertedEntityEntry.appearanceName=setRedString(convertedEntityEntry.appearanceName,SIENA_APPEARANCE_NAME,'entTemplateAppearance.appearanceName');
convertedEntityEntry.appearanceResource.DepotPath=setRedString(convertedEntityEntry.appearanceResource?.DepotPath,SIENA_APPEARANCE_PATH,'entTemplateAppearance.appearanceResource.DepotPath');

const app=prepare(BASE_APPEARANCE_PATH,SIENA_APPEARANCE_PATH,sourceApp,convertedApp,'appearanceAppearanceResource');
const ent=prepare(BASE_ENTITY_PATH,SIENA_ENTITY_PATH,sourceEnt,convertedEnt,'entEntityTemplate');
const savedApp=save(app);const savedEnt=save(ent);
const savedDefinitions=matchingIndexes(appDefinitions(savedApp),definitionName,SIENA_APPEARANCE_NAME);
const savedMappings=entityAppearances(savedEnt).filter(entry=>entityAppearanceName(entry)===SIENA_APPEARANCE_NAME && entityAppearanceResource(entry)===SIENA_APPEARANCE_PATH);
if (savedDefinitions.length!==1) fail(`Saved Siena .app requires exactly one ${SIENA_APPEARANCE_NAME} definition; found ${savedDefinitions.length}.`);
if (savedMappings.length!==1) fail(`Saved Siena .ent requires exactly one ${SIENA_APPEARANCE_NAME} mapping to ${SIENA_APPEARANCE_PATH}; found ${savedMappings.length}.`);
Logger.Success(`[SienaAssets] Verified ${SIENA_APPEARANCE_NAME} -> ${SIENA_APPEARANCE_PATH}; original entity/app resources were not written.`);
Logger.Success('[SienaAssets] Standalone CR2W copies verified. MANUAL REQUIRED: visually inspect components/rigs, run File Validation, then Pack Project.');
