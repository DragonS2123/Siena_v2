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

console.log(`Lua static checks passed for ${files.length} files.`)
