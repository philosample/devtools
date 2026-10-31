---
name: dev-orchestration
description: Organize substantial development tasks with selective subagent delegation, bounded context, clear file ownership, and integrated verification. Use when splitting implementation, investigation, or review work, or auditing agent orchestration; skip routine small edits.
---

# Development orchestration

Use the smallest workflow that completes the task reliably. Optimize total work, including child context, repeated exploration, waiting, and integration. Parallel execution can reduce elapsed time while increasing tokens; do not claim token savings without measurements.

## Choose the execution shape

- **Direct:** small fixes, sequential debugging, work needing the same context, or a task the parent can finish with a few focused calls.
- **Parallel tools:** independent searches, reads, or checks with no need for separate judgment. Filter and bound tool output first.
- **Subagent:** an independent deliverable the parent can validate cheaply; a large noisy investigation whose useful result is small; or an independent review where a fresh perspective matters.
- **Skill or script:** recurring procedure or deterministic work. A persona, long prompt, or separate ownership alone does not justify another agent.

For eligible work, delegate to subagents without an additional permission question, within the user's task and existing tool permissions. Start with one child, usually no more than two concurrently. Add more only when each has useful independent work and expected benefit. Do not spawn a planner, implementer, and reviewer for every task. No recursive delegation unless the parent assigns it explicitly.

## Brief and ownership

Use the available delegation tools; do not create user-owned chats for internal subtasks. Prefer a fresh child context with a complete brief when supported. Fork history only when reconstructing the necessary context would cost more or omit essential constraints. Use the configured defaults: GPT-6.1 Sol at medium effort for orchestration and GPT-6 Luna at high for clear, bounded child tasks. Pass the child model and effort explicitly when the spawn interface supports them; use a fresh context when model overrides require it. Explicit user/session choices take precedence. Use Sol medium/high for ambiguous diagnosis, architecture, complex visual judgment, or consequential independent review. After a failed approach, reassess the brief and evidence before retrying or escalating; avoid repeated blind retries. If a requested model is unavailable, report the limitation and use an available suitable model.

A compact brief can be plain text:

```text
Objective: <one bounded question or deliverable>
Context: <facts, paths, base revision, relevant instructions>
Ownership: <read-only, or exact files/directories you may edit>
Constraints: <interfaces, dependencies, excluded work, allowed side effects>
Done when: <observable acceptance condition and relevant check>
Return: <conclusion; evidence with paths/lines; edits; checks/results;
         unresolved risks or blockers. Keep concise; no raw logs.>
Do not delegate further.
```

Send facts needed for the work, not the whole exploration transcript. For an independent review, provide requirements and the diff but avoid coaching the reviewer toward a suspected answer.

Treat shared workspaces as shared: children must preserve others' edits. Assign disjoint write ownership, keep shared interfaces and integration with the parent, and give every Git-backed agent/session its own worktree and branch, including read-only review sessions and the coordinator. Never share a writable checkout or edit the canonical primary checkout. Inspect/reuse only a suitable worktree already owned by that same session; create another for a new owner. An instruction to read only is a behavioral constraint, not a separate sandbox.

## Coordinate and integrate

The parent continues useful complementary work instead of repeating the child's task. Send changed assumptions promptly; reuse an existing child for related follow-up. Wait through the available completion tools with bounded waits instead of polling repeatedly. Stop obsolete work.

Check returned evidence and the actual diff, resolve interface mismatches, then run the relevant integrated checks. Re-run child checks only if integration changed their assumptions or results are uncertain. Report what passed and any remaining limitation. A child saying “done” is not verification.

## Computer control

A child can use computer control only if its runtime exposes the relevant tools and permissions. Confirm tool availability before assigning UI work; if unavailable, keep UI operations in the parent and delegate code or research.

Delegate substantial, routine UI sequences to a Luna high child when the steps and success condition are clear and handoff overhead is worthwhile. Keep brief interactions direct. Use Sol for ambiguous UI state, complex visual interpretation, or recovery that requires broader task context.

Assign one operator to the shared desktop or browser session at a time, including the parent. Other agents may work on independent files or APIs. Hand off the target app/tab, current state, objective, allowed actions, and stopping condition; the next operator must inspect current state before acting. Separate tabs do not make shared focus or session state independent. Delegation grants no additional authority for external actions.

## Keep the setup small

Keep universal routing in `AGENTS.md`, procedures here, and project commands near the project. Add specialized agent definitions only after repeated work shows a need for different persistent instructions or settings.

When assessing an existing agent system or making efficiency claims, read [references/audit.md](references/audit.md). Do not load that reference for ordinary development delegation.

## Reusable tool promotion

At meaningful checkpoints, evaluate custom scripts/functions for persistence. Reuse the private `philosample/devtools` suite for established general mechanics; promote tested, portable additions there when they justify maintenance. Keep musical behavior, project regressions and transient probes in their owning repo. A promotion should include its command, narrow scope, tests and provenance; do not copy whole private environments or add dependencies merely to wrap a one-off command.
