/* Private reconciliation runner. Never submits a command or starts a consumer. */
'use strict';
const fs = require('fs'), path = require('path'), crypto = require('crypto');
const evidence = process.argv[2];
const sha = bytes => crypto.createHash('sha256').update(bytes).digest('hex');
const same = (a,b) => JSON.stringify(a) === JSON.stringify(b);
const canonical = v => v && typeof v === 'object' && !Array.isArray(v) ? Object.fromEntries(Object.keys(v).sort().map(k=>[k,canonical(v[k])])) : v;
const equal = (a,b) => same(canonical(a), canonical(b));
function safe(file, directory=false) {
  if (!path.isAbsolute(file) || file.split(path.sep).includes('..')) throw Error('absolute safe path required');
  let at=file;
  for (;;) {
    if (fs.lstatSync(at).isSymbolicLink() || fs.existsSync(path.join(at,'.git'))) throw Error('symlink/Git path');
    const parent=path.dirname(at);if(parent===at) break;at=parent;
  }
  const st=fs.statSync(file);
  if ((directory ? !st.isDirectory() : !st.isFile()) || st.uid!==process.getuid() || (st.mode & 0o077)) throw Error('unsafe private path');
}
function read(file) { safe(file);const bytes=fs.readFileSync(file);if(bytes.length>65536) throw Error('oversized evidence');return bytes; }
function write(file,value) { fs.writeFileSync(file,JSON.stringify(value)+'\n',{mode:0o600,flag:'wx'}); }
let intent, mutated=false, callbackCount=0;
try {
  safe(evidence,true);
  intent=JSON.parse(read(path.join(evidence,'intent.json')));
  const root=intent.root, oldDir=path.dirname(intent.descriptor), actor=path.join(root,'reconciliation-actor');
  safe(root,true);safe(oldDir,true);safe(path.join(root,'owner'),true);safe(actor,true);
  if (intent.assertion!=='target-unloaded' || !intent.reason.trim() || !intent.operator_evidence.trim()) throw Error('explicit operator assertion required');
  if (!equal(JSON.parse(read(path.join(actor,'identity.json'))),{attempt:intent.attempt,old_identity:intent.identity})) throw Error('reconciliation actor changed');
  const pinBytes=read(path.join(evidence,'expected-pins.json'));
  if(sha(pinBytes)!==intent.expected_pins_sha256 || !equal(JSON.parse(pinBytes),intent.identity.pins)) throw Error('expected pins changed');
  const helper=path.join(__dirname,'harness_mailbox.cjs');
  if (sha(fs.readFileSync(helper))!=='4e7d443e68e44a63c91f1a3aa0ce45700e1e061bb3ad104b4029ece1da51787e') throw Error('helper pin mismatch');
  for (const [name,digest] of Object.entries(intent.before_sha256)) {
    if (path.isAbsolute(name) || name.split('/').some(x=>x==='..' || x==='')) throw Error('unsafe snapshot member');
    if (sha(read(path.join(root,name)))!==digest || sha(read(path.join(evidence,'before',name)))!==digest) throw Error('snapshot changed');
  }
  const descriptor=JSON.parse(read(intent.descriptor));
  if(descriptor.state!=='fenced' || !equal(Object.fromEntries(['format','instance','nonce','pins'].map(k=>[k,descriptor[k]])),intent.identity) || !equal(JSON.parse(read(path.join(root,'owner','identity.json'))),intent.identity)) throw Error('old identity/state changed');
  // Require exact member inventory so no newly arrived ACK/actor is omitted.
  const members=dir=>fs.readdirSync(dir).flatMap(name=>name==='actor' ? fs.readdirSync(path.join(dir,name)).map(x=>'actor/'+x) : [name]);
  const current=[...members(path.join(root,'owner')).map(x=>'owner/'+x),...members(oldDir).map(x=>descriptor.instance+'/'+x)].sort();
  if(!equal(current,Object.keys(intent.before_sha256).sort())) throw Error('old inventory changed');
  const {createHarnessMailbox}=require(helper);
  mutated=true;
  const marker=createHarnessMailbox({root,pins:intent.identity.pins,
    commands:{retirement_marker:{}},reconciliation:'target-unloaded',
    handle() { callbackCount++;throw Error('inert marker must never dispatch'); }
  });
  // Synchronous same-stack close precedes the helper's first timer poll.
  marker.close();
  const closed=JSON.parse(read(marker.descriptorPath));
  if(closed.state!=='closed' || closed.pending!==null || callbackCount!==0 || fs.existsSync(path.join(root,'owner'))) throw Error('marker retirement incomplete');
  write(path.join(evidence,'marker.json'),closed);
  write(path.join(evidence,'runner.json'),{status:'closed',callback_count:callbackCount,descriptor:marker.descriptorPath});
} catch(error) {
  // If helper failed after dropping the old owner but before publishing a new
  // one, conservatively restore an exclusive old-identity ownership fence.
  if(mutated && intent) {
    const owner=path.join(intent.root,'owner');
    if(!fs.existsSync(owner)) {
      try {fs.mkdirSync(owner,{mode:0o700});write(path.join(owner,'identity.json'),intent.identity);}
      catch (_) {} // Never remove or overwrite a racing owner.
    }
  }
  console.error(error.message);
  process.exitCode=1;
}
