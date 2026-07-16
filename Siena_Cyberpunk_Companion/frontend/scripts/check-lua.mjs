import fs from 'node:fs'
import path from 'node:path'
import process from 'node:process'
import luaparse from 'luaparse'

const root = path.resolve(process.cwd(), '..', 'bridge', 'cet', 'siena_cyberpunk_observer')
const files = fs.readdirSync(root).filter((name) => name.endsWith('.lua')).sort()
const forbidden = [
  [/\bHttpClient\s*\./, 'blocking HttpClient API'],
  [/\bwhile\s+true\s+do\b/, 'unbounded while loop'],
  [/\b(loadstring|load)\s*\(/, 'runtime code loading'],
  [/GetRecordID\s*\(/, 'v0.8.1 runtime-proven missing GetRecordID call'],
  [/Game\.GetPreventionSystem\s*\(/, 'runtime-proven missing Game.GetPreventionSystem call'],
  [/Game\.GetTargetingSystem\s*\(|GetComponentClosestToCrosshair\s*\(/, 'deferred target access'],
  [/GetItemList\s*\(|Game\.GetJournalManager\s*\(|Game\.GetQuestsSystem\s*\(/, 'deferred inventory or quest access'],
  [/PlayerDevelopmentSystem|CyberdeckProgram/, 'deferred perk, cyberware, or quickhack access'],
]

for (const name of files) {
  const filename = path.join(root, name)
  const source = fs.readFileSync(filename, 'utf8')
  try {
    const ast = luaparse.parse(source, { luaVersion: '5.1', locations: true })
    const locals = new Set()
    const collectLocals = (node) => {
      if (!node || typeof node !== 'object') return
      if (node.type === 'LocalStatement') node.variables.forEach((variable) => locals.add(variable.name))
      if (node.type === 'FunctionDeclaration') node.parameters.forEach((parameter) => {
        if (parameter.type === 'Identifier') locals.add(parameter.name)
      })
      for (const value of Object.values(node)) {
        if (Array.isArray(value)) value.forEach(collectLocals)
        else if (value && typeof value === 'object' && value.type) collectLocals(value)
      }
    }
    collectLocals(ast)
    const visit = (node) => {
      if (!node || typeof node !== 'object') return
      if (node.type === 'AssignmentStatement') {
        for (const variable of node.variables) {
          if (variable.type === 'Identifier' && !locals.has(variable.name)) {
            throw new Error(`${name}:${variable.loc.start.line}: unexpected global assignment ${variable.name}`)
          }
        }
      }
      if (node.type === 'FunctionDeclaration' && !node.isLocal && node.identifier?.type === 'Identifier') {
        throw new Error(`${name}:${node.loc.start.line}: unexpected global function ${node.identifier.name}`)
      }
      for (const value of Object.values(node)) {
        if (Array.isArray(value)) value.forEach(visit)
        else if (value && typeof value === 'object' && value.type) visit(value)
      }
    }
    visit(ast)
  } catch (error) {
    throw new Error(`${name}: ${error.message}`)
  }
  for (const [pattern, label] of forbidden) {
    if (pattern.test(source)) throw new Error(`${name}: forbidden ${label}`)
  }
}

const init = fs.readFileSync(path.join(root, 'init.lua'), 'utf8')
if (!/registerForEvent\("onInit"/.test(init) || !/registerForEvent\("onUpdate"/.test(init) || !/registerForEvent\("onShutdown"/.test(init)) {
  throw new Error('init.lua: required CET lifecycle handlers are missing')
}
if (/AsyncHttpClient\./.test(init)) throw new Error('init.lua: network API must stay outside the frame lifecycle module')
if (!/transport:shutdown\(\)/.test(init) || !/app\.last_state = nil/.test(init)) {
  throw new Error('init.lua: shutdown state cleanup is incomplete')
}
if (!files.some((name) => name === 'telemetry_client.lua')) throw new Error('telemetry client is missing')
if (!files.includes('presence_client.lua') || !files.includes('presence_overlay.lua') || !files.includes('presence_config.lua')) {
  throw new Error('v0.7 presence modules are incomplete')
}
const presenceClient = fs.readFileSync(path.join(root, 'presence_client.lua'), 'utf8')
const presenceOverlay = fs.readFileSync(path.join(root, 'presence_overlay.lua'), 'utf8')
const presenceConfig = fs.readFileSync(path.join(root, 'presence_config.lua'), 'utf8')
if (!/AsyncHttpClient\.Get/.test(presenceClient) || !/in_flight/.test(presenceClient)) throw new Error('presence client must use one tracked asynchronous GET')
if (!/after_revision/.test(presenceClient) || !/current_backoff_ms/.test(presenceClient)) throw new Error('presence revision/backoff guards are missing')
if (/AsyncHttpClient|HttpClient\./.test(presenceOverlay)) throw new Error('presence overlay must not perform HTTP')
if (!/NoInputs/.test(presenceOverlay)) throw new Error('presence overlay input passthrough guard is missing')
if (!/gsub\("%%", "%%%%"\)/.test(presenceOverlay)) throw new Error('presence text percent escaping is missing')
if (/ImGui\.Text(?:Wrapped)?\(value\.(?:text|provider)/.test(presenceOverlay)) throw new Error('external presence text is used as an ImGui format string')
if (!/GetDisplayResolution/.test(presenceOverlay) || !/top_right/.test(presenceOverlay) || !/bottom_center/.test(presenceOverlay)) throw new Error('presence viewport anchors are incomplete')
if (!/enabled\s*=\s*false/.test(presenceConfig) || !/allow_remote_presence_url\s*=\s*false/.test(presenceConfig)) throw new Error('presence safety defaults are missing')
const onDrawBody = init.match(/registerForEvent\("onDraw"[\s\S]*?\nend\)/)?.[0] ?? ''
if (/presence_client:update|AsyncHttpClient/.test(onDrawBody)) throw new Error('onDraw must contain rendering only')
if (!/if overlay_open then app:_draw_overlay\(\) end/.test(onDrawBody)) throw new Error('diagnostic overlay must stay CET-overlay gated')
if (!/app\.presence_overlay:draw/.test(onDrawBody)) throw new Error('presence overlay must render independently')

const stateReader = fs.readFileSync(path.join(root, 'state_reader.lua'), 'utf8')
const stateBuilder = fs.readFileSync(path.join(root, 'state_builder.lua'), 'utf8')
for (const evidence of [
  'GetStatValue(entity_id, field[2])', 'gamedataStatPoolType.Memory',
  'GetActiveWeaponObject(40)', 'AttachmentSlots.WeaponRight', 'selected:GetItemID()',
  'item_id.id', 'StatusEffectHelper.GetAppliedEffects(player)'
]) {
  if (!stateReader.includes(evidence)) throw new Error(`state_reader.lua: missing v0.8.1 evidence-backed access ${evidence}`)
}
if (!/active\s+or\s+slot_item/.test(stateReader) || !/source\s*=\s*"weapon_right"/.test(stateReader)) {
  throw new Error('state_reader.lua: holstered WeaponRight fallback is missing')
}
if (!/if not player_ok or player == nil then[\s\S]*self:_clear_session_cache\(\)/.test(stateReader)) {
  throw new Error('state_reader.lua: no-player session cache reset is missing')
}
if (!/deep_status_effects_max_items\s*\+\s*1/.test(stateReader) || !/truncated\s*=\s*true/.test(stateReader)) {
  throw new Error('state_reader.lua: bounded status-effect enumeration is missing')
}
if (!/deep_static_stats_interval_ms/.test(stateReader) || !/deep_weapon_interval_ms/.test(stateReader) || !/deep_status_effects_interval_ms/.test(stateReader)) {
  throw new Error('state_reader.lua: domain polling-rate caches are missing')
}
if (!/deep_game_state\s*=\s*raw\.deep_game_state/.test(stateBuilder)) {
  throw new Error('state_builder.lua: additive deep_game_state payload is missing')
}

console.log(`Lua static checks passed for ${files.length} files.`)
