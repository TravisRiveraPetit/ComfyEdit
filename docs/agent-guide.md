# ComfyEdit: agent quick reference

Use ComfyEdit for guarded batches, Python symbol edits/renames, and changes that
need reliable reversal. Ordinary patch tools remain useful for simple edits;
project test execution belongs to your normal execution tool.

## Choose a tool

| Need | Tool |
| --- | --- |
| Find paths | `list_files(pattern="*.py")` |
| Find literal text | `search_code(query="solve", pattern="*.py")` |
| Inspect a target and get its version | `read_code(file="src/solver.py", symbol="Solver.solve")` |
| Stage edits, including file creation/moves/deletion | `preview(edits=[...])` |
| Rename a Python definition and inferred callers | `rename_symbol(file, symbol, new_name, version)` |
| Inspect remaining preview text | `read_diff(plan_id, offset=next_offset)` |
| Recover a lost preview receipt | `list_previews()` |
| Remove an unused preview | `discard_preview(plan_id)` |
| Apply the exact saved edit preview | `commit_edit(plan_id)` |
| Stage reversal | `undo_edit(undo_id)`; review and commit its preview |
| Inspect an interrupted write | `list_transactions()` |
| Restore or finish an interrupted write | `recover_transaction(transaction_id, action="rollback" or "finish")` |

**`plan_id` means saved edit preview ID.** It is unrelated to the agent's thinking,
reasoning, or planning mode. `preview` stores proposed changes and returns this
receipt; `commit_edit` applies them. `undo_id` identifies an applied transaction.

## Spend context on the change

- Narrow discovery/search patterns. Search is literal, case-sensitive, and live;
  each match carries the exact source version it came from.
- Read the relevant symbol or line range. Use `include_symbols=false` when an
  outline would repeat information you already have.
- A read can stop mid-line. Continue with `start_line=next_line`,
  `start_column=next_column`, the same `symbol`, and the original `version`.
- Keep fetching diff pages while `next_offset` is non-null. Offsets and columns
  count Unicode characters, not bytes. Pages can split lines.
- Batch related edits. Every edit uses the path's version from before the batch,
  even when an earlier edit changes it. Use `version: null` only for a path that
  was originally absent and has been created or populated by a move in the batch.
- If `preview` returns `status: "no_change"`, there is no receipt to commit.

## Edit shapes

```json
{"operation":"replace_text","file":"config.toml","version":"<read version>","old":"timeout = 10","new":"timeout = 30"}
{"operation":"replace_symbol","file":"solver.py","version":"<read version>","symbol":"Solver.solve","code":"def solve(self):\n    return 42"}
{"operation":"create_file","file":"tests/test_solver.py","code":"def test_answer():\n    assert 42 == 42\n"}
{"operation":"move_file","file":"solver.py","destination":"src/solver.py","version":"<read version>"}
{"operation":"delete_file","file":"obsolete.txt","version":"<read version>"}
```

Each line above is a separate edit object; pass the desired objects in one
`edits` array. Moves do not update imports. Replacement symbol code includes
its decorators. Exact text replacement defaults to one match; additional matches
require an explicit `expected_matches` count.

## Recover without guessing

Always inspect MCP `isError` first, then inspect `ok` in the returned application
payload. The CLI exposes only the application payload and uses exit code 1 for
`ok: false`. On `stale_version`, reread and rebuild the
preview; never substitute a new hash into an old proposal without reviewing it.
On `recovery_required`, inspect the journal's per-file states. Recovery directly
writes the saved original (`rollback`) or proposed (`finish`) state. It refuses
files changed to anything else. Preserve metadata if corruption is reported.

After a successful commit, run the project's appropriate tests. Syntax validation
alone does not establish correctness; Rope cannot reliably cover dynamic callers.

Example pending-transaction summary:

```json
{
  "transaction_id": "…",
  "status": "pending",
  "files": [
    {"file": "src/a.py", "state": "after"},
    {"file": "tests/test_a.py", "state": "before"},
    {"file": "src/other.py", "state": "conflict"}
  ]
}
```

`before` means the recorded original is present, `after` means the proposed
state is present, `both` means the file is unchanged from both recorded states
(for example an absent path), and `conflict` means it matches neither. Recovery
checks every file before writing. Resolve conflicts with your normal editor or
terminal, then rebuild a fresh preview when the intended result is known.
