# Working on ComfyEdit

ComfyEdit helps coding agents make explicit, version-checked source changes.
Keep the core usable without optional dependencies or project-code execution.

## Map

- `src/comfyedit/engine.py`: source reads, edit previews, snapshots, commits, undo,
  transaction journals, recovery, and optional Rope refactoring.
- `src/comfyedit/discovery.py`: file inventory and bounded literal search.
- `src/comfyedit/cli.py`: shared dispatch and JSON stdin/stdout interface.
- `src/comfyedit/server.py`: concrete MCP schemas; delegates to shared dispatch.
- `TODO.md`: reviewer-driven work queue; update statuses as concerns are addressed.
- `tests/`: engine, lifecycle/recovery, discovery, CLI subprocess, and MCP tests.
- `docs/agent-guide.md`: using ComfyEdit to edit another repository.
- `docs/mcp-reference.md`: compact public tool and argument reference.

## Development loop

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[agent]'
.venv/bin/python -m unittest discover -s tests -v
git diff --check
```

For a core-only install, use `-e .`; optional integration tests skip when their
dependencies are absent. Use temporary projects for destructive test scenarios.
`examples/workflow.py` creates and cleans up its own temporary project.

## Invariants

- All normal source mutations go through preview, guard checks, and a journal.
  Explicit recovery uses the journal's exact before/after states.
- Versions describe original UTF-8 bytes for each path in a batch. `null` means
  the path was originally absent. Never silently accept stale input.
- Preserve ordinary mode bits and distinguish missing files from empty files.
- Reject symlinks, protected metadata, special files, and path escapes.
- Never execute project code/configuration while discovering or refactoring it.
- Do not describe multi-file commits as crash-atomic or Rope inference as complete.
- New operations need failure-path tests as well as a successful round trip.
  Exercise real CLI/MCP boundaries when changing their public contracts.
- Keep documentation, schemas, recovery instructions, and error codes aligned.
  `plan_id` is an edit-preview receipt; use “preview” in prose and examples.
- MCP transport errors (`isError`) and application errors (`ok: false`) are
  separate layers; document and test both when changing public tools.
