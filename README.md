# ComfyEdit

**An agent should spend its context on the change, not on escaping shell commands.**

ComfyEdit is a local code editor for coding agents. It exposes five MCP tools
and a JSON CLI: read source, preview edits, rename a Python symbol, commit a
preview, and undo a transaction. No API key, model, or network service required.

Status: **tested prototype, v0.1**. Linux and macOS; Python 3.11+. Not published
to PyPI. Install from this repository.

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

## The comfortable loop

1. `read_code(file="src/planner.py", symbol="Planner.solve")` returns source,
   a SHA-256 `version`, and a qualified symbol outline. `next_line` supports paging.
2. `preview(edits=[...])` validates an ordered batch and returns a diff and `plan_id`.
3. `commit_edit(plan_id=...)` applies exactly that preview, checking input versions
   again. It returns new versions and an `undo_id`.

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

For a semantic Python rename:

```json
{
  "file": "src/planner.py",
  "symbol": "Planner.solve",
  "new_name": "find_plan",
  "version": "<version from read_code>"
}
```

Pass that to `rename_symbol`, then commit its returned plan. Rope computes the
rename in an isolated copy of Python sources. The plan guards the whole Python
source snapshot and inventory, including callers that might be added later.
Static inference cannot guarantee coverage of reflection, dynamic attributes,
or callers outside this root. Review the diff and run your project tests.

`undo_edit(undo_id=...)` returns a reverse preview. Commit that preview to undo.
It refuses if any affected file has changed. It can undo an older transaction
when intervening transactions touched only other files; it does not merge
intervening edits within the same file. An undo commit itself has an undo ID.

## Terminal and Python use

Avoid shell escaping by supplying JSON on stdin:

```sh
comfyedit --root /path/to/project <<'JSON'
{"tool": "read_code", "file": "src/planner.py", "symbol": "Planner.solve"}
JSON
```

All five tools use the same fields through the CLI; add `"tool": "preview"`,
for example. Success exits 0; errors exit 1. stdout contains JSON only.

```python
from comfyedit import Editor

editor = Editor("/path/to/project")
read = editor.read_code("planner.py", symbol="Planner.solve")
plan = editor.rename_symbol("planner.py", "Planner.solve", "find_plan", read["version"])
print(plan["diff"])
result = editor.commit_edit(plan["plan_id"])
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

## Guarantees and limits

- Source is never executed. Changed Python files must compile before a plan is
  saved. This is a syntax check, not type checking or behavioral validation.
- All input hashes are checked before writes. An advisory project lock
  serializes ComfyEdit processes. External editors do not take this lock: use
  separate worktrees for concurrent independent agents.
- Each file replacement is atomic and preserves ordinary permission bits.
  A caught write failure attempts rollback of already-written files.
  **Multi-file commits are not crash-atomic.** A process/OS crash can leave a
  partially applied transaction; journals remain for manual recovery.
- `.comfyedit/` stores full before/after contents and versions locally. It is
  ignored in this repo; add `.comfyedit/` to other projects' `.gitignore` files.
  Keep it private like the source itself. Removing it discards previews and undo.
- Paths must stay within the configured root; symlink paths and `.git` are
  rejected. This is a cooperative local tool, not a sandbox against malicious
  processes changing paths or metadata concurrently.
- Existing UTF-8 files only, at most 2 MB each. Rename supports at most 2,000
  Python files and ignores common dependency/build metadata directories.
  Diffs are capped at 24,000 characters with an explicit truncation flag.
- No file creation/deletion, signature refactoring, reference-list tool, LSP
  backend, Windows mutations, or automatic project test execution yet.

## Development

```sh
.venv/bin/python -m unittest discover -s tests -v
```

The tests cover file integrity, stale edits, ambiguous matches, cross-file
renaming, undo, rollback, and real MCP stdio communication. CI runs Python
3.11–3.13 on Linux and Python 3.12 on macOS.

See [CONTRIBUTING.md](CONTRIBUTING.md) for API principles and extension priorities.

Built using the [official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
and [Rope's refactoring API](https://rope.readthedocs.io/en/latest/library.html).

## Publish your copy

Create an empty GitHub repository, then from this directory:

```sh
git init -b main
git add .
git commit -m "Initial ComfyEdit prototype"
git remote add origin git@github.com:YOUR_USERNAME/comfyedit.git
git push -u origin main
```
