"use strict";

const fs = require('node:fs');
const path = require('node:path');
const net = require('node:net');
const http = require('node:http');
const { spawn } = require('node:child_process');

function portOccupied(port = 8000) {
  return new Promise((resolve, reject) => {
    const socket = net.connect({ host: '127.0.0.1', port });
    socket.setTimeout(1000);
    socket.once('connect', () => { socket.destroy(); resolve(true); });
    socket.once('timeout', () => { socket.destroy(); reject(new Error('Backend port probe timed out')); });
    socket.once('error', error => error.code === 'ECONNREFUSED' ? resolve(false) : reject(error));
  });
}

function healthy() {
  return new Promise(resolve => {
    const request = http.get('http://127.0.0.1:8000/api/runtime/status', { timeout: 1000 }, response => {
      let body = '';
      response.on('data', chunk => { body += chunk; if (body.length > 100000) request.destroy(); });
      response.on('end', () => {
        try { resolve(response.statusCode === 200 && JSON.parse(body).inference?.available === true); }
        catch { resolve(false); }
      });
    });
    request.on('timeout', () => request.destroy());
    request.on('error', () => resolve(false));
  });
}

function waitExit(child, timeoutMs) {
  if (child.exitCode !== null || child.signalCode !== null) return Promise.resolve(true);
  return new Promise(resolve => {
    const finish = exited => { clearTimeout(timer); child.removeListener('exit', exitedHandler); resolve(exited); };
    const exitedHandler = () => finish(true);
    const timer = setTimeout(() => finish(false), timeoutMs);
    child.once('exit', exitedHandler);
  });
}

class BackendProcess {
  constructor(root, { spawnProcess = spawn, occupied = portOccupied, health = healthy,
    startupTimeoutMs = 150000, shutdownTimeoutMs = 75000, killTimeoutMs = 5000 } = {}) {
    this.root = root;
    this.spawnProcess = spawnProcess;
    this.occupied = occupied;
    this.health = health;
    this.startupTimeoutMs = startupTimeoutMs;
    this.shutdownTimeoutMs = shutdownTimeoutMs;
    this.killTimeoutMs = killTimeoutMs;
    this.child = null; // Only this concrete ChildProcess can ever be signalled.
    this.starting = null;
    this.stopping = null;
    this.closed = false;
    this.lastExit = null;
    this.lastError = null;
    this.onUnexpectedExit = null;
  }

  start() {
    if (this.closed) return Promise.reject(new Error('Backend ownership is closing'));
    if (this.starting) return this.starting;
    if (this.child?.exitCode === null && this.child?.signalCode === null) return Promise.resolve(this.child);
    this.lastError = null;
    this.starting = this._start().catch(error => { this.lastError = error.message; throw error; })
      .finally(() => { this.starting = null; });
    return this.starting;
  }

  async _start() {
    const python = path.join(this.root, '.venv-linux/bin/python');
    const script = path.join(this.root, 'scripts/run_linux.py');
    const guard = path.join(this.root, 'core/owned_exec.py');
    fs.accessSync(python, fs.constants.X_OK);
    fs.accessSync(script, fs.constants.R_OK);
    fs.accessSync(guard, fs.constants.R_OK);
    if (await this.occupied(8000)) throw new Error('Port 8000 is already occupied. Siena will not adopt or stop an external backend.');
    if (this.closed) throw new Error('Backend startup cancelled');
    const env = { ...process.env, PYTHONUNBUFFERED: '1', SIENA_DATA_DIR: process.env.SIENA_DATA_DIR || path.join(this.root, 'storage/linux') };
    // These switches select the Electron UI GPU only. Inference retains its settings.
    for (const name of ['ELECTRON_RUN_AS_NODE', 'DRI_PRIME', 'EGL_PLATFORM', 'SIENA_SHELL_DEBUG', 'SIENA_OPEN_DEVTOOLS']) delete env[name];
    const argv = process.platform === 'linux' ? [guard, String(process.pid), python, script] : [script];
    const child = this.spawnProcess(python, argv, { cwd: this.root, env, stdio: ['ignore', 'pipe', 'pipe'] });
    this.child = child;
    const logs = path.join(env.SIENA_DATA_DIR, 'logs');
    fs.mkdirSync(logs, { recursive: true });
    const output = fs.createWriteStream('', { fd: fs.openSync(path.join(logs, 'desktop-backend.stdout.log'), 'a') });
    const errors = fs.createWriteStream('', { fd: fs.openSync(path.join(logs, 'desktop-backend.stderr.log'), 'a') });
    output.on('error', error => console.error('Backend stdout log:', error.message));
    errors.on('error', error => console.error('Backend stderr log:', error.message));
    child.stdout?.pipe(output); child.stderr?.pipe(errors);
    child.once('error', error => { this.lastError = error.message; output.end(); errors.end(); });
    child.once('exit', (code, signal) => {
      this.lastExit = { pid: child.pid, code, signal };
      output.end(); errors.end();
      if (!this.closed && !this.starting) this.onUnexpectedExit?.(this.lastExit);
    });
    const deadline = Date.now() + this.startupTimeoutMs;
    while (Date.now() < deadline) {
      if (this.closed) throw new Error('Backend startup cancelled');
      if (this.lastError || child.exitCode !== null || child.signalCode !== null) {
        throw new Error(this.lastError || `Backend exited before readiness (${child.exitCode ?? child.signalCode})`);
      }
      const ready = await this.health();
      if (this.closed) throw new Error('Backend startup cancelled');
      if (this.lastError || child.exitCode !== null || child.signalCode !== null) {
        throw new Error(this.lastError || `Backend exited before readiness (${child.exitCode ?? child.signalCode})`);
      }
      if (ready) return child;
      await new Promise(resolve => setTimeout(resolve, 100));
    }
    throw new Error('Backend startup timed out; see desktop-backend.stderr.log');
  }

  stop() {
    if (this.stopping) return this.stopping;
    this.closed = true;
    this.stopping = this._stop();
    return this.stopping;
  }

  async _stop() {
    const child = this.child;
    if (!child?.pid || child.exitCode !== null || child.signalCode !== null) return;
    // Register wait before signalling: even an immediate exit must be observed.
    const graceful = waitExit(child, this.shutdownTimeoutMs);
    child.kill('SIGTERM');
    if (!(await graceful)) {
      const forced = waitExit(child, this.killTimeoutMs);
      child.kill('SIGKILL');
      if (!(await forced)) throw new Error(`Owned backend PID ${child.pid} did not exit after SIGKILL`);
    }
  }

  diagnostics() {
    return { pid: this.child?.pid ?? null, owned: this.child !== null,
      running: this.child?.exitCode === null && this.child?.signalCode === null,
      closing: this.closed, lastExit: this.lastExit, lastError: this.lastError };
  }
}
module.exports = { BackendProcess, portOccupied, waitExit };
