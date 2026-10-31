# Opt-in test-harness mailbox (DEV-04)

This transport removes repeated manual harness-button interactions. It does not control Max, Live, MIDI, routing, a desktop, or a production device. A test harness must explicitly load `codex_devtools/harness_mailbox.cjs`, publish capabilities and route the callback through its existing control inlet/driver state machine. No service starts on import. No network listener or package dependency is used. The first musical consumer and native validation are separate work.

## Register from a Node harness

```javascript
const {createHarnessMailbox} = require('/reviewed/devtools/codex_devtools/harness_mailbox.cjs');
const mailbox = createHarnessMailbox({
  // Optional; default ~/.local/state/codex-devtools/harness
  root: '/private/local/path/test-harness-mailbox',
  pins: {
    artifact_sha256: '<64 lowercase hex characters>',
    components: {driver: '<64 lowercase hex characters>'}
  },
  commands: {
    configure: {count: {type: 'integer', min: 1, max: 100}},
    arm: {},
    start: {mode: {enum: ['diagnostic', 'measured']}}
  },
  timeoutMs: 10000, // 100..60000, monotonic receiver deadline
  leaseMs: 3000,   // 200..30000, publication freshness bound
  handle(command, args, completion, correlation) {
    // Domain-owned implementation, provided separately:
    // register completion with the driver's matching run/request before sending;
    // send through the actual existing harness control inlet.
    domainControlInlet(command, args, correlation, {
      completed: completion.completed,
      failed: completion.failed
    });
  }
});
console.log(mailbox.descriptorPath);
```

The pins are mandatory caller-supplied provenance assertions. The integrating harness must hash the actual loaded/tested artifact and components and check them before registration; the transport cannot establish what Max loaded. Keep the reviewed helper version in the consumer's component pins. Do not hash unrelated repository heads as substitutes for exact component bytes.

`handle` receives a command, a copied argument object, explicit completion callbacks and `{request, sequence}`. Returning from `handle`, returning a Promise, or calling `maxApi.outlet` establishes no domain completion. A driver must report the matching command's terminal state before calling `completed()` or `failed()`. An exception or Promise rejection fences the instance because effects are unknown. `failed()` asserts a known terminal domain failure, so it releases the actor. Completion callbacks return false if no longer current. A domain with unresolved effects must let the timeout fence the instance, never report a known terminal failure merely to clear a lock.

Capabilities allow at most 16 named commands and 32 required scalar arguments each. Rules are bounded finite numeric/integer ranges, boolean, or a finite string enum (32 entries, 128 characters each). No free-form scripts, expressions, paths, device ports or callbacks are accepted from command data. There are no optional/extra arguments. Domain capabilities must remain restricted to the test harness; do not expose general message forwarding or host control.

## Select and submit from Python

```sh
python3 -m codex_devtools harness discover --root /private/local/path/test-harness-mailbox
python3 -m codex_devtools harness status --descriptor /private/local/path/test-harness-mailbox/INSTANCE/descriptor.json
python3 -m codex_devtools harness submit \
  --descriptor /private/local/path/test-harness-mailbox/INSTANCE/descriptor.json \
  --expected-pins /private/local/path/expected-pins.json \
  --command configure --args '{"count":10}' --wait 5
```

`expected-pins.json` is exactly the artifact/component pins object shown above, prepared independently for the intended candidate. There is no first-match target selection. Discovery/status are read-only and report timestamp freshness only, never authoritative process liveness. Submit requires the exact descriptor and exact artifact plus full component pin map. CLI exit 0 means the domain explicitly reported completed; exit 1 means failed, unknown_effect, enqueued or unconfirmed; exit 2 means a client/transport error. Each result includes instance, nonce, pins, request and sequence for correlation. Neither successful delivery nor completed domain state establishes native MIDI output correctness.

## Protocol and lifecycle

The POSIX root and instance directories must be owned by the current user and private (no group/other permission bits), outside any Git tree, and have no symlink ancestors. Use a resolved `/private/...` temp path on macOS; `/var` and `/tmp` may be symlinks. Default local state is outside Git and deployment destinations. No global configuration, permissions or installed assets are changed. JSON records are limited to 64 KiB on read. Arguments are limited to 16 KiB before submission. The trust boundary excludes malicious processes running as the same user: nonces prevent accidental cross-instance use, not attacks by processes that can read/write the same files. This is a local cooperative test protocol, not a security sandbox.

Registration exclusively creates `ROOT/owner`, creates a fresh UUID instance directory and cryptographically random nonce, and atomically publishes `descriptor.json`. Schema format is `codex-harness-mailbox-v1`. The descriptor contains identity/pins, command rules, last sequence, `ready|pending|fenced|closed` state, pending request/sequence or null, wall-clock `expires_at_ms` and `timeout_ms`. The root ownership record prevents even a fresh process from silently replacing an old or unresolved instance.

A submitter exclusively creates `INSTANCE/actor`, publishes its request identity, rereads the descriptor, then atomically publishes fixed `command.json`. Its strict fields are `{format, instance, nonce, pins, request, sequence, command, args, deadline_ms}`. Sequence is exactly last sequence + 1. Receiver validates all fields, allowed schema, deadline, nonce, pins and actor/request match before invoking the callback once. It records sequence and pending state first. No command is retried or replayed. An invalid envelope conservatively fences the instance without invoking the callback.

A pending command continues to own the actor until explicit correlated completion. The CLI may return `enqueued` after its own wait expires if the descriptor reports that exact pending request; otherwise it returns `unconfirmed`. It never releases the actor after publishing. The receiver atomically publishes one terminal `ack.REQUEST.json` with identity/pins, request, sequence, status and detail, then releases only its own terminal command/actor. Per-request ACK paths prevent a later command overwriting the previous result. The newest 32 terminal ACKs are retained per instance; old instance directories are not automatically deleted. Collect needed evidence promptly. All JSON publication uses temporary files plus rename; this contract is process-atomic visibility, not a power-loss durability guarantee.

Timeouts use Node's own monotonic clock, including checks at completion. Receiver polling gaps beyond the lease and wall/monotonic clock drift greater than 250 ms fence the instance. Python uses its own monotonic clock only for its local wait; the protocol never compares clock origins. Wall-clock command deadlines reject stale input, and wall-clock descriptor expiry is only a client freshness check. A receiver timeout gives `unknown_effect`, retains command/actor/pending, ignores late completion and disables subsequent dispatch. It does not claim cancellation. A receiver crash leaves ownership/actor state; freshness expiry does not authorize clearing them.

`mailbox.close()` succeeds only with no pending/fenced work, publishes closed and relinquishes root ownership. Otherwise reconcile the actual domain state or unload the target first. **Only after that action**, an explicit new registration may pass `reconciliation: 'domain-reconciled'` or `'target-unloaded'` to replace old root ownership. This option is an operator assertion, never inferred automatically from a new nonce, PID, timeout, heartbeat, descriptor or process restart. Old receivers notice lost root ownership and stop polling; the transport cannot cancel already-running external domain work. Never use reconciliation as an automatic retry mechanism.

## Verification

```sh
python3 -m unittest discover -s tests -p test_harness_control.py -v
```

Tests require an actual discovered Node executable (absence is a failing prerequisite, not a skip). They launch the real CommonJS helper and Python CLI in separate processes with private temporary mailboxes, using a stub callback that counts real dispatches. Positive cases assert exact positive counts; two simultaneous Python processes produce one callback. Tests cover explicit asynchronous completion, ACK retention, pins/nonces/mixed instances, replay, invalid commands/arguments, stale/crashed ownership, manual reconciliation, callback exceptions, wall discontinuity and both asynchronous and synchronously blocked timeout fencing. They do not run Max/Live, open MIDI ports or establish native musical behavior.
