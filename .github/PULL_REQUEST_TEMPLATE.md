## Change and reasoning

<!-- Explain the concrete problem, resulting behavior, scope and material risks. -->

## Visual before and after (when applicable)

<!-- Put readable before/after images directly in the PR description. Keep the same patcher/view, viewport size and zoom. Use GitHub attachments or commit-pinned GitHub blob links for private-repository images; raw.githubusercontent.com links may 404 for reviewers. Label source-derived renders versus native application screenshots, and include the exact artifact hash and renderer/host. Write “Not applicable” when the change has no visible result. -->

- Before image:
- After image:
- Artifact hash and renderer/host:
- Image provenance: source-derived / native / not applicable

## Persisted plan and ownership

- Plan ID and link/path under `~/workstation-docs/plans`:
- Story/issue:
- Implementer:
- Designated coordinator (chat/identity):
- Intended base branch:

## Consolidated review (when combining related PRs)

<!-- Prefer one frozen combined diff and affected test decision for compatible stacks. Reuse reviews and evidence for unchanged components. Do not combine unrelated work merely to reduce PR count. -->

- Combined review head and intended integration target:
- Integration owner and registered worktree:
- Prior reviews/evidence reused, with exact unchanged component pins:
- New substantive delta requiring review/checks:

| Source PR / story | Frozen source head | Dependency / retained hold | Exact incorporation evidence in combined head | Replacement / disposition |
|---|---|---|---|---|
| | | | | |

<!-- Verify actual resulting content; ancestry alone is insufficient. Link the replacement before closing a superseded PR. Consolidation does not waive source-specific native, hardware, release or other explicit gates. -->

## Test-level decision and reasoning

<!-- The GPT-6.1 Sol coordinator chooses the required testing level before execution. Link that decision; a worker may propose changes but cannot waive a gate. Explain why the selected checks cover this change and why omitted levels are not required. Preserve repository-mandated gates. -->

- Coordinator decision reference:
- Required development checks:
- Required development/integration checks:
- Additional applicable gates (native, wire, recall, hardware, visual, deployment/release):
- Levels not required and specific reasoning:
- Checks blocking this merge versus gates retained for later acceptance:

## Dev testing

<!-- Fill actual commands/results and durable evidence. Zero discovered tests is not a pass. For prose-only changes, record the chosen documentation checks and why runtime tests are unnecessary. -->

| Check ID | Exact command/check | Tested source/component pin | Result and test count | Evidence/receipt |
|---|---|---|---|---|
| | | | | |

## Dev/integration testing

<!-- Describe the actual composed boundary and expected/observed behavior. Distinguish source/model, Max/Live native, virtual wire, save/reopen and physical hardware evidence. Never substitute an isolated helper/model for its host or external effects. -->

| Check ID / evidence level | Environment and artifact pins | Scenario / expected behavior | Actual result | Evidence/receipt |
|---|---|---|---|---|
| | | | | |

## Failures, skips, limitations and holds

<!-- List every failure/skip, its effect on readiness, and the next owner/action. A required skipped test blocks readiness. An unrelated baseline failure needs an explicit coordinator classification and supporting evidence. Use “none” only after checking. -->

- Failed or skipped checks:
- Unverified behavior:
- Active hold, owner and next action:
- Follow-up issue(s):

## Coordinator review and merge disposition

<!-- These references must resolve to coordinator-originated review/authorization for the frozen head. Implementer checkboxes or a ready label are not independent review proof. Recheck current head/base, description/evidence and required checks immediately before merge. -->

- Exact frozen source head:
- Relevant component/artifact hashes:
- Coordinator source/evidence review reference and reviewed head:
- Findings resolved or explicitly classified:
- Disposition: merge / named hold / changes requested / superseded:
- Exact-head merge authorization (when eligible):
- Readiness tool receipt (when the tool is available; do not invent one):

## Merge and owner cleanup evidence

<!-- Complete after a confirmed terminal merge. Do not run dependent cleanup while merge outcome is unknown. Preserve active, dirty or uncertain worktrees and all native/release evidence. -->

- Confirmed merged PR head and merge commit:
- Owner's clean-worktree/ownership verification:
- Registry release and guarded cleanup result, or explicit retained-worktree reason:
