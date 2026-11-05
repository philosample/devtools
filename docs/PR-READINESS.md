# Pull request readiness declarations

`python3 -m codex_devtools pr plan-check --policy policy.json --evidence evidence.json --plan plan.md`
validates closed, bounded JSON declarations and exact pins. It does not execute
declared commands or dereference receipt paths. A valid declaration is not an
overall readiness decision.

`python3 -m codex_devtools pr review-ready --repo OWNER/REPO --number N --policy policy.json --evidence evidence.json --plan plan.md`
adds one fixed, read-only `gh pr view` query and compares the current PR number,
head, base, state and description hash with the declaration. GitHub review
decision is reported as observed metadata only.

The report exposes blocking `unknown` gates for GitHub required CI checks,
review submissions/unresolved thread findings, and actual receipt-content/hash
verification. The command does not fetch check runs or review threads, or
retrieve receipts. An observed `CHANGES_REQUESTED` adds its own blocking
`review_disposition` gate. These declarations cannot establish that review
findings are resolved or that required CI passed.

Both commands require policy schema `codex-devtools-pr-policy-v1`, evidence
schema `codex-devtools-pr-evidence-v1`, and a byte-hashed plan file. Policy
fields are closed: repository/PR/body pins, plan identity and hash, component
hashes, coordinator-selected test-level reasoning, required checks and holds.
Evidence rows pin the exact base/head/component hashes and include result,
exit code, test count, receipt hash, environment, limitations, owner and next
action. Unknown fields, duplicate JSON keys, malformed or oversized input,
missing/stale/wrong pins, zero discovered tests, failed/skipped/unknown required
checks, and active holds block the declaration check.

This increment has no independent authenticated coordinator identity boundary.
Consequently `review-ready` always reports `ready: false` and coordinator
approval `unknown`, including when GitHub reports `APPROVED`. Same-account
reviews and worker-authored chat/model labels cannot attest approval. Required
checks, review findings/thread state, receipt verification, a future trusted
approval boundary and documentation pilot remain separate issue #75 gates.
