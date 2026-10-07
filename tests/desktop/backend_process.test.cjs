const { test } = require('node:test');
const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const { PassThrough } = require('node:stream');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { BackendProcess } = require('../../Siena v2 Control Panel UI/electron/backend-process.cjs');

function fixture(t, options = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'siena-desktop-test-'));
  fs.mkdirSync(path.join(root,'.venv-linux/bin'),{recursive:true});
  fs.mkdirSync(path.join(root,'scripts'));
  fs.mkdirSync(path.join(root,'core'));
  fs.writeFileSync(path.join(root,'core/owned_exec.py'),'fixture');
  fs.writeFileSync(path.join(root,'.venv-linux/bin/python'),'fixture',{mode:0o700});
  fs.writeFileSync(path.join(root,'scripts/run_linux.py'),'fixture');
  const children = [];
  function spawn(command, argv, settings) {
    const child = new EventEmitter();
    Object.assign(child,{pid:100+children.length,exitCode:null,signalCode:null,stdout:new PassThrough(),stderr:new PassThrough(),signals:[],command,argv,settings});
    child.kill = signal => {
      child.signals.push(signal);
      if (!options.ignoreTerm || signal === 'SIGKILL') {
        child.signalCode=signal; child.emit('exit',null,signal);
      }
      return true;
    };
    children.push(child); return child;
  }
  const manager = new BackendProcess(root,{spawnProcess:spawn,occupied:async()=>false,health:async()=>true,
    startupTimeoutMs:5,shutdownTimeoutMs:5,killTimeoutMs:5,...options});
  t.after(async()=>{await manager.stop(); fs.rmSync(root,{recursive:true,force:true});});
  return {manager,root,children};
}

test('owns concrete child, waits readiness, uses permanent interpreter',async t=>{
  const f=fixture(t); const child=await f.manager.start();
  assert.equal(child.command,path.join(f.root,'.venv-linux/bin/python'));
  assert.equal(child.argv.at(-1),path.join(f.root,'scripts/run_linux.py'));
  if (process.platform === 'linux') assert.equal(child.argv[1],String(process.pid));
  assert.equal(child.settings.env.ELECTRON_RUN_AS_NODE,undefined);
  assert.equal(child.settings.env.DRI_PRIME,undefined);
  assert.equal(f.manager.diagnostics().pid,child.pid);
  await f.manager.stop(); assert.deepEqual(child.signals,['SIGTERM']);
});
test('duplicate and concurrent start do not spawn twice',async t=>{
  const f=fixture(t);await Promise.all([f.manager.start(),f.manager.start()]);await f.manager.start();assert.equal(f.children.length,1);
});
test('foreign occupied port is never adopted or killed',async t=>{
  const f=fixture(t,{occupied:async()=>true});await assert.rejects(f.manager.start(),/occupied/);await f.manager.stop();assert.equal(f.children.length,0);
});
test('missing interpreter fails before spawning',async t=>{
  const f=fixture(t);fs.unlinkSync(path.join(f.root,'.venv-linux/bin/python'));await assert.rejects(f.manager.start(),/ENOENT/);assert.equal(f.children.length,0);
});
test('startup timeout keeps concrete ownership for shutdown',async t=>{
  const f=fixture(t,{health:async()=>false});await assert.rejects(f.manager.start(),/timed out/);await f.manager.stop();assert.deepEqual(f.children[0].signals,['SIGTERM']);
});
test('early backend exit is detected',async t=>{
  const f=fixture(t,{health:async()=>{const c=f.children[0];c.exitCode=2;c.emit('exit',2,null);return false}});
  await assert.rejects(f.manager.start(),/exited before/);assert.equal(f.manager.lastExit.code,2);
});
test('duplicate quit is idempotent and SIGTERM precedes timed kill',async t=>{
  const f=fixture(t,{ignoreTerm:true});await f.manager.start();const a=f.manager.stop(),b=f.manager.stop();assert.equal(a,b);await a;assert.deepEqual(f.children[0].signals,['SIGTERM','SIGKILL']);
});
test('quit during pending start prevents later spawn',async t=>{
  let release;const gate=new Promise(r=>release=r);const f=fixture(t,{occupied:()=>gate});const start=f.manager.start();await f.manager.stop();release(false);await assert.rejects(start,/cancelled/);assert.equal(f.children.length,0);
});
test('quit while readiness resolves cannot report successful start',async t=>{
  const f=fixture(t,{health:async()=>{await f.manager.stop();return true}});
  await assert.rejects(f.manager.start(),/cancelled/);
  assert.deepEqual(f.children[0].signals,['SIGTERM']);
});
test('spawn error is reported without signalling a nonexistent process',async t=>{
  const f=fixture(t,{health:async()=>{const c=f.children[0];c.pid=undefined;c.emit('error',new Error('spawn EACCES'));return false}});
  await assert.rejects(f.manager.start(),/EACCES/);await f.manager.stop();
  assert.equal(f.manager.lastError,'spawn EACCES');assert.deepEqual(f.children[0].signals,[]);
});
