# GitHub Relay — portable handoff for a new ChatGPT session

Status: **MORDOR-specific Relay works today; universal Relay is a proposal, not implemented.** This document lives in the shared DevTools repository as the proposed future home of the generic agent. No code has been migrated yet.

## Purpose

Use GitHub issue comments as a controlled message bus between an ordinary ChatGPT session with a GitHub connector and a local polling agent on the user's Mac. An assistant submits an **allowlisted structured job**; the agent validates it, executes the approved operation, records the job locally, and posts a status-only acknowledgment. The assistant reads the result through GitHub. This is not a remote shell.

## Current working deployment (MORDOR adapter)

- Code: private `mordor-project/mordor-cli`, branch `feature/bootstrap`, local `~/Code/mordor-cli`, draft PR [#2](https://github.com/mordor-project/mordor-cli/pull/2).
- Queue and backlog: [mordor-cli issue #3](https://github.com/mordor-project/mordor-cli/issues/3).
- Executables: `mordor-relay`, `mordor-relay-service`, `mordor-relay-status`; launchd label `org.mordor-project.relay`.
- Agent configured for trusted GitHub login `philosample`, issue #3 and platform checkout `~/Code/MORDOR-worktrees/integration`. Do not assume it is currently running.
- Existing allowed operations: `doctor` with `suite:null`; `test` with `suite` one of `unit`, `integration`, `data-arch`, `frontend`, `neo4j`, `all`.
- Actual remote job `doctor` and `test unit` executions have returned `completed` and exit code 0. A later display test `doctor` also returned exit code 0. These are historical observations, not evidence of current health.
- The local SQLite ledger prevents normal re-execution of already claimed job IDs; this is not yet a robust distributed locking or delivery guarantee.

### Existing job protocol (V1)

Post a single GitHub issue comment as the authorized author:

```text
MORDOR_RELAY_JOB_V1:{"id":"relay_test_20261114_01","action":"test","suite":"unit"}
```

IDs must be unique and match the agent's validation rules. The agent ignores unsupported commands and untrusted comment authors.

Result comments look like:

```text
MORDOR_RELAY_RESULT_V1: {"id":"relay_test_20261114_01","status":"completed","exit_code":0}
```

Possible states include `completed`, `failed`, `timeout`, `launch_failed`. Exit code zero means the subprocess returned success; it does not prove every desired assertion ran. Raw stdout/stderr are intentionally not sent to GitHub.

## Instructions to a new ordinary ChatGPT session

1. Discover and use the connected GitHub tools. Verify access to the designated private queue and read recent comments.
2. Read this document and the installed agent's protocol before posting. Confirm whether the local watcher/service is actually running; absence of results is not proof of execution.
3. Look for existing matching jobs/results. For an approved action, generate a unique job ID and post exactly one valid V1 comment.
4. Read the queue for the matching result; distinguish submitted, acknowledged, completed, failed, timed out, and unknown. Never invent a result or assume a comment triggers execution.
5. If the request is not one of the allowed actions, **do not** post shell snippets or attempt to extend permissions via comments. Request implementation of a new, reviewed adapter.
6. Do not use the queue to access production evidence, credentials, private files or destructive operations. No unrestricted command execution.

GitHub connector access and local agent operation are independent. A connected assistant cannot automatically control the user's computer without a running, authorized agent.

## Universal Relay — target architecture (NOT YET SUPPORTED)

**Repository ownership:** move the generic core to `philosample/devtools` as reusable Python tooling; keep `mordor-cli` as an adapter/client until a tested migration is complete. DevTools' existing guidance prefers a standard-library Python CLI usable via `python3 -m codex_devtools` without installation. Inspect its architecture before choosing whether Relay should be a `codex_devtools relay` command or a separately packaged module. The Relay protocol should work with ordinary ChatGPT sessions and other authorized GitHub-capable clients, not depend on Codex.

**Core modules:** GitHub transport, strict identity and queue binding, schema/version validation, durable job state, single-agent locking, safe acknowledgments and retries, timeouts, resource limits, redacted local diagnostics, observability, and macOS service management.

**Local project registry:** map stable project IDs to fixed approved checkout roots, GitHub repository identity, adapters and explicitly allowed actions. Never accept arbitrary working directories, executable paths or shell commands from issue comments. Separate read-only, development-write and privileged permissions.

**Adapters:** generic Python tests, Node tests/builds, read-only Git diagnostics and other reviewed project-specific operations. MORDOR's existing test/doctor actions become one adapter. Do not silently expose the staged Git sync helper: it lacks full origin/path and self-update hardening.

**Proposed V2 payload** (design only):

```json
{"protocol":"GH_RELAY_V2","job_id":"job_20261114_001","project":"example-project","action":"test","parameters":{"suite":"unit"}}
```

The registry must be trusted local configuration, not editable by remote jobs. A project-specific backend can consume a read-only status adapter; a generic dashboard must not require MORDOR's evidence schema or UI.

## Safety and migration gates

- Preserve the existing V1 queue and launchd service until a parallel V2 agent passes synthetic cross-project tests; do not break active jobs.
- Confirm GitHub repository identity, permitted authors and permissions; add defense against account compromise and malicious comments.
- Enforce single-process execution, secure local state permissions, bounded pagination, retry/backoff, durable claim/result delivery and crash recovery without duplicate execution.
- Add structured telemetry: action, suite, created/started/finished timestamps, duration, last poll, last successful GitHub contact, and result-delivery status. Never upload private logs, secrets or evidence to GitHub.
- Separate installation from service start; keep `launchd` opt-in. Avoid updating code while its own process is running.
- Add `python -m compileall` and test gates: past escaped newline literals in `relay.py` caused GitHub CI syntax failures.
- Validate on a **synthetic second repository** before declaring generic operation. No production DB, destructive Git, or Docker volume removal.

## Related tracking

- [MORDOR Relay backlog #3](https://github.com/mordor-project/mordor-cli/issues/3)
- [CLI backlog #1](https://github.com/mordor-project/mordor-cli/issues/1)
- [MORDOR admin dashboard #40](https://github.com/mordor-project/mordor/issues/40)
- [Platform/Codex handoff](https://github.com/mordor-project/mordor/blob/feature/integration/docs/development/CODEX_RELAY_HANDOFF.md)

## Definition of done

A fresh ChatGPT session can discover the connection, submit a permitted job to a registered project, receive a trustworthy result, and continue without copy/paste terminal commands. This must be demonstrated with a second synthetic project and verified local/CI tests before migrating MORDOR or retiring the V1 agent.
