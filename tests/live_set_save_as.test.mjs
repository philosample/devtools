import assert from 'node:assert/strict';
import * as fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import {pathToFileURL} from 'node:url';
import {absolutePath, parseAX, setURL, preserveLiveSetAs, LiveSaveAsGuardError} from '../cua/live_set_save_as.mjs';

let count = 0;
async function test(name, run) { await run(); count++; console.log('PASS ' + name); }
const url = filename => pathToFileURL(filename).href;

async function fixture({nested = false, edit, filename = 'Copy With Spaces.als'} = {}) {
  const base = await fs.realpath(await fs.mkdtemp(path.join(os.tmpdir(), 'devtools-save-as-test-')));
  const source = path.join(base, 'Original Source.als');
  await fs.writeFile(source, 'synthetic source bytes; never opened in Live');
  const directory = path.join(base, 'fresh preservation');
  const final = nested ? path.join(directory, 'Copy With Spaces Project', filename) : path.join(directory, filename);
  const options = {expectedSourceURL: url(source), expectedFinalURL: url(final), destinationDirectory: directory, destinationFilename: filename};
  let state = 'source', name = 'Untitled.als', folder = '', observations = 0;
  const actions = [];
  let lastActionObservation = 0;
  const freshAction = () => { assert.ok(observations > lastActionObservation, 'fresh AX before each UI action'); lastActionObservation = observations; };
  const normal = target => `0 standard window Name, Secondary Actions: Raise, URL: ${url(target)}\n  27 checkbox Description: Play, Value: 0, ID: Transport.Play, Help: Play Button`;
  const save = () => `0 standard window Name, Secondary Actions: Raise, URL: ${url(source)}\n  27 checkbox Description: Play, Value: 0, ID: Transport.Play, Help: Play Button\n  30 dialog Save\n    31 text field Value: ${name}, ID: saveAsNameTextField\n    32 pop up button Where:, Value: ${path.basename(folder) || 'old location'}, ID: where\n    33 button Save, ID: OKButton\n    34 button Cancel, ID: cancel${state === 'path' ? `\n    35 text field Value: ${folder}, ID: PathTextField` : ''}`;
  const app = {
    async getAXState(args) {
      assert.deepEqual(args, {emit: false, disableDiffing: true});
      observations++;
      const text = state === 'source' ? normal(source) : state === 'final' ? normal(final) : save();
      return edit ? await edit({text, state, observations, base, source, directory, final, actions, app}) : text;
    },
    async performSecondaryAction(id, action) { freshAction(); actions.push(['raise', id, action]); assert.equal(state, 'source'); assert.equal(id, 0); assert.equal(action, 'Raise'); },
    async pressKey(key) {
      freshAction(); actions.push(['key', key]);
      if (key === 'super+shift+s') { assert.equal(state, 'source'); state = 'save'; }
      else if (key === 'super+shift+g') { assert.equal(state, 'save'); state = 'path'; }
      else if (key === 'Return') { assert.equal(state, 'path'); state = 'save'; }
      else assert.fail('unapproved key: ' + key);
    },
    async setValue(id, value) {
      freshAction(); actions.push(['set', id, value]);
      if (id === 31) { assert.equal(state, 'save'); name = value; }
      else { assert.equal(id, 35); assert.equal(state, 'path'); folder = value; }
    },
    async click(id) {
      freshAction(); actions.push(['click', id]); assert.equal(id, 33); assert.equal(state, 'save');
      assert.equal(name, filename); assert.equal(folder, directory);
      await fs.mkdir(path.dirname(final), {recursive: true});
      await fs.writeFile(final, 'synthetic destination bytes', {flag: 'wx'});
      state = 'final';
    },
  };
  return {base, source, directory, filename, final, options, app, actions,
    cleanup: () => fs.rm(base, {recursive: true, force: true})};
}
async function withFixture(options, run) {
  const f = await fixture(options);
  try { await run(f); } finally { await f.cleanup(); }
}
async function rejected(f, pattern, attempted = false) {
  await assert.rejects(preserveLiveSetAs(f.app, f.options), error => {
    assert.ok(error instanceof LiveSaveAsGuardError);
    assert.match(error.message, pattern);
    assert.equal(error.saveAttempted, attempted);
    assert.equal(error.effect, attempted ? 'save_effect_unknown' : 'save_not_attempted');
    return true;
  });
  assert.equal(f.actions.filter(a => a[0] === 'click').length, attempted ? 1 : 0);
}

await test('direct and exact Live Project nesting preserve source with one Save and fresh decisions', async () => {
  for (const nested of [false, true]) await withFixture({nested}, async f => {
    const original = await fs.readFile(f.source);
    const result = await preserveLiveSetAs(f.app, f.options);
    assert.equal(result.status, 'destination_observed');
    assert.equal(result.destinationURL, url(f.final)); assert.equal(result.saveAttempts, 1);
    assert.deepEqual(await fs.readFile(f.source), original);
    assert.equal((await fs.stat(f.directory)).mode & 0o777, 0o700);
    assert.deepEqual(f.actions, [['raise', 0, 'Raise'], ['key', 'super+shift+s'], ['set', 31, f.filename],
      ['key', 'super+shift+g'], ['set', 35, f.directory], ['key', 'Return'], ['click', 33]]);
  });
});
await test('a Save panel may hide the modal source subtree while visible state is still checked', async () => {
  await withFixture({edit: ({text, state}) => ['save', 'path'].includes(state) ? text.split('\n').slice(2).join('\n') : text}, async f => {
    await preserveLiveSetAs(f.app, f.options); assert.equal(f.actions.filter(a => a[0] === 'click').length, 1);
  });
  await withFixture({edit: ({text, observations}) => observations === 5 ? text.replace('Value: 0, ID: Transport.Play', 'Value: 1, ID: Transport.Play') : text}, f => rejected(f, /stopped/));
});
await test('Set URL parser compares exact decoded file identity and rejects misleading forms', async () => {
  assert.equal(setURL('file:///private/tmp/A%20Set.als').path, '/private/tmp/A Set.als');
  assert.equal(setURL('file:///private/tmp/%41.als').url, 'file:///private/tmp/A.als');
  for (const value of ['http:///x.als', 'file://localhost/x.als', 'file:////x.als', 'file:///x.als?query', 'file:///x.als#tail',
    'file:///private/tmp/../x.als', 'file:///private/tmp/%2e%2e/x.als', 'file:///private/tmp/a%2fb.als',
    'file:///private/tmp/a%5cb.als', 'file:///private/tmp/a%00b.als', 'file:///private/tmp/x%.als', 'file:///x.als/other']) {
    assert.throws(() => setURL(value), undefined, value);
  }
});
await test('destination paths reject normalization, controls and relative traversal', async () => {
  for (const value of ['relative', '/', '/a/../b', '/a/./b', '/a//b', '/a/', '/a\\b', '/a\nb']) assert.throws(() => absolutePath(value));
});
await test('AX parser rejects duplicate/unsafe IDs and bounded oversized inputs', async () => {
  for (const text of ['0 button A\n0 button B', '9007199254740992 button A', 'x'.repeat(1_000_001), '\n'.repeat(10_001)]) assert.throws(() => parseAX(text));
});
await test('source URL prefixes, title substrings and extra URL attributes cannot select the Set', async () => {
  for (const variant of ['suffix', 'title', 'extra']) await withFixture({edit: ({text, source}) => {
    if (variant === 'suffix') return text.replace(url(source), url(source) + '.backup.als');
    if (variant === 'title') return text.replace('window Name', 'window ' + url(source)).replace(', URL: ' + url(source), ', URL: file:///private/tmp/Other.als');
    return text.replace(', URL: ', ', URL: file:///private/tmp/Decoy.als, URL: ');
  }}, f => rejected(f, /URL/));
});
await test('playing, missing or ambiguous transport aborts before UI mutations', async () => {
  for (const variant of ['playing', 'missing', 'ambiguous', 'false_help', 'wrong_role']) await withFixture({edit: ({text}) => {
    if (variant === 'playing') return text.replace('Value: 0, ID: Transport.Play', 'Value: 1, ID: Transport.Play');
    if (variant === 'missing') return text.split('\n')[0];
    if (variant === 'wrong_role') return text.replace('checkbox Description: Play', 'button Description: Play');
    if (variant === 'ambiguous') return text + '\n28 checkbox Value: 0, ID: Transport.Play';
    return text.replace('Value: 0, ID: Transport.Play', 'Value: 1, ID: Transport.Play, Help: Value: 0');
  }}, async f => { await rejected(f, /transport|Play|value/i); assert.deepEqual(f.actions, []); });
});
await test('preexisting Save, security and discard dialogs are never dismissed', async () => {
  for (const prompt of ['30 dialog Save', '30 alert Security', "30 sheet Save changes\n31 button Don't Save"]) await withFixture({edit: ({text}) => text + '\n' + prompt}, async f => {
    await rejected(f, /dialog|prompt/); assert.deepEqual(f.actions, []);
  });
});
await test('ambiguous standard windows, field IDs and Save buttons fail closed', async () => {
  for (const variant of ['window', 'name', 'save', 'path', 'where']) await withFixture({edit: ({text, state}) => {
    if (variant === 'window') return text + '\n9 standard window Other, Secondary Actions: Raise, URL: file:///private/tmp/Other.als';
    if (variant === 'name' && state === 'save') return text + '\n81 text field Value: x, ID: saveAsNameTextField';
    if (variant === 'save' && state === 'save') return text + '\n81 button Save, ID: OKButton';
    if (variant === 'path' && state === 'path') return text + '\n81 text field Value: x, ID: PathTextField';
    if (variant === 'where' && state === 'save') return text + '\n81 pop up button Where:, Value: x, ID: other';
    return text;
  }}, f => rejected(f, /exactly one|ambiguous/));
});
await test('source change after Raise or during Save aborts without Save click', async () => {
  for (const at of [3, 4, 8]) await withFixture({edit: ({text, observations}) => observations === at ? text.replace(/, URL: .*/, ', URL: file:///private/tmp/Other.als') : text}, f => rejected(f, /Set.*mismatch|Set changed/));
});
await test('unexpected Replace/security prompts at every decision stop without acceptance', async () => {
  for (const at of [4, 5, 6, 7, 8]) await withFixture({edit: ({text, observations}) => observations === at ? text + '\n99 button Replace, ID: replace' : text}, f => rejected(f, /prompt/));
});
await test('Save dialog identity must be exact, not a Save changes prompt', async () => {
  await withFixture({edit: ({text, state}) => state === 'save' ? text.replace('dialog Save', 'dialog Save changes') : text}, f => rejected(f, /dialog structure/));
});
await test('filename is verified after setValue and again before the single Save', async () => {
  for (const at of [5, 8]) await withFixture({edit: ({text, observations}) => observations === at ? text.replace(/Value: Copy With Spaces.als, ID: saveAsNameTextField/, 'Value: Wrong.als, ID: saveAsNameTextField') : text}, f => rejected(f, /filename/));
});
await test('Go To Folder full path and Where leaf are both required exactly', async () => {
  for (const variant of ['path', 'where']) await withFixture({edit: ({text, observations}) => {
    if (variant === 'path' && observations === 7) return text.replace(/Value: .*?, ID: PathTextField/, 'Value: /wrong/place, ID: PathTextField');
    if (variant === 'where' && observations === 8) return text.replace('Where:, Value: fresh preservation,', 'Where:, Value: fresh preservation suffix,');
    return text;
  }}, f => rejected(f, /path mismatch|Where destination/));
});
await test('Where label text on an unrelated role cannot authorize the destination', async () => {
  await withFixture({edit: ({text, observations}) => observations === 8 ? text.replace('pop up button Where:', 'static text Where:') : text}, f => rejected(f, /role\/label mismatch/));
});
await test('a Go To Folder field remaining after Return prevents Save', async () => {
  await withFixture({edit: ({text, observations}) => observations === 8 ? text + '\n35 text field Value: x, ID: PathTextField' : text}, f => rejected(f, /still open/));
});
await test('missing or disabled controls are never clicked', async () => {
  for (const variant of ['name', 'save', 'disabled']) await withFixture({edit: ({text, state}) => {
    if (state !== 'save') return text;
    if (variant === 'name') return text.replace('ID: saveAsNameTextField', 'ID: other');
    if (variant === 'save') return text.replace('button Save, ID: OKButton', 'button Cancel, ID: cancel2');
    return text.replace('button Save, ID: OKButton', 'button Save, ID: OKButton, Enabled: false');
  }}, f => rejected(f, /exactly one|disabled/));
});
await test('native (disabled) AX role flags reject already-correct fields before actions', async () => {
  for (const target of ['name', 'path', 'window', 'where']) await withFixture({edit: ({text, observations, directory}) => {
    if (target === 'name' && observations === 4) return text.replace('text field Value: Untitled.als, ID: saveAsNameTextField',
      'text field (disabled) Value: Copy With Spaces.als, ID: saveAsNameTextField');
    if (target === 'path' && observations === 6) return text.replace('text field Value: , ID: PathTextField',
      `text field (disabled) Value: ${directory}, ID: PathTextField`);
    if (target === 'window' && observations === 1) return text.replace('standard window Name', 'standard window (disabled) Name');
    if (target === 'where' && observations === 8) return text.replace('pop up button Where:', 'pop up button (disabled) Where:');
    return text;
  }}, async f => {
    await rejected(f, /disabled/);
    if (target === 'name') assert.deepEqual(f.actions, [['raise', 0, 'Raise'], ['key', 'super+shift+s']], 'correct filename does not authorize a disabled field');
    if (target === 'path') assert.ok(!f.actions.some(row => row[0] === 'set' && row[1] === 35), 'correct path does not authorize a disabled field');
    if (target === 'window') assert.deepEqual(f.actions, []);
  });
});
await test('literal (disabled) filename text is not an AX role flag', async () => {
  await withFixture({filename: 'Copy (disabled) With Spaces.als'}, async f => {
    const result = await preserveLiveSetAs(f.app, f.options); assert.equal(result.destinationFilename, f.filename);
  });
});
await test('existing destination directory/file is not reused or overwritten', async () => {
  await withFixture({}, async f => {
    await fs.mkdir(f.directory); const keep = path.join(f.directory, f.filename); await fs.writeFile(keep, 'keep');
    await rejected(f, /fresh destination/); assert.deepEqual(f.actions, []); assert.equal(await fs.readFile(keep, 'utf8'), 'keep');
  });
});
await test('source or destination ancestor symlinks are rejected', async () => {
  for (const variant of ['source', 'destination']) await withFixture({}, async f => {
    const alias = path.join(f.base, 'alias'); await fs.symlink(f.base, alias);
    if (variant === 'source') f.options.expectedSourceURL = url(path.join(alias, path.basename(f.source)));
    else { f.options.destinationDirectory = path.join(alias, 'copy'); f.options.expectedFinalURL = url(path.join(alias, 'copy', f.filename)); }
    if (variant === 'source') f.app.getAXState = async () => `0 standard window Source, Secondary Actions: Raise, URL: ${f.options.expectedSourceURL}\n27 checkbox Value: 0, ID: Transport.Play`;
    await rejected(f, /symlink/); assert.deepEqual(f.actions, []);
  });
});
await test('destination inside a Git checkout or worktree marker is rejected', async () => {
  for (const variant of ['directory', 'file']) await withFixture({}, async f => {
    const git = path.join(f.base, '.git'); if (variant === 'directory') await fs.mkdir(git); else await fs.writeFile(git, 'gitdir: elsewhere');
    await rejected(f, /outside Git/); assert.deepEqual(f.actions, []);
  });
});
await test('a file arriving in the fresh directory before Save prevents any Save attempt', async () => {
  await withFixture({edit: async context => {
    if (context.observations === 7) await fs.writeFile(path.join(context.directory, 'arrived.als'), 'keep');
    return context.text;
  }}, f => rejected(f, /no longer empty/));
});
await test('explicit final URL cannot authorize an arbitrary or source destination', async () => {
  for (const target of ['outside', 'wrong_project', 'wrong_filename', 'source']) await withFixture({}, async f => {
    f.options.expectedFinalURL = url(target === 'source' ? f.source : target === 'outside' ? path.join(f.base, f.filename) :
      target === 'wrong_project' ? path.join(f.directory, 'Other Project', f.filename) : path.join(f.directory, 'Wrong.als'));
    await assert.rejects(preserveLiveSetAs(f.app, f.options), /expected final URL/); assert.deepEqual(f.actions, []);
  });
});
await test('invalid or ambiguous filenames/config do not reach the app', async () => {
  for (const name of ['../copy.als', '/copy.als', 'x\\copy.als', 'x.als\n', '.als', 'copy', 'x, ID: spoof.als']) await withFixture({}, async f => {
    f.options.destinationFilename = name; await assert.rejects(preserveLiveSetAs(f.app, f.options), /filename/); assert.deepEqual(f.actions, []);
  });
});
await test('a mismatched final URL, unexpected final prompt or playing result is unknown and never retried', async () => {
  for (const variant of ['url', 'prompt', 'playing']) await withFixture({edit: ({text, state}) => {
    if (state !== 'final') return text;
    if (variant === 'url') return text.replace(/, URL: .*/, ', URL: file:///private/tmp/Unexpected.als');
    if (variant === 'prompt') return text + '\n98 dialog Save\n99 button Replace, ID: replace';
    return text.replace('Value: 0, ID: Transport.Play', 'Value: 1, ID: Transport.Play');
  }}, f => rejected(f, /mismatch|prompt|stopped/, true));
});
await test('a Save click exception is possible egress and is never retried or cleaned up', async () => {
  await withFixture({}, async f => {
    f.app.click = async id => { f.actions.push(['click', id]); throw Error('native callback effect unknown'); };
    await rejected(f, /effect unknown/, true); assert.ok((await fs.stat(f.directory)).isDirectory());
    assert.ok(!f.actions.some(row => ['Cancel', 'Escape', 'super+w', 'super+s'].includes(row[1])));
  });
});
await test('arbitrary Save exceptions retain uncertainty without reading hostile messages', async () => {
  let messageReads = 0, coercions = 0;
  const hostile = Object.defineProperties({}, {
    message: {get() { messageReads++; throw Error('message getter must not run'); }},
    toString: {value() { coercions++; throw Error('object coercion must not run'); }},
  });
  const uninspectable = new Proxy({}, {getOwnPropertyDescriptor() { throw Error('descriptor unavailable'); }});
  for (const cause of [null, undefined, 'raw native failure', Symbol('native failure'), hostile, uninspectable]) {
    await withFixture({}, async f => {
      f.app.click = async id => { f.actions.push(['click', id]); throw cause; };
      await assert.rejects(preserveLiveSetAs(f.app, f.options), error => {
        assert.ok(error instanceof LiveSaveAsGuardError);
        assert.equal(error.phase, 'save-once');
        assert.equal(error.saveAttempted, true);
        assert.equal(error.effect, 'save_effect_unknown');
        assert.equal(error.cause, cause);
        assert.equal(error.destinationDirectory, f.directory);
        assert.equal(typeof error.message, 'string');
        return true;
      });
      assert.equal(f.actions.filter(a => a[0] === 'click').length, 1);
      assert.ok((await fs.stat(f.directory)).isDirectory());
      assert.equal(await fs.readFile(f.source, 'utf8'), 'synthetic source bytes; never opened in Live');
      assert.ok(!f.actions.some(row => ['Cancel', 'Escape', 'super+w', 'super+s'].includes(row[1])));
    });
  }
  assert.equal(messageReads, 0); assert.equal(coercions, 0);
});
await test('an arbitrary exception before Save preserves the no-Save classification', async () => {
  await withFixture({}, async f => {
    f.app.getAXState = async () => { throw null; };
    await rejected(f, /source-preflight/, false);
    assert.deepEqual(f.actions, []);
  });
});
await test('missing/zero/symlink final files and changed source metadata cannot claim success', async () => {
  for (const variant of ['missing', 'zero', 'symlink', 'source']) await withFixture({edit: async ({text, state, source, final}) => {
    if (state === 'final') {
      if (variant === 'missing') await fs.unlink(final);
      else if (variant === 'zero') await fs.writeFile(final, '');
      else if (variant === 'symlink') { await fs.unlink(final); await fs.symlink(source, final); }
      else await fs.appendFile(source, 'changed externally');
    }
    return text;
  }}, f => rejected(f, /ENOENT|destination Set|source Set/, true));
});
await test('replacing the destination directory during Save is unknown and never accepted', async () => {
  await withFixture({edit: async ({text, state, base, directory, final}) => {
    if (state === 'final') {
      const saved = await fs.readFile(final);
      await fs.rename(directory, path.join(base, 'retained-original-directory'));
      await fs.mkdir(directory); await fs.writeFile(final, saved);
    }
    return text;
  }}, f => rejected(f, /directory identity changed/, true));
});
await test('one active helper owns an app, with no concurrent UI sequence', async () => {
  await withFixture({}, async f => {
    const original = f.app.getAXState; let nested = false;
    f.app.getAXState = async args => {
      if (!nested) { nested = true; await assert.rejects(preserveLiveSetAs(f.app, f.options), /already owns/); }
      return original(args);
    };
    await preserveLiveSetAs(f.app, f.options); assert.equal(f.actions.filter(a => a[0] === 'click').length, 1);
  });
});
console.log(`PASS ${count} CUA Save As fake-app/guard checks; no native UI used`);
