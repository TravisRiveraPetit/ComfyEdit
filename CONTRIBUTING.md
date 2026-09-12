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

1. Durable recovery: interrupted transaction inspection and guarded recovery.
2. LSP backend: definition/reference navigation and workspace edits with source
   version guards. Negotiate each server's actual capabilities.
3. File lifecycle operations and full paginated diff retrieval.
4. Typed diagnostics adapters and explicit opt-in validation commands.
5. Windows locking; benchmark large repositories and context/token costs.
6. Three-way selective undo with conflict reporting, never silent overwrites.

Keep backends behind the preview/commit contract. Semantic guarantees must
name the language/backend and distinguish proven targets from inferred ones.

## Evaluation ideas

Use fixed tasks in disposable worktrees: rename a public method, edit a nested
function, update config with repeated values, and handle a stale preview.
Measure correct completion, unintended changes, tool calls, input/output
tokens, and wall time against apply_patch. Do not claim a speed or reliability
advantage until measured on representative projects.
