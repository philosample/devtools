# Pull request readiness declarations

`python3 -m codex_devtools pr plan-check --policy policy.json --evidence evidence.json --plan plan.md`
validates closed, bounded JSON declarations and exact pins. It does not execute
declared commands or dereference receipt paths. A valid declaration is not an
overall readiness decision.

`python3 -m codex_devtools pr review-ready --repo OWNER/REPO --number N --policy policy.json --evidence evidence.json --plan plan.md`
reads the PR snapshot before and after fixed read-only GitHub observations and
compares number, head, base, state and description hash with the declaration.
It reads required checks with `gh pr checks --required --json` and review
threads through a paginated, fixed GraphQL query. Snapshot changes invalidate
the check and thread observations. Check field names and `--required` behavior
follow the [GitHub CLI `pr checks` interface](https://cli.github.com/manual/gh_pr_checks).

Only required checks with bucket `pass` and state `SUCCESS` count as passed.
Failed, pending, cancelled, skipped, neutral and unknown states block. Empty
required-check results are ambiguous and remain unknown. Missing access,
malformed output, duplicate check identity or an oversized response also leaves
the check gate unknown.

Review threads are fetched in pages of 100, up to 10 pages. The report removes
the thread unknown gate only after a complete pagination result. Unresolved
threads block; inaccessible, malformed, inconsistent or truncated results
remain unknown. `CHANGES_REQUESTED` adds its own blocking
`review_disposition` gate even if thread reads are complete.

Receipt references are still declarations only: actual receipt content and
hashes are not retrieved or verified, so the `receipt_verification` gate stays
unknown. `review-ready` also keeps coordinator approval unknown and
`ready:false` regardless of GitHub's `reviewDecision`.

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
checks and review threads are now read-only observations, while receipt
verification, a future trusted approval boundary and documentation pilot remain
separate issue #75 gates.
