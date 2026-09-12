# ComfyEdit

**An agent should spend its context on the change, not on escaping shell commands.**

ComfyEdit is a local editing workspace for coding agents. Find code, read bounded
source pages, preview a batch of edits, and apply or undo it with version checks.
Batches can create, move, delete, and edit files. Interrupted transactions can be
inspected and recovered. No API key, model, or network service required.

Status: **tested prototype, v0.2**. Linux and macOS; Python 3.11+. Not published
to PyPI. Install from this repository.

`plan_id` means the opaque ID of a saved edit preview. It is not an agent's
reasoning plan. If an agent loses the ID, call `list_previews()` to find recent
unapplied previews; call `discard_preview(plan_id)` to remove one that is no
longer needed. Applied previews remain available for diff history and undo.

## Install

From the checked-out repository:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[agent]'
```

The core and CLI have no runtime dependencies. Install `.` for those alone;
`.[python]` adds Rope renaming; `.[agent]` adds renaming and the MCP server.

## Connect an agent

Use these fields in your MCP client's server configuration. Replace both paths
with absolute paths on your machine. Each server is scoped to one project.

```json
{
  "mcpServers": {
    "comfyedit": {
      "command": "/absolute/path/to/comfyedit/.venv/bin/comfyedit",
      "args": ["--root", "/absolute/path/to/your/project", "--mcp"]
    }
  }
}
```

Configuration wrappers vary by client; the command and arguments are the same.
Only stdio is supported; ComfyEdit does not open a listening port.

## First run

Use a temporary project for this smoke test. After installing, start with a read:

```sh
.venv/bin/comfyedit --root /path/to/project <<'JSON'
{"tool":"read_code","file":"src/planner.py","max_lines":40}
JSON
```

Copy the returned `version` into a small preview. Review `diff` (and any
`read_diff` pages), then apply the exact saved preview:

```sh
.venv/bin/comfyedit --root /path/to/project <<'JSON'
{"tool":"preview","edits":[{"operation":"replace_text","file":"src/planner.py","version":"<version>","old":"timeout = 10","new":"timeout = 30"}]}
JSON

.venv/bin/comfyedit --root /path/to/project <<'JSON'
{"tool":"commit_edit","plan_id":"<saved-preview ID>"}
JSON
```

The commit returns an `undo_id`. Call `undo_edit` with it, review the reverse
preview, and commit that preview if you want to restore the original state.

Use ComfyEdit when stale-write protection, complete diff review, file lifecycle
changes, project-wide renames, or guarded undo matter. Use ordinary patch or
terminal tools when the change is tiny, already fully visible, and does not need
these transaction guarantees. After committing, run the project's tests with
your normal execution tool; ComfyEdit never executes project code automatically.

## The comfortable loop

Start with `list_files(pattern="*.py")` or `search_code(query="solve", pattern="*.py")`
if you do not know the target file. See the compact [agent guide](docs/agent-guide.md)
for tool selection and examples.

For the fastest sanity check, use the [agent quick reference](docs/agent-guide.md)
and run `examples/workflow.py`. It creates a temporary project, finds a literal,
previews a source edit plus a new test, reads all diff pages, commits, and undoes
the transaction.

1. `read_code(file="src/planner.py", symbol="Planner.solve")` returns source,
   a SHA-256 `version`, and a qualified symbol outline. To page, pass `next_line`
   as `start_line` and `next_column` as `start_column`, keeping the same `symbol`
   and `version`. Pages stop at the symbol boundary, line limit, or character
   limit. A long line may span several pages.
2. `preview(edits=[...])` validates an ordered batch and returns a diff and saved-preview `plan_id`.
   If `next_offset` is not null, use `read_diff` to review the remaining pages.
3. `commit_edit(plan_id=...)` applies exactly that preview, checking input versions
   again. It returns new versions and an `undo_id`.

**`plan_id` is the ID of a saved edit preview.** It is an opaque receipt for
specific before/after file contents, not an AI reasoning plan or a planning mode.
`commit_edit` uses that receipt to apply exactly the changes you reviewed.
`undo_id` identifies a completed transaction; `undo_edit` turns it into a reverse
preview with its own `plan_id`.

The MCP transport can report malformed calls as `isError`; valid calls can still
return an application payload with `ok: false`. Check both layers. The CLI always
prints the application payload and exits 1 for `ok: false`.

An edit looks like this; copy the version from the read result:

```json
{
  "operation": "replace_symbol",
  "file": "src/planner.py",
  "symbol": "Planner.solve",
  "version": "<version from read_code>",
  "code": "def solve(self, task):\n    return self.search(task)"
}
```

Replacement includes the whole definition and its decorators. Code is dedented
and placed at the existing symbol's indentation. `insert_before` and
`insert_after` take the same fields and insert sibling code. Nested names such
as `Planner.solve.helper` work. Ambiguous duplicate definitions are rejected.

For any UTF-8 file, use an exact replacement:

```json
{
  "operation": "replace_text",
  "file": "config.toml",
  "version": "<version from read_code>",
  "old": "timeout = 10",
  "new": "timeout = 30",
  "expected_matches": 1
}
```

The default is exactly one match. More matches require an explicit count.
Edits in a batch run in order; every version refers to the original file,
including when several edits target the same file.

When you know the exact location but quoting the old text is inconvenient, use
`replace_range`. It uses 1-based physical lines and Unicode-character columns;
the start is inclusive and the end is exclusive, and the range may span lines:

```json
{
  "operation": "replace_range",
  "file": "src/parser.py",
  "version": "<version from read_code>",
  "start_line": 12,
  "start_column": 5,
  "end_line": 15,
  "end_column": 1,
  "code": "return parse_fast(value)\n"
}
```

This is still preview-only until you call `commit_edit`; the same version guard,
diff review, and undo transaction apply.

For a semantic Python rename:

```json
{
  "file": "src/planner.py",
  "symbol": "Planner.solve",
  "new_name": "find_plan",
  "version": "<version from read_code>"
}
```

Pass that to `rename_symbol`, then commit its returned edit preview. Rope computes
the rename in an isolated copy of Python sources. The preview guards the whole Python
source snapshot and inventory, including callers that might be added later.
Static inference cannot guarantee coverage of reflection, dynamic attributes,
or callers outside this root. Review the diff and run your project tests.

`undo_edit(undo_id=...)` returns a reverse preview. Commit that preview to undo.
It refuses if any affected file has changed. It can undo an older transaction
when intervening transactions touched only other files; it does not merge
intervening edits within the same file. An undo commit itself has an undo ID.

## Create, move, and delete files

These operations participate in the same ordered `preview(edits=[...])` batch:

```json
{
  "tool": "preview",
  "edits": [
    {"operation": "create_file", "file": "tests/test_solver.py", "code": "def test_answer():\n    assert 2 + 2 == 4\n"},
    {"operation": "move_file", "file": "old_solver.py", "destination": "src/solver.py", "version": "<original old_solver.py version>"},
    {"operation": "delete_file", "file": "obsolete.txt", "version": "<original obsolete.txt version>"}
  ]
}
```

Creation and moves require an absent destination in the batch's current state.
Commit checks the original destination state again before any writes. Missing
parent directories are created at commit time. Moves preserve ordinary permission
bits and do **not** rewrite imports or references; include those edits explicitly.
Creation defaults to mode `420` (octal `0644`); use `493` for an executable `0755`.

Every version describes the path **before the whole batch**. To edit a newly
created or newly occupied move destination in the same batch, use `version: null`
if that path was originally absent. An existing path always uses its original
read version. A batch cannot edit both a file path and one of its descendants.
All resulting Python files must compile, including created and moved files.

Undo reverses creations, deletions, moves, contents, and permission bits. It checks
both contents and modes and refuses to overwrite a recreated deleted file.
Ordinary undo may leave empty parent directories; failed commits and rollback
recovery remove newly created directories only when they are still empty.

## Discovery and bounded reads

`list_files(pattern="*", offset=0, limit=100)` returns sorted relative paths,
`next_offset`, and an inventory `version`. Pass that version on later pages to
reject an inventory change. Patterns are case-sensitive shell globs over the
whole relative path; `*.py` matches Python files at any depth.

In Git worktrees, discovery includes tracked and non-ignored untracked files.
Git fsmonitor hooks are disabled during discovery. Without Git, discovery walks
the tree. Both paths exclude symlinks and common dependency/build metadata
directories; the fallback does not interpret `.gitignore`. Explicit `read_code`
requests can still address ignored files outside protected metadata.

`list_previews(include_applied=false, offset=0, limit=20)` lists saved preview
receipts and their files. `discard_preview(plan_id)` removes only an unapplied
preview; it refuses applied previews so transaction history remains undoable.
Saved previews contain full before/after contents under `.comfyedit/`, so discard
unneeded ones when working on a long-lived project.

`search_code(query="literal text", pattern="*.py", max_results=50)` returns
non-overlapping, case-sensitive, single-line literal matches with file versions,
1-based lines/columns, and snippets of at most 240 characters. Follow `next_offset`
for more matches. Results are live, not a cross-file snapshot: restart the search
if the workspace changes while paging. Unsupported files are reported in `skipped`
(up to 20 details plus the total). Discovery supports at most 10,000 matching
files; search scans at most 20 MB per call. Narrow the pattern when needed.

`read_code` returns at most 24,000 text characters and 120 lines by default.
Set `include_symbols=false` to omit the outline. Otherwise it returns at most
100 symbols; follow `next_symbol_offset` using `symbol_offset` for more. Use
`max_chars`, `max_lines`, and `max_symbols` to request smaller pages. Columns and
character offsets count Unicode characters. Keep the original `version` while
paging source or outlines to detect changes between calls.

## Recover an interrupted transaction

A journal is saved and synced before source writes. If a process exits midway,
new commits return `recovery_required` with the pending transaction IDs.

1. Call `list_transactions()` to inspect each affected file's `before`, `after`,
   `both`, or `conflict` state. Full source contents are omitted from this result.
2. Call `recover_transaction(transaction_id=..., action="rollback")` to restore
   the original contents, or `action="finish"` to finish the saved changes.
3. A finished transaction returns an `undo_id`. A rollback makes its original
   preview available to retry, subject to its original guards.

Recovery **writes directly**, using the exact states already recorded in the
journal. It checks all affected files before writing and refuses if any file
matches neither recorded state. Resolve those conflicts explicitly; recovery
never guesses or merges them. Finishing also rechecks unchanged input guards and
any Python rename inventory guard; rollback can still restore the affected files
when an unrelated dependency changed. If recovery itself is interrupted, inspect and
retry. `list_transactions(include_completed=true)` also lists undoable and
rolled-back transactions, with `offset`/`limit` paging.

This is recoverability, not multi-file crash atomicity. Other programs can observe
partial changes, and durable writes depend on the filesystem honoring `fsync`.
Corrupt or missing journals require manual inspection; preserve `.comfyedit/`.

## Review the complete diff

Previews include at most 24,000 Unicode characters of diff, plus `total_chars`
and `next_offset`. Continue from that offset to inspect every proposed change:

```json
{
  "tool": "read_diff",
  "plan_id": "<saved-preview ID from preview, rename_symbol, or undo_edit>",
  "offset": 24000,
  "max_chars": 12000
}
```

Keep passing the returned `next_offset` until it is null. Offsets count Unicode
characters, not UTF-8 bytes or lines; pages can split a line. Concatenating pages
reconstructs the complete unified diff. `max_chars` accepts 1–24,000 (default
24,000). Start at offset 0 to reread a plan from the beginning.

Diffs come from the saved before/after snapshot, so they remain stable even if
source files change or the plan is committed. Commit still checks the original
versions. Pass a preview's `plan_id`, not a transaction's `undo_id`.
Files without a final newline are marked explicitly in the diff.

## Terminal and Python use

Avoid shell escaping by supplying JSON on stdin:

```sh
.venv/bin/comfyedit --root /path/to/project <<'JSON'
{"tool": "read_code", "file": "src/planner.py", "symbol": "Planner.solve"}
JSON
```

All twelve tools use the same fields through the CLI; add `"tool": "preview"`,
for example. Success exits 0; errors exit 1. stdout contains JSON only.

```python
from comfyedit import Editor

editor = Editor("/path/to/project")
read = editor.read_code("planner.py", symbol="Planner.solve")
preview = editor.rename_symbol("planner.py", "Planner.solve", "find_plan", read["version"])
print(preview["diff"])
# Fetch remaining diff pages if preview["next_offset"] is not None.
result = editor.commit_edit(preview["plan_id"])
```

## Errors agents can act on

Results have `ok: true`, or `ok: false` and a structured `error` with a `code`
and message. MCP clients must inspect `ok` in the tool payload.

| Code | Next action |
| --- | --- |
| `stale_version` | Read the changed file and rebuild the preview. |
| `match_count` | Use a more precise anchor or an explicit match count. |
| `symbol_not_unique` | Inspect the outline and use a unique qualified name. |
| `syntax_error` | Fix the proposal using the returned file/line/diagnostic. |
| `undo_conflict` | Read subsequent changes; resolve them explicitly. |
| `missing_dependency` | Install the appropriate optional extra. |
| `invalid_range` | Use the returned paging cursor and keep page sizes within the documented limits. |
| `invalid_plan` | Pass the `plan_id` from a preview rather than an `undo_id`. |
| `file_exists` | Choose an absent destination or explicitly edit/delete the existing file. |
| `path_conflict` | Separate operations that turn a file into a directory or vice versa. |
| `recovery_required` | Inspect `list_transactions`, then finish or roll back the pending transaction. |
| `recovery_conflict` | Inspect external changes; recovery has refused to overwrite them. |
| `search_too_large` | Narrow the file pattern. |
| `invalid_state` | Preserve the metadata and inspect the reported record for corruption. |

## Guarantees and limits

- Source is never executed. New edit previews require resulting Python files to
  compile. This is a syntax check, not type checking or behavioral validation.
  Undo restores recorded originals even when they originally had syntax errors.
- All input hashes are checked before writes. An advisory project lock
  serializes ComfyEdit processes. External editors do not take this lock: use
  separate worktrees for concurrent independent agents.
- Each file replacement is atomic and preserves ordinary permission bits.
  A caught write failure attempts rollback of already-written files.
  **Multi-file commits are not crash-atomic.** A process/OS crash can leave a
  partially applied transaction; use the guarded recovery tools described above.
- `.comfyedit/` stores full before/after contents and versions locally. It is
  ignored in this repo; add `.comfyedit/` to other projects' `.gitignore` files.
  Keep it private like the source itself. Removing it discards previews and undo.
- Paths must stay within the configured root; symlink paths and `.git` are
  rejected. This is a cooperative local tool, not a sandbox against malicious
  processes changing paths or metadata concurrently.
- UTF-8 files only, at most 2 MB each. Rename supports at most 2,000
  Python files and ignores common dependency/build metadata directories.
  Diff responses are capped at 24,000 characters with an explicit truncation
  flag; `read_diff` provides the remaining pages from the saved plan.
- No signature refactoring, semantic reference-list tool, LSP backend, Windows
  mutations, or automatic project test execution yet. Run project tests with your
  normal execution tool after committing an edit.

For maintainers, contributor guidance, test commands, and implementation
invariants are in [CONTRIBUTING.md](CONTRIBUTING.md), [AGENTS.md](AGENTS.md), and
the [agent guide](docs/agent-guide.md). The repository also includes a
disposable workflow example at `examples/workflow.py`.
