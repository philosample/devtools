/* Opt-in local test-harness transport. No Max, MIDI, network or UI dependency. */
'use strict';
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const os = require('os');
const FORMAT = 'codex-harness-mailbox-v1';
const uuid = () => crypto.randomUUID();
const exact = (o, keys) => o && typeof o === 'object' && !Array.isArray(o) && Object.keys(o).sort().join() === keys.slice().sort().join();
const same = (a, b) => JSON.stringify(canonical(a)) === JSON.stringify(canonical(b));
function canonical(v) { return v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.keys(v).sort().map(k => [k, canonical(v[k])])) : v; }
function atomic(file, value) {
  const tmp = file + '.' + uuid() + '.tmp';
  const serialized = JSON.stringify(value);
  if (Buffer.byteLength(serialized, 'utf8') > 65536) throw Error('mailbox JSON exceeds 65536 bytes');
  fs.writeFileSync(tmp, serialized, {mode: 0o600, flag: 'wx'});
  fs.renameSync(tmp, file);
}
function read(file) { const s = fs.readFileSync(file, 'utf8'); if (Buffer.byteLength(s, 'utf8') > 65536) throw Error('oversized JSON'); return JSON.parse(s); }
function pinsValid(p) {
  const hash = x => typeof x === 'string' && /^[a-f0-9]{64}$/.test(x);
  return exact(p, ['artifact_sha256', 'components']) && hash(p.artifact_sha256) && p.components && !Array.isArray(p.components) && Object.keys(p.components).length > 0 && Object.keys(p.components).length <= 64 && Object.entries(p.components).every(([k,v]) => /^[a-zA-Z0-9_-]{1,64}$/.test(k) && hash(v));
}
function schemaValid(s) {
  if (!s || Array.isArray(s) || typeof s !== 'object' || Object.keys(s).length > 32) return false;
  return Object.entries(s).every(([key, r]) => /^[a-zA-Z][a-zA-Z0-9_]{0,31}$/.test(key) && (
    (exact(r, ['type','min','max']) && ['number','integer'].includes(r.type) && Number.isFinite(r.min) && Number.isFinite(r.max) && r.min <= r.max) ||
    (exact(r, ['enum']) && Array.isArray(r.enum) && r.enum.length > 0 && r.enum.length <= 32 && r.enum.every(x => typeof x === 'string' && x.length <= 128)) ||
    (exact(r, ['type']) && r.type === 'boolean')));
}
function argsValid(args, schema) {
  return exact(args, Object.keys(schema)) && Object.entries(schema).every(([k,r]) => r.enum ? r.enum.includes(args[k]) : r.type === 'boolean' ? typeof args[k] === 'boolean' : typeof args[k] === 'number' && Number.isFinite(args[k]) && args[k] >= r.min && args[k] <= r.max && (r.type !== 'integer' || Number.isInteger(args[k])));
}
function privateRoot(root) {
  if (!path.isAbsolute(root)) throw Error('root must be absolute');
  fs.mkdirSync(root, {recursive:true, mode:0o700});
  let at = root;
  for (;;) {
    if (fs.lstatSync(at).isSymbolicLink()) throw Error('symlink root ancestor');
    if (fs.existsSync(path.join(at, '.git'))) throw Error('mailbox must be outside Git');
    const parent = path.dirname(at); if (parent === at) break; at = parent;
  }
  const st = fs.statSync(root);
  if ((st.mode & 0o077) || (process.getuid && st.uid !== process.getuid())) throw Error('root must be private and owned');
}
function createHarnessMailbox(options) {
  const root = options.root || path.join(os.homedir(), '.local', 'state', 'codex-devtools', 'harness');
  if (!pinsValid(options.pins)) throw Error('invalid pins');
  const commands = options.commands && structuredClone(options.commands);
  if (!commands || Object.keys(commands).length < 1 || Object.keys(commands).length > 16 || !Object.entries(commands).every(([k,v]) => /^[a-z][a-z0-9_]{0,31}$/.test(k) && schemaValid(v))) throw Error('invalid capabilities');
  if (typeof options.handle !== 'function') throw Error('handle callback required');
  const leaseMs = options.leaseMs === undefined ? 3000 : options.leaseMs;
  const timeoutMs = options.timeoutMs === undefined ? 10000 : options.timeoutMs;
  if (!Number.isInteger(leaseMs) || leaseMs < 200 || leaseMs > 30000 || !Number.isInteger(timeoutMs) || timeoutMs < 100 || timeoutMs > 60000) throw Error('invalid time bounds');
  // Reserve the largest descriptor state before touching root/ownership/files.
  const instance = uuid(), nonce = crypto.randomBytes(32).toString('hex');
  const identity = {format:FORMAT, instance, nonce, pins:structuredClone(options.pins)};
  const largestDescriptor = {...identity, commands, sequence:Number.MAX_SAFE_INTEGER,
    state:'pending', pending:{request:uuid(),sequence:Number.MAX_SAFE_INTEGER},
    expires_at_ms:Number.MIN_SAFE_INTEGER, timeout_ms:timeoutMs};
  if (Buffer.byteLength(JSON.stringify(largestDescriptor), 'utf8') > 65536) throw Error('descriptor exceeds 65536-byte state bound');
  privateRoot(root);
  const owner = path.join(root, 'owner');
  // Manual reconciliation is an assertion by the harness operator, never inferred from TTL.
  if (options.reconciliation !== undefined) {
    if (!['target-unloaded','domain-reconciled'].includes(options.reconciliation)) throw Error('invalid reconciliation');
    fs.rmSync(owner, {recursive:true, force:true});
  }
  fs.mkdirSync(owner, {mode:0o700}); // Existing owner (even stale) requires reconciliation.
  const dir = path.join(root, instance); fs.mkdirSync(dir, {mode:0o700});
  const descriptorPath = path.join(dir, 'descriptor.json');
  atomic(path.join(owner, 'identity.json'), identity);
  let sequence = 0, pending = null, state = 'ready', closed = false;
  let previousWall = Date.now(), previousMono = process.hrtime.bigint();
  const acknowledgements = [];
  const descriptor = () => ({...identity, commands, sequence, state, pending:pending && {request:pending.request, sequence:pending.sequence}, expires_at_ms:Date.now()+leaseMs, timeout_ms:timeoutMs});
  const publish = () => atomic(descriptorPath, descriptor());
  const ownsRoot = () => { try { return same(read(path.join(owner, 'identity.json')), identity); } catch (_) { return false; } };
  function ack(command, status, detail) {
    const value = {...identity, request:command.request, sequence:command.sequence, status, detail};
    atomic(path.join(dir, 'ack.'+command.request+'.json'), value);
    acknowledgements.push(command.request);
    while (acknowledgements.length > 32) fs.rmSync(path.join(dir, 'ack.'+acknowledgements.shift()+'.json'), {force:true});
  }
  function finish(command, status, detail) {
    // Bind to this exact admission, not a UUID that may be reused after pruning.
    if (pending !== command || pending.request !== command.request || pending.sequence !== command.sequence || state !== 'pending') return false;
    const elapsed = Number(process.hrtime.bigint()-pending.started)/1e6;
    const sincePoll = Number(process.hrtime.bigint()-previousMono)/1e6;
    if (elapsed >= timeoutMs || sincePoll > leaseMs || Math.abs(Date.now()-previousWall-sincePoll) > 250) {
      fence('completion outside time bounds; effects unknown'); return false;
    }
    ack(command, status, detail); // terminal ACK before release; callback never releases client locks.
    pending = null; state = 'ready';
    fs.rmSync(path.join(dir, 'command.json'), {force:true});
    fs.rmSync(path.join(dir, 'actor'), {recursive:true, force:true});
    publish(); return true;
  }
  function fence(reason) {
    if (state === 'fenced' || closed) return;
    state = 'fenced';
    if (pending) ack(pending, 'unknown_effect', reason);
    publish(); // Leave pending, command, actor and root ownership intact.
  }
  function poll() {
    if (closed) return;
    if (!ownsRoot()) { state = 'fenced'; closed = true; clearInterval(timer); return; }
    const now = process.hrtime.bigint(), wall = Date.now();
    const delta = Number(now-previousMono)/1e6;
    if (Math.abs(wall-previousWall-delta) > 250 || delta > leaseMs) fence('clock discontinuity or receiver lease gap');
    previousMono=now; previousWall=wall;
    if (pending && Number(now-pending.started)/1e6 >= timeoutMs) fence('completion timeout; effects unknown');
    if (state !== 'ready') { publish(); return; }
    const file = path.join(dir, 'command.json');
    if (!fs.existsSync(file)) { publish(); return; }
    let c;
    try {
      c=read(file);
      if (!exact(c, ['format','instance','nonce','pins','request','sequence','command','args','deadline_ms']) || !/^[a-f0-9-]{36}$/.test(c.request) || !Number.isSafeInteger(c.sequence) || c.sequence !== sequence+1 || !same({format:c.format,instance:c.instance,nonce:c.nonce,pins:c.pins}, identity) || !Object.hasOwn(commands,c.command) || !argsValid(c.args, commands[c.command]) || !Number.isFinite(c.deadline_ms) || c.deadline_ms < wall || c.deadline_ms > wall+timeoutMs+leaseMs) throw Error('invalid command identity, sequence, deadline or schema');
      if (fs.existsSync(path.join(dir,'ack.'+c.request+'.json'))) throw Error('request already acknowledged');
      const actor = read(path.join(dir,'actor','identity.json'));
      if (!exact(actor, ['request']) || actor.request !== c.request) throw Error('actor mismatch');
    } catch (e) { fence('invalid command: '+e.message); return; }
    sequence=c.sequence; pending={...c, started:now}; state='pending'; publish();
    const admission = pending;
    // Completion is explicit. Returning from handle/outlet means only enqueued.
    const controls = Object.freeze({
      completed: () => finish(admission,'completed','domain reported completion'),
      failed: () => finish(admission,'failed','domain reported failure')
    });
    try {
      const result = options.handle(c.command, structuredClone(c.args), controls, Object.freeze({request:c.request,sequence:c.sequence}));
      if (result && typeof result.then === 'function') result.catch(() => { if (pending === admission) fence('handler rejected; effects unknown'); });
    } catch (_) { if (pending === admission) fence('handler threw; effects unknown'); }
  }
  publish();
  const timer = setInterval(poll, Math.min(50, Math.floor(leaseMs/4)));
  return Object.freeze({descriptorPath, close() {
    if (pending || state === 'fenced') throw Error('reconciliation required before close');
    closed=true; clearInterval(timer); state='closed'; publish();
    if (ownsRoot()) fs.rmSync(owner,{recursive:true});
  }});
}
module.exports = {createHarnessMailbox};
