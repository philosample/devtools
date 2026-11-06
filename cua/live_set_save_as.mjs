/* CUA-facing Live Set preservation. Importing this module performs no UI action. */
import * as nodeFiles from 'node:fs/promises';
import path from 'node:path';
import {pathToFileURL} from 'node:url';

const activeApps = new WeakSet();
const MAX_AX_CHARS = 1_000_000;
const MAX_AX_LINES = 10_000;
const requiredCalls = ['getAXState', 'performSecondaryAction', 'pressKey', 'setValue', 'click'];

function requireGuard(condition, message) {
  if (!condition) throw new Error(message);
}

export function absolutePath(value) {
  requireGuard(typeof value === 'string' && value.length <= 4096 && value.startsWith('/'), 'absolute POSIX path required');
  requireGuard(!/[\\\u0000-\u001f\u007f]/.test(value), 'control/backslash path is unsupported');
  requireGuard(value !== '/' && value.split('/').slice(1).every(part => part && part !== '.' && part !== '..'), 'normalized non-root path required');
  return value;
}

export function setURL(value) {
  requireGuard(typeof value === 'string' && value.startsWith('file:///') && !/[?#\r\n]/.test(value), 'local file Set URL required');
  // Validate segments BEFORE URL normalization can erase dot segments.
  const raw = value.slice('file://'.length);
  const decoded = '/' + raw.split('/').slice(1).map(part => {
    const component = decodeURIComponent(part);
    requireGuard(!component.includes('/') && !component.includes('\\'), 'encoded path delimiter forbidden');
    return component;
  }).join('/');
  absolutePath(decoded);
  requireGuard(decoded.endsWith('.als'), 'Set URL must identify an .als file');
  return Object.freeze({path: decoded, url: pathToFileURL(decoded).href});
}

export function parseAX(text) {
  requireGuard(typeof text === 'string' && text.length <= MAX_AX_CHARS, 'bounded AX text required');
  const lines = text.split(/\r?\n/);
  requireGuard(lines.length <= MAX_AX_LINES, 'AX line bound exceeded');
  const ids = new Set();
  return lines.flatMap(line => {
    const match = /^\s*(\d+) (.+)$/.exec(line);
    if (!match) return [];
    const id = Number(match[1]);
    requireGuard(Number.isSafeInteger(id) && !ids.has(id), 'ambiguous AX element ID');
    ids.add(id);
    return [{id, body: match[2]}];
  });
}

function unique(rows, predicate, label) {
  const matches = rows.filter(predicate);
  requireGuard(matches.length === 1, `expected exactly one ${label}`);
  const body = matches[0].body;
  const disabledRole = /^(?:standard window|text field|button|pop up button) \(disabled\)(?: |,|$)/.test(body);
  requireGuard(!disabledRole && !/(?:Enabled: (?:false|0)|\[disabled\])(?:,|$)/.test(body), `${label} is disabled`);
  return matches[0];
}

function withID(row, id) {
  const identifiers = [...row.body.matchAll(/(?:^|[ ,])ID: ([^,]+)/g)];
  return identifiers.length === 1 && identifiers[0][1].trim() === id;
}

function field(rows, id) {
  const result = unique(rows, row => withID(row, id), id);
  requireGuard(/^text field(?: |,|$)/.test(result.body), `${id} must be a text field`);
  return result;
}

function fieldValue(row, id) {
  const values = [...row.body.matchAll(/(?:^|[ ,])Value: /g)];
  const identifiers = [...row.body.matchAll(/(?:^|[ ,])ID: ([^,]+)/g)];
  requireGuard(values.length === 1 && identifiers.length === 1 && identifiers[0][1].trim() === id,
    `unparseable ${id} value`);
  const valueStart = values[0].index + values[0][0].length;
  const identifierStart = identifiers[0].index + identifiers[0][0].lastIndexOf('ID: ');
  let end;
  if (identifierStart > valueStart) {
    end = identifierStart;
    if (row.body.slice(end - 2, end) === ', ') end -= 2;
    else if (row.body[end - 1] === ' ') end -= 1;
    else requireGuard(false, `unparseable ${id} value`);
  } else {
    end = row.body.length;
  }
  requireGuard(end >= valueStart, `unparseable ${id} value`);
  return row.body.slice(valueStart, end);
}

function windowURL(row) {
  const marker = ', URL: ', begin = row.body.indexOf(marker);
  requireGuard(begin >= 0 && row.body.indexOf(marker, begin + marker.length) < 0, 'unparseable standard-window URL');
  let value = row.body.slice(begin + marker.length);
  const trailingRaise = ', Secondary Actions: Raise';
  if (value.endsWith(trailingRaise)) value = value.slice(0, -trailingRaise.length);
  return setURL(value);
}

function stopped(rows, required = true) {
  const play = rows.filter(row => withID(row, 'Transport.Play'));
  requireGuard(!required || play.length === 1, 'exact Play transport control required');
  requireGuard(play.length <= 1, 'ambiguous Play transport control');
  if (play.length) {
    requireGuard(play[0].body.startsWith('checkbox '), 'Play transport checkbox required');
    requireGuard(fieldValue(play[0], 'Transport.Play') === '0', 'transport must already be stopped');
  }
}

function rejectDanger(rows) {
  requireGuard(!rows.some(row => /^button (?:Replace|Allow|Allow Anyway|Discard|Don't Save|Don’t Save)(?: |,|$)/.test(row.body)), 'unexpected overwrite/security/discard prompt');
}

function normalState(rows, expectedURL) {
  rejectDanger(rows);
  requireGuard(!rows.some(row => /^(?:dialog|sheet|alert)(?: |,|$)/.test(row.body)), 'preexisting or unexpected dialog');
  const window = unique(rows, row => row.body.startsWith('standard window '), 'standard Set window');
  requireGuard(windowURL(window).url === expectedURL.url, 'exact Set window URL mismatch');
  stopped(rows);
  requireGuard(/(?:^|, )Secondary Actions: Raise(?:,|$)/.test(window.body), 'Set window does not expose Raise');
  return window;
}

function saveState(rows, expectedSource, pathEntry = false) {
  rejectDanger(rows);
  const dialogs = rows.filter(row => /^(?:dialog|sheet|alert)(?: |,|$)/.test(row.body));
  const expectedModal = dialogs.length === 1 && (/^dialog Save(?:,|$)/.test(dialogs[0].body) ||
    (pathEntry && /^sheet ID: GoToWindow(?:, Secondary Actions: Raise)?$/.test(dialogs[0].body)));
  requireGuard(expectedModal, 'unexpected Save dialog structure');
  // Save panels may hide the Set/transport tree. Any visible identity must match.
  const windows = rows.filter(row => row.body.startsWith('standard window '));
  requireGuard(windows.length <= 1, 'ambiguous Set window during Save');
  if (windows.length) requireGuard(windowURL(windows[0]).url === expectedSource.url, 'source Set changed during Save');
  stopped(rows, false);
  if (pathEntry) {
    const pathField = field(rows, 'PathTextField');
    fieldValue(pathField, 'PathTextField');
    return pathField;
  }
  requireGuard(!rows.some(row => withID(row, 'PathTextField')), 'Go To Folder is still open');
  const name = field(rows, 'saveAsNameTextField');
  const save = unique(rows, row => /^button Save, ID: OKButton(?:,|$)/.test(row.body), 'Save button');
  return {name, save};
}

function whereValue(rows) {
  const marker = 'Where:, Value: ';
  const where = unique(rows, row => row.body.includes(marker), 'Where control');
  requireGuard(/^pop up button (?:Description: )?Where:, Value: /.test(where.body), 'Where control role/label mismatch');
  const start = where.body.indexOf(marker) + marker.length;
  const end = where.body.indexOf(',', start);
  requireGuard(end > start, 'unparseable Where value');
  return where.body.slice(start, end);
}

async function optionalStat(files, filename) {
  try { return await files.lstat(filename); }
  catch (error) { if (error.code === 'ENOENT') return null; throw error; }
}

async function directoryChain(files, directory, outsideGit = false) {
  const chain = [];
  for (let cursor = directory; ; cursor = path.posix.dirname(cursor)) {
    chain.unshift(cursor);
    if (cursor === '/') break;
  }
  for (const cursor of chain) {
    const info = await files.lstat(cursor);
    requireGuard(!info.isSymbolicLink() && info.isDirectory(), 'directory path must have no symlink/non-directory component');
    if (outsideGit) requireGuard(await optionalStat(files, path.posix.join(cursor, '.git')) === null, 'destination must be outside Git');
  }
  requireGuard(await files.realpath(directory) === directory, 'directory realpath mismatch');
}

function sameFile(a, b) {
  return a.dev === b.dev && a.ino === b.ino && a.size === b.size && a.mtimeMs === b.mtimeMs;
}

function config(options) {
  const source = setURL(options.expectedSourceURL);
  const final = setURL(options.expectedFinalURL);
  const directory = absolutePath(options.destinationDirectory);
  const filename = options.destinationFilename;
  requireGuard(typeof filename === 'string' && filename.length <= 255 && filename.endsWith('.als') &&
    filename !== '.als' && !/[\/\\\u0000-\u001f\u007f]/.test(filename) && filename.trim() === filename &&
    !filename.includes(', ID:') && !filename.includes('Value: '), 'explicit plain .als filename required');
  requireGuard(!path.posix.basename(directory).includes(','), 'comma in destination leaf is ambiguous in AX Where text');
  const direct = path.posix.join(directory, filename);
  const project = path.posix.join(directory, filename.slice(0, -4) + ' Project', filename);
  requireGuard(final.path === direct || final.path === project, 'expected final URL must be exact direct or Live Project destination');
  requireGuard(final.path !== source.path, 'source and destination must differ');
  return {source, final, directory, filename};
}

function causeMessage(cause) {
  if (typeof cause === 'string') return cause.slice(0, 256);
  if (cause !== null && (typeof cause === 'object' || typeof cause === 'function')) {
    try {
      const descriptor = Object.getOwnPropertyDescriptor(cause, 'message');
      if (descriptor && Object.hasOwn(descriptor, 'value') && typeof descriptor.value === 'string') {
        return descriptor.value.slice(0, 256);
      }
    } catch { /* Uninspectable errors must retain their operation classification. */ }
  }
  return 'native operation failed';
}

export class LiveSaveAsGuardError extends Error {
  constructor(cause, phase, saveAttempted, destinationDirectory) {
    // Do not invoke accessors or coerce thrown objects while reporting uncertainty.
    super(`${phase}: ${causeMessage(cause)}`, {cause});
    this.name = 'LiveSaveAsGuardError';
    this.phase = phase;
    this.saveAttempted = saveAttempted;
    this.effect = saveAttempted ? 'save_effect_unknown' : 'save_not_attempted';
    this.destinationDirectory = destinationDirectory;
  }
}

/** Requires an explicit native CUA app handle. No CLI, discovery or fallback UI. */
export async function preserveLiveSetAs(app, options, files = nodeFiles) {
  requireGuard(app && requiredCalls.every(name => typeof app[name] === 'function'), 'documented native CUA app handle required');
  requireGuard(!activeApps.has(app), 'a Save As operation already owns this app');
  const {source, final, directory, filename} = config(options);
  let phase = 'source-preflight', saveAttempted = false;
  activeApps.add(app);
  const observe = async () => parseAX(await app.getAXState({emit: false, disableDiffing: true}));
  try {
    let rows = await observe();
    normalState(rows, source);
    await directoryChain(files, path.posix.dirname(source.path));
    const originalSource = await files.lstat(source.path);
    requireGuard(!originalSource.isSymbolicLink() && originalSource.isFile(), 'source Set must be a regular file');
    await directoryChain(files, path.posix.dirname(directory), true);
    requireGuard(await optionalStat(files, directory) === null, 'fresh destination directory required');
    // Exclusive, nonrecursive creation. No preexisting destination is reused.
    await files.mkdir(directory, {mode: 0o700});
    const directoryIdentity = await files.lstat(directory);
    const freshDestination = async () => {
      await directoryChain(files, directory, true);
      const current = await files.lstat(directory);
      requireGuard(current.dev === directoryIdentity.dev && current.ino === directoryIdentity.ino, 'destination directory identity changed');
      requireGuard((await files.readdir(directory)).length === 0, 'destination directory is no longer empty');
      requireGuard(await optionalStat(files, final.path) === null, 'destination file already exists');
    };
    await freshDestination();
    phase = 'raise-source';
    rows = await observe();
    const window = normalState(rows, source);
    await app.performSecondaryAction(window.id, 'Raise');
    phase = 'open-save-as';
    rows = await observe();
    normalState(rows, source);
    await app.pressKey('super+shift+s');
    phase = 'set-filename';
    rows = await observe();
    const {name} = saveState(rows, source);
    await app.setValue(name.id, filename);
    phase = 'open-go-to-folder';
    rows = await observe();
    const named = saveState(rows, source);
    requireGuard(fieldValue(named.name, 'saveAsNameTextField') === filename, 'filename was not set exactly');
    await app.pressKey('super+shift+g');
    phase = 'set-folder';
    rows = await observe();
    const pathField = saveState(rows, source, true);
    await app.setValue(pathField.id, directory);
    phase = 'confirm-folder';
    rows = await observe();
    requireGuard(fieldValue(saveState(rows, source, true), 'PathTextField') === directory, 'Go To Folder path mismatch');
    await app.pressKey('Return');
    phase = 'save-once';
    await freshDestination();
    rows = await observe();
    const ready = saveState(rows, source);
    requireGuard(fieldValue(ready.name, 'saveAsNameTextField') === filename, 'final filename mismatch');
    requireGuard(whereValue(rows) === path.posix.basename(directory), 'Where destination mismatch');
    // Exactly one Save attempt. An exception/final mismatch never triggers retry.
    saveAttempted = true;
    await app.click(ready.save.id);
    phase = 'verify-destination';
    rows = await observe();
    normalState(rows, final);
    await directoryChain(files, path.posix.dirname(final.path), true);
    const finalDirectory = await files.lstat(directory);
    requireGuard(finalDirectory.dev === directoryIdentity.dev && finalDirectory.ino === directoryIdentity.ino, 'destination directory identity changed');
    const saved = await files.lstat(final.path);
    requireGuard(!saved.isSymbolicLink() && saved.isFile() && saved.size > 0, 'destination Set file not observed');
    const retainedSource = await files.lstat(source.path);
    requireGuard(!retainedSource.isSymbolicLink() && retainedSource.isFile() && sameFile(originalSource, retainedSource), 'source Set file metadata changed');
    return {format: 'devtools-cua-live-save-as/v1', status: 'destination_observed',
      sourceURL: source.url, destinationURL: final.url, destinationDirectory: directory,
      destinationFilename: filename, saveAttempts: 1, destinationBytes: saved.size,
      scope: 'fresh AX URL and regular-file observation; not Set-content, durable-write or MIDI proof'};
  } catch (error) {
    throw new LiveSaveAsGuardError(error, phase, saveAttempted, directory);
  } finally { activeApps.delete(app); }
}
