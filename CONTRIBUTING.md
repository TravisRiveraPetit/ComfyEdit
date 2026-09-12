# Contributing

Optimize for fewer mistaken edits and less agent context, not for the number
of tool operations. Keep these design rules:

- Return source and its version together. Never silently resolve stale input.
- Accept code as data, not embedded shell programs.
- Share one engine between the CLI and MCP. Give new MCP inputs concrete schemas.
- Fail with specific recovery information. Avoid fuzzy matching that silently
  picks an unintended target.
- Keep source changes previewable and reversible; expose limitations honestly.
- Never run project code merely to inspect or refactor it.
- Add regression tests for real failure cases, especially concurrent changes,
  Unicode, decorators, file modes, and interrupted writes.

## Next priorities

1. Evaluate realistic workflows against ordinary patches: correctness, tool calls,
   bytes of context, and elapsed time. Publish the methodology and raw results.
2. LSP backend: definition/reference navigation and workspace edits with source
   version guards. Negotiate each server's actual capabilities.
3. Typed diagnostics adapters and explicit opt-in validation commands.
4. Windows locking and durable writes; large-repository discovery/search budgets.
5. Three-way selective undo with conflict reporting, never silent overwrites.

File lifecycle operations, complete paginated diff retrieval, bounded discovery,
and guarded recovery are implemented. Extend these contracts rather than adding
parallel mutation paths. `plan_id` means a saved edit preview ID, not agent reasoning.

Keep backends behind the preview/commit contract. Semantic guarantees must
name the language/backend and distinguish proven targets from inferred ones.

## Development

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The tests cover file integrity, stale edits, ambiguous matches, cross-file
renaming, undo, rollback, symbol paging, complete Unicode diff retrieval,
JSON CLI subprocesses, file lifecycle operations, permission guards, actual
process termination and recovery, and real MCP stdio communication. CI runs
Python 3.11–3.13 on Linux, Python 3.12 on macOS, and a core-only install without
MCP or Rope.

Run `.venv/bin/python examples/workflow.py` for a disposable end-to-end example.
ComfyEdit uses the [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
and [Rope's refactoring API](https://rope.readthedocs.io/en/latest/library.html).

## Evaluation ideas

Use fixed tasks in disposable worktrees: rename a public method, edit a nested
function, update config with repeated values, and handle a stale preview.
Measure correct completion, unintended changes, tool calls, input/output
tokens, and wall time against apply_patch. Do not claim a speed or reliability
advantage until measured on representative projects.
