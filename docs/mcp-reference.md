# MCP tool reference

All paths are relative to the configured root. Successful calls return a JSON
object with `ok: true`; application failures return `ok: false` and `error`.
Malformed MCP calls can fail at the transport layer with `isError: true` before
an application payload exists. Check both.

| Tool | Arguments | Result or next step |
| --- | --- | --- |
| `list_files` | `pattern`, `offset`, `limit`, optional inventory `version` | Relative paths, inventory version, `next_offset` |
| `search_code` | Literal `query`, `pattern`, `offset`, `max_results` | Versioned line/column matches, `next_offset` |
| `read_code` | `file`, optional `symbol`; `start_line`, `start_column`, `max_lines`, `max_chars`, `symbol_offset`, `max_symbols`, `include_symbols`, `version` | Bounded text, source version, cursors |
| `preview` | Ordered `edits` array | Saved edit-preview receipt `plan_id` and diff |
| `read_diff` | `plan_id`, `offset`, `max_chars` | Complete saved diff pages |
| `list_previews` | `include_applied`, `offset`, `limit` | Saved preview receipts and file summaries |
| `discard_preview` | Unapplied `plan_id` | Removes one unused preview receipt |
| `rename_symbol` | Python `file`, qualified `symbol`, `new_name`, source `version` | Saved edit-preview receipt and diff |
| `commit_edit` | Saved-preview `plan_id` | Applies it; returns transaction `undo_id` |
| `undo_edit` | Transaction `undo_id` | Saved reverse edit preview; review and commit it |
| `list_transactions` | `include_completed`, `offset`, `limit` | Pending/recoverable transaction states |
| `recover_transaction` | `transaction_id`, `action` = `rollback` or `finish` | Direct guarded recovery of a pending transaction |
| `validate` | `command`, optional `timeout_seconds`, `max_output_chars` | Explicit argv command with bounded output and status |

Edit objects use `replace_text`, `replace_range`, `insert_at`, `replace_symbol`, `insert_before`,
`insert_after`, `create_file`, `move_file`, or `delete_file`. Existing paths use
the version read before the batch; a path absent before the batch uses
`version: null` when it is created or populated by a move. `create_file` has no
version field. `plan_id` is a saved edit-preview receipt, not agent reasoning.

`replace_range` selects a half-open range using 1-based physical line and
Unicode-character columns: `start` is included and `end` is excluded. It can
span lines and normalizes inserted line endings to the surrounding file's style.
For example:

```json
{"operation":"replace_range","file":"notes.txt","version":"<read version>","start_line":4,"start_column":1,"end_line":6,"end_column":1,"code":"updated\ntext\n"}
```

`insert_at` uses `line` and `column` for an empty insertion point. Position
`line=1,column=1` is the beginning; `line=physical_line_count+1,column=1` is EOF.
Coordinates for later edits in an ordered batch are evaluated against the text
produced by earlier edits, while versions still refer to the original source.
CRLF pairs are indivisible: positions between `\r` and `\n` return
`invalid_range`.
