# Pull request readiness declarations

`python3 -m codex_devtools pr plan-check --policy policy.json --evidence evidence.json --plan plan.md`
validates closed, bounded JSON declarations and exact pins, and checks each
explicit local receipt path against its byte hash and versioned content schema.
It does not execute declared commands or retrieve receipts from a network. A
valid declaration is not an overall readiness decision.

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

Each declared receipt is read only from its explicit relative path beside the
evidence file. The target must be a bounded regular file with no symlink in the
receipt-relative path; reads are limited to 64 KiB. No glob discovery, network
fetch, command execution, or arbitrary log parsing occurs. Exact file bytes
must match `receipt_sha256`, and JSON must use the closed
`codex-devtools-pr-check-receipt-v1` schema. It binds `check_id`, `level`,
base/head/component pins, command, environment, result, exit code, and test
count to the corresponding evidence row. Missing, malformed, oversized,
symlinked, hash-mismatched, content-mismatched, or zero-test test receipts
block. Missing required evidence rows also block.

The report exposes receipt status as `verified`, `failed`, or `unknown` and
removes the receipt unknown gate only after every declared receipt is verified.
This proves local byte/content consistency only; it does not authenticate the
receipt writer or establish independent test provenance. `review-ready` keeps
coordinator approval unknown and `ready:false` regardless of GitHub's
`reviewDecision`.

Both commands require policy schema `codex-devtools-pr-policy-v1`, evidence
schema `codex-devtools-pr-evidence-v1`, and a byte-hashed plan file. Policy
fields are closed: repository/PR/body pins, plan identity and hash, component
hashes, coordinator-selected test-level reasoning, required checks and holds.
Evidence rows pin the exact base/head/component hashes and include result,
exit code, test count, local receipt reference/hash, environment, limitations,
owner and next action. Unknown fields, duplicate JSON keys, malformed or
oversized input,
missing/stale/wrong pins, zero discovered tests, failed/skipped/unknown required
checks, and active holds block the declaration check.

This increment has no independent authenticated coordinator identity boundary.
Consequently `review-ready` always reports `ready: false` and coordinator
approval `unknown`, including when GitHub reports `APPROVED`. Same-account
reviews and worker-authored chat/model labels cannot attest approval. Required
checks and review threads are now read-only observations, while receipt
verification, a future trusted approval boundary and documentation pilot remain
separate issue #75 gates.

## Explicit operator-asserted merge

`python3 -m codex_devtools pr merge --repo OWNER/REPO --number N --policy policy.json --evidence evidence.json --plan plan.md --expected-head SHA --expected-base SHA --expected-tree SHA --body-sha256 SHA256 --review-reference REVIEW_URL --operator-assertion 'I am the designated coordinator and explicitly authorize this exact-head merge.'`
is a separate explicit mutation command. It freshly runs `review-ready`,
requires every blocker except the intentionally unknown coordinator identity
gate to be absent, and requires verified policy/evidence/plan pins, passing
required CI, resolved review threads, and verified evidence receipts. It then
re-reads the open PR head/base/head tree/body hash and the exact approved review
reference for that same head, immediately before one GitHub head-matched merge
request. The head tree and body hash are provided as reviewed pins.
GitHub's merge endpoint supports an exact head SHA precondition, but not an
atomic base SHA precondition; the base is freshly re-read immediately before
the request and any intervening change is outside the adapter's atomic guard.

`--dry-run` performs these reads and validations without making a mutation.
Rejected or unknown network completion is reported without retry. A successful
response is not reported as merged until a terminal read confirms the PR is
merged at the exact head and source tree and provides a merge commit. No cleanup
is performed by this command.

The operator assertion and review reference record the designated coordinator's
intent; they are not independent authentication. Any actor with access to this
command could type the assertion under the shared identity. The command does
not make `review-ready` return ready, treat GitHub's shared-account review as
independent proof, or create a credential/signing mechanism. Actual invocation
remains restricted by the program workflow to the designated coordinator.
