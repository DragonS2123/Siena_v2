const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const path=require('node:path');
const vm=require('node:vm');
const {EventEmitter}=require('node:events');
const electronDir=path.resolve(__dirname,'../../Siena v2 Control Panel UI/electron');

function shell() {
  const app=new EventEmitter();
  let completedQuits=0, stops=0, release;
  const stopped=new Promise(r=>release=r);
  Object.assign(app,{setName(){},setDesktopName(){},setPath(){},getPath(){return '/tmp/siena-shell-fixture'},
    requestSingleInstanceLock(){return true},whenReady(){return {then(){return {catch(){}}}}},
    quit(){const e={preventDefault(){this.prevented=true}};app.emit('before-quit',e);if(!e.prevented)completedQuits++}});
  let windows=[];
  class BrowserWindow {
    constructor(){throw Error('window created before backend readiness')}
    static getAllWindows(){return windows}
  }
  const context=vm.createContext({
    require(name){
      if(name==='electron')return {app,BrowserWindow};
      if(name==='node:fs')return {mkdirSync(){},writeFileSync(){}};
      if(name==='./backend-process.cjs')return {BackendProcess:class {stop(){stops++;return stopped}}};
      return require(name);
    },
    __dirname:electronDir,
    process:{platform:'linux',env:{},on(){},pid:100},console,setTimeout,clearTimeout,
  });
  vm.runInContext(fs.readFileSync(path.join(electronDir,'main.cjs'),'utf8'),context);
  return {app,context,release,stats:()=>({stops,completedQuits}),
    openWindow(){windows=[{isDestroyed:()=>false,getNormalBounds:()=>({width:1320,height:860}),isMaximized:()=>false,
      destroy(){windows=[];app.emit('window-all-closed')}}]}};
}

test('second instance during startup waits for backend before creating a window',()=>{
  const f=shell();assert.doesNotThrow(()=>f.app.emit('second-instance'));
});
test('window destruction and repeated quit share one stop and wait for backend',async()=>{
  const f=shell();f.openWindow();
  const first=vm.runInContext('requestQuit()',f.context), second=vm.runInContext('requestQuit()',f.context);
  await new Promise(r=>setImmediate(r));
  assert.deepEqual(f.stats(),{stops:1,completedQuits:0});
  f.release();await Promise.all([first,second]);
  assert.deepEqual(f.stats(),{stops:1,completedQuits:1});
});
