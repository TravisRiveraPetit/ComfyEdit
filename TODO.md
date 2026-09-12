# Agent productivity TODO

This list records feedback from a fresh reviewer who had not seen ComfyEdit
before. Items are ordered by how often they affect an agent trying to complete a
real change. Completed work stays listed so the project history remains clear.

## In progress

- [x] Make saved edit previews discoverable with `list_previews`.
- [x] Let agents safely discard unapplied previews with `discard_preview`.
- [x] Correct the MCP paging instruction: `next_line` maps to `start_line`, and
  `next_column` maps to `start_column`.
- [x] Document that MCP clients must check transport-level `isError` and the
  application payload's `ok` field.
- [x] Add a first-run path near the top of the README: install, configure one
  project, make a tiny preview, inspect it, commit, and undo it.
- [x] Keep CRLF pairs intact in both edit coordinates and paged source reads.

## Editing power

- [x] Add a line-range edit primitive so changing a small block does not require
  reconstructing an entire Python symbol.
- [x] Add an explicit append/insert-at-location operation with clear line and
  column guards.
- [ ] Add opt-in regex and multi-line search/replacement, with match previews and
  the same stale-version contract as literal replacement.
- [ ] Add structured JSON/YAML edits only after choosing a parser and preserving
  formatting behavior; do not silently reserialize user configuration.

## Finding code

- [ ] Add optional filename-plus-content search filters and a clearly bounded
  multi-line query mode.
- [ ] Add semantic definition/reference navigation behind negotiated language
  backends (LSP where available); keep literal search as the reliable fallback.
- [ ] Expand renaming beyond Python/Rope where a backend can state its guarantees,
  including module/file renames and explicit dynamic-reference limitations.

## Workflow integration

- [ ] Add an explicit, opt-in validation command adapter so agents can run the
  project's tests or linters through a bounded, captured interface. Never run
  project code during read, preview, rename, or recovery.
- [ ] Add a terminal/worktree integration path for agents that need commands and
  edits to share one project root without shell-escaping source text.
- [ ] Add retention policy controls for old applied previews and transaction
  journals, with dry-run output before deletion.
- [ ] Show recovery conflict evidence as bounded before/after diff pages so an
  agent can resolve a conflict without switching tools.
- [ ] Consider a backwards-compatible `preview_id` alias for `plan_id`; keep the
  current name until clients can migrate.

## Documentation and distribution

- [x] Add a compact agent guide and a runnable end-to-end example.
- [x] Add a concise “ComfyEdit vs normal patch/terminal tools” decision guide.
- [x] Add a compact MCP argument/schema reference.
- [x] Keep the main README focused on installing and using ComfyEdit; move
  maintainer workflow and implementation notes to contributor documentation.
- [x] Explain `plan_id` as an edit-preview receipt rather than agent reasoning.
- [x] Show recovery output with `before`, `after`, `both`, and `conflict` states.
- [x] Surface core limits and the core-only CI job.
- [ ] Make installation less checkout-dependent (release artifact or package
  publishing) and document the one-root-per-server tradeoff.

## Design constraints

- Preserve version guards, exact-match defaults, previewability, reversibility,
  path safety, and the no-project-code-execution guarantee.
- Measure against ordinary patch tooling before claiming productivity gains:
  completion correctness, unintended changes, tool calls, context bytes, and
  elapsed time on representative tasks.
