import fs from 'node:fs';import path from 'node:path';
const root=path.resolve(import.meta.dirname,'..'),dir=path.join(root,'assets','siena_npc');
const manifest=JSON.parse(fs.readFileSync(path.join(dir,'asset-manifest.json'),'utf8'));
const required=[manifest.wolvenkit_entity_source,manifest.wolvenkit_appearance_source].map(value=>path.resolve(dir,value));
const packedRoot=path.resolve(dir,manifest.packed_archive_search_root),archives=fs.existsSync(packedRoot)?fs.readdirSync(packedRoot,{recursive:true}).filter(name=>path.basename(name)==='siena_npc.archive').map(name=>path.join(packedRoot,name)):[];
const missing=required.filter(file=>!fs.existsSync(file));if(missing.length||archives.length!==1)throw new Error(`WolvenKit authoring/build incomplete: ${[...missing,archives.length===0?'packed siena_npc.archive missing':archives.length>1?'multiple packed siena_npc.archive files':''].filter(Boolean).join(', ')}`);
for(const file of required){const data=fs.readFileSync(file);if(data.length<16||data.subarray(0,4).toString('ascii')!=='CR2W')throw new Error(`Invalid CR2W resource: ${file}`)}
if(fs.statSync(archives[0]).size<1024)throw new Error(`Built archive is unexpectedly small: ${archives[0]}`);
console.log('Siena .ent/.app/archive readiness checks passed. Packed path inspection in WolvenKit is still required before deployment.');
