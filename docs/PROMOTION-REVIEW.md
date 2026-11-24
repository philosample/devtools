# Custom script promotion review — 2026-10-31

This is the review record for selecting reusable utilities from the existing local Max for Live work. Source paths below are provenance only; this package does not import or depend on those project-local scripts.

| Existing script | Reusable behavior | Disposition |
| --- | --- | --- |
| `host/tools/recover_mxf.py` | `mx@c` chunk bounds, directory offsets, entry names, payload boundaries, and exact-byte inventory/extraction | Promoted and hardened in `codex_devtools.maxfiles`; generic archive logic only |
| `host/tools/validate_recovery.py` | Hash and JSON syntax checks against a one-off extracted project manifest | The generic SHA-256 and patch JSON checks inform this suite; project recovery provenance/manifest checks remain in `host/` |
| `tr1000/tools/m4l_tool.py` | AMXD wrapper decoding, dependency archive inspection, graph port checks, recursive inline patchers | Read-only inspection and graph mechanics promoted; device building and dependency replacement remain project-specific |

The new suite adds a deterministic relative-path hash manifest because it is useful across repositories. Device-specific mappings and Markov regression models remain with their project. Transient runtime harnesses and project fixtures stay project-local; they encode project/runtime assumptions that are not a general CLI contract.

The archive implementation is intentionally limited to the observed `mx@c` layout with root payload at offset 16 and `dlst` directory records. It does not claim compatibility with every historical Max archive variant. Graph validation checks inline JSON patchers and declared inlet/outlet indices; it does not prove object behavior, external patch loading, Max execution, Live loading, or hardware response.

## Additional checkpoint decisions

- The master-plan bootstrap is project-specific: its durable results are the studio backlog/plan, not a global devtool.
- Markov Max-File dispatch tests, Host decoder tests, tempo guard regressions and TR capture fixtures stay in their owning repos.
- Curated instruction/skill snapshots are tracked in guidance/; personal Codex state and credentials are excluded. Export/install automation is deferred until repeat usage establishes a stable interface.
