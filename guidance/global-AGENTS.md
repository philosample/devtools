# Development orchestration

Optimize for correct completed work and total token use, including child agents and integration.

- Work directly for small, tightly coupled tasks. Batch independent tool reads before considering more agents.
- For substantial development work, use `$dev-orchestration` when dividing work or deciding whether delegation will help.
- Delegate bounded independent work to subagents when parallel progress, context isolation, or an independent check justifies the extra context and coordination. This instruction requests delegation in those cases; do not ask again merely to spawn an agent. Start with one child; add a second only for another useful independent task.
- Give each child an objective, relevant paths, ownership, constraints, acceptance check, and concise return format. Prefer a fresh context with a self-contained brief when supported. Keep dependent decisions and integration in the parent.
- Assign disjoint file ownership. Use one active operator for a shared desktop or browser session; hand it off explicitly. Prefer APIs or CLI tools when they accomplish the task reliably.
- Load only needed skills and references. Request evidence and short conclusions rather than copied logs. Verify the integrated result with checks appropriate to the change.

- Model routing: prefer GPT-6.1 Sol at medium effort for orchestration and GPT-6 Luna at high for bounded child tasks. Escalate to Sol medium/high when ambiguity, failures, or verification needs justify it. Explicit user/session model choices take precedence.
- Delegate substantial, routine computer-control sequences to a Luna high child when tools are available and handoff is worthwhile. Keep complex visual judgment or ambiguous UI recovery with Sol; keep brief interactions direct. Preserve single-operator ownership.

# Development memory and Git tracking

- Keep reusable project code, tests, source data, architecture decisions, and concise repository instructions in Git. Keep secrets, caches, machine-specific routing, and disposable build output out. Preserve required assets and reviewed generated artifacts; do not ignore them merely because they are binary or generated.
- Track substantial work with stable issue/story IDs, dependencies, acceptance checks, and a branch/PR. Put current status and execution evidence in the issue/PR; keep durable architecture and contracts in repository docs. Avoid competing status copies.
- Before parallel work, give each session a bounded story and disjoint file ownership; assign one owner to shared contracts and integration. Every agent/session performing Git-backed work, including the coordinator, must use its own Git worktree and branch. Never share a writable worktree or edit the canonical primary checkout; inspect existing worktrees first and preserve other agents’ changes.
- At meaningful checkpoints, consider whether a verified discovery would prevent repeated failure or rediscovery. Update the narrowest useful location: repository AGENTS.md for local invariants, a skill for a repeatable procedure, global AGENTS.md for broadly applicable preferences. Do not require a guidance edit when there is no useful new lesson.
- Preserve the evidence, applicability, and limits of each lesson. Do not turn a one-off workaround, untested inference, or temporary service failure into a universal rule. Consolidate or remove stale instructions instead of continually appending rules.
- Pair a concrete bug fix with a focused regression check when practical. Verify the integrated behavior; passing an isolated model or helper does not establish its UI, adapter, host, or external effects. Record exact source/artifact versions for runtime evidence.
- Discover existing test/tool entrypoints in repository AGENTS.md and testing docs before writing another runner. Prefer deterministic CLI checks and saved structured evidence over repeated UI work. Run focused checks and required integration checks; repeat or broaden only for changed sources, failures or unresolved concerns. A runner that finds zero tests is not passing coverage. For MIDI capture/export/assertions or M4L transport evidence use `$midi-capture-testing` and the owning repo's acceptance gates.
- End work with a compact handoff: story/PR, commit, checks, unresolved blocker, next action, and any reusable guidance changed. Reuse saved evidence instead of restarting discovery.

- Evaluate custom scripts/functions for persistence at meaningful checkpoints. Check the private `philosample/devtools` suite before duplicating general archive, graph, hashing or inspection logic. Locate its existing checkout from the project/workspace registry; promote tested reusable mechanics there and keep domain-specific tests/data with their project.

- Keep canonical repositories in `~/Code/<repository>`; use per-agent worktrees under `~/Code/.worktrees/<repository>/` or the app-managed worktree location. Installed libraries/apps are deployment destinations, not development checkouts. Propagate only explicitly listed reviewed artifacts through a hash-checked plan with backups; do not bulk-sync repositories into runtime search paths.
- For this studio’s Max development, the user owns Max for Live only. All new musical deliverables must be M4L-compatible and authorable under that license; do not assume full Max, standalone rebuilding, or Gen/MC authoring. Record a concrete blocker before proposing a purchase.
