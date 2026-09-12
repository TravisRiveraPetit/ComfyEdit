"""Small editing core. No network calls and no execution of project code."""
from __future__ import annotations

import ast
import contextlib
import difflib
import errno
import hashlib
import io
import json
import keyword
import os
from pathlib import Path
from .discovery import DiscoveryMixin
import re
import stat
import tempfile
import textwrap
import uuid


class EditError(Exception):
    def __init__(self, code, message, **details):
        super().__init__(message)
        self.code, self.details = code, details

    def result(self):
        return {"ok": False, "error": {"code": self.code, "message": str(self), **self.details}}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def content_version(source):
    return digest(source.encode()) if source is not None else None


def source_lines(source):
    """Keep physical line endings without treating Unicode separators as lines."""
    return io.StringIO(source, newline="").readlines()


def position_offset(source, line, column):
    """Convert a 1-based physical line/column to a character offset."""
    lines = source_lines(source)
    if not lines:
        if line == 1 and column == 1:
            return 0
        raise EditError("invalid_range", "The empty file only has position line 1, column 1.")
    if line == len(lines) + 1 and column == 1:
        return len(source)
    if line < 1 or line > len(lines):
        raise EditError("invalid_range", "Line is outside the file.", line=line, total_lines=len(lines))
    if column < 1 or column > len(lines[line - 1]) + 1:
        raise EditError("invalid_range", "Column is outside the selected physical line.",
                        line=line, column=column, line_length=len(lines[line - 1]))
    return sum(len(item) for item in lines[:line - 1]) + column - 1


def diff_chunks(before, after, before_modes=None, after_modes=None):
    before_modes, after_modes = before_modes or {}, after_modes or {}
    for file, source in after.items():
        old = before[file]
        old_mode, new_mode = before_modes.get(file, 0o644), after_modes.get(file, 0o644)
        if old is None or source is None or old_mode != new_mode:
            yield f"diff --git a/{file} b/{file}\n"
            if old is None:
                yield f"new file mode {0o100000 | new_mode:06o}\n"
            elif source is None:
                yield f"deleted file mode {0o100000 | old_mode:06o}\n"
            else:
                yield f"old mode {0o100000 | old_mode:06o}\nnew mode {0o100000 | new_mode:06o}\n"
        for line in difflib.unified_diff(source_lines(old or ""), source_lines(source or ""),
                fromfile="/dev/null" if old is None else "a/" + file,
                tofile="/dev/null" if source is None else "b/" + file):
            yield line
            if not line.endswith(("\r", "\n")):
                yield "\n\\ No newline at end of file\n"


def diff_page(before, after, offset, max_chars, before_modes=None, after_modes=None):
    chunks, total = [], 0
    stop = offset + max_chars
    for chunk in diff_chunks(before, after, before_modes, after_modes):
        end = total + len(chunk)
        if total < stop and end > offset:
            chunks.append(chunk[max(0, offset - total):stop - total])
        total = end
    if offset > total:
        raise EditError("invalid_range", "offset exceeds the diff length.", total_chars=total)
    return {"diff": "".join(chunks), "total_chars": total,
            "next_offset": stop if stop < total else None,
            "diff_truncated": stop < total}


def symbols(source):
    result = []
    def visit(node, prefix=""):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                name = prefix + child.name
                start = min([child.lineno] + [d.lineno for d in child.decorator_list])
                result.append((name, start, child.end_lineno, child))
                visit(child, name + ".")
            else:
                visit(child, prefix)
    visit(ast.parse(source))
    return result


def select(source, name):
    candidates = [s for s in symbols(source) if s[0] == name]
    if len(candidates) != 1:
        raise EditError("symbol_not_unique", "Use an exact qualified symbol from read_code.",
                        symbol=name, matches=len(candidates))
    return candidates[0]


class Editor(DiscoveryMixin):
    MAX_BYTES = 2_000_000
    SKIP = {".git", ".comfyedit", ".venv", "venv", "node_modules", "__pycache__", ".ropeproject", "build", "dist", ".mypy_cache", ".pytest_cache", ".ruff_cache"}

    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise EditError("invalid_root", "Root must be a directory.")

    def path(self, name):
        if not isinstance(name, str) or "\0" in name:
            raise EditError("unsafe_path", "Use a relative path string without NUL bytes.")
        p = Path(name)
        if p.as_posix() != name or p.is_absolute() or not p.parts or any(x in {"..", ".git", ".comfyedit"} for x in p.parts):
            raise EditError("unsafe_path", "Use a relative path inside the project, outside internal metadata.")
        current = self.root
        for part in p.parts:
            current /= part
            if current.is_symlink():
                raise EditError("unsafe_path", "Symlink paths are not editable.")
        if not current.resolve().is_relative_to(self.root):
            raise EditError("unsafe_path", "Path escapes project root.")
        return current

    def read_bytes(self, name):
        p = self.path(name)
        if not p.exists():
            raise EditError("missing_file", "File does not exist.", file=name)
        if not stat.S_ISREG(p.stat().st_mode):
            raise EditError("not_file", "Path is not a regular file.", file=name)
        if p.stat().st_size > self.MAX_BYTES:
            raise EditError("file_too_large", "File exceeds the 2 MB editing limit.", file=name)
        data = p.read_bytes()
        try:
            source = data.decode("utf-8")
        except UnicodeDecodeError:
            raise EditError("encoding", "Only UTF-8 files are supported.", file=name)
        if "\0" in source:
            raise EditError("binary_file", "Binary files are not editable.", file=name)
        return data

    def read_code(self, file, symbol=None, start_line=1, max_lines=120, start_column=1,
                  max_chars=24000, include_symbols=True, symbol_offset=0, max_symbols=100, version=None):
        ranges = ((start_line, 1, None), (max_lines, 1, 1000), (start_column, 1, None),
                  (max_chars, 1, 24000), (symbol_offset, 0, None), (max_symbols, 1, 100))
        if any(type(value) is not int or value < low or (high is not None and value > high)
               for value, low, high in ranges):
            raise EditError("invalid_range", "Use positive line/column, max_lines 1..1000, max_chars 1..24000, symbol_offset >= 0, max_symbols 1..100.")
        data = self.read_bytes(file)
        current_version = digest(data)
        if version is not None and version != current_version:
            raise EditError("stale_version", "Source changed while paging; read again.", file=file, current_version=current_version)
        source = data.decode("utf-8")
        outline, diagnostics = [], []
        if file.endswith(".py") and include_symbols:
            try:
                outline = [{"symbol": n, "start_line": a, "end_line": b} for n, a, b, _ in symbols(source)]
            except SyntaxError as e:
                diagnostics = [{"line": e.lineno, "message": e.msg}]
        lines = source_lines(source)
        end = len(lines)
        if symbol:
            self.python_only(file)
            _, first, end, _ = select(source, symbol)
            if start_line < first:
                start_line, start_column = first, 1
        if start_line <= end and start_column > len(lines[start_line - 1]) + 1:
            raise EditError("invalid_range", "start_column is past the end of the selected line.")
        chunks, remaining, last = [], max_chars, min(end, start_line - 1)
        next_line, next_column = None, None
        for number in range(start_line, min(end, start_line + max_lines - 1) + 1):
            column = start_column if number == start_line else 1
            content = lines[number - 1][column - 1:]
            take = min(remaining, len(content))
            chunks.append(content[:take])
            remaining -= take
            last = number
            if take < len(content):
                next_line, next_column = number, column + take
                break
            if number < end:
                next_line, next_column = number + 1, 1
            else:
                next_line, next_column = None, None
            if remaining == 0:
                break
        return {"ok": True, "file": file, "version": current_version, "start_line": start_line,
                "start_column": start_column, "end_line": last, "text": "".join(chunks),
                "next_line": next_line, "next_column": next_column,
                "symbols": outline[symbol_offset:symbol_offset + max_symbols],
                "next_symbol_offset": symbol_offset + max_symbols if symbol_offset + max_symbols < len(outline) else None,
                "diagnostics": diagnostics}

    @staticmethod
    def python_only(file):
        if not file.endswith(".py"):
            raise EditError("unsupported_language", "Symbol operations currently support Python. Use replace_text for other UTF-8 files.")

    @contextlib.contextmanager
    def lock(self):
        # Advisory locking serializes ComfyEdit processes. External editors do not participate.
        try:
            import fcntl
        except ImportError:
            raise EditError("unsupported_platform", "Mutations currently require Linux or macOS.")
        state = self.root / ".comfyedit"
        if state.is_symlink():
            raise EditError("unsafe_path", ".comfyedit must not be a symlink.")
        state.mkdir(mode=0o700, exist_ok=True)
        self.sync_directory(self.root)
        lock = state / "lock"
        if lock.is_symlink():
            raise EditError("unsafe_path", "Lock must not be a symlink.")
        with lock.open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield state

    @staticmethod
    def sync_directory(path):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    @staticmethod
    def atomic_write(path, data, mode=None):
        fd, temporary = tempfile.mkstemp(prefix=".comfyedit-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                if mode is None:
                    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
                os.fchmod(stream.fileno(), mode)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
            Editor.sync_directory(path.parent)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def snapshot(self, file):
        path = self.path(file)
        if not path.exists():
            # A non-directory ancestor must never count as an absent destination.
            for parent in path.parents:
                if parent == self.root:
                    break
                if parent.exists() and not parent.is_dir():
                    raise EditError("path_conflict", "Parent path is not a directory.", file=file)
            return None, None
        return self.read_bytes(file).decode(), path.stat().st_mode & 0o777

    def write_snapshot(self, file, content, mode):
        path = self.path(file)
        if content is None:
            if path.exists():
                path.unlink()
                self.sync_directory(path.parent)
        else:
            missing = []
            parent = path.parent
            while not parent.exists():
                missing.append(parent)
                parent = parent.parent
            for directory in reversed(missing):
                directory.mkdir()
                self.sync_directory(directory.parent)
            self.atomic_write(path, content.encode(), mode=mode)

    def save(self, state, identifier, payload):
        self.atomic_write(state / (identifier + ".json"), json.dumps(payload).encode())

    def load(self, state, identifier):
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise EditError("invalid_id", "Expected a plan or undo ID returned by ComfyEdit.")
        p = state / (identifier + ".json")
        if p.is_symlink() or not p.is_file():
            raise EditError("unknown_id", "Plan or undo ID not found.")
        try:
            payload = json.loads(p.read_text())
        except (ValueError, UnicodeError) as error:
            raise EditError("invalid_state", "Saved metadata is corrupt; preserve it for manual inspection.", identifier=identifier) from error
        if not isinstance(payload, dict) or "kind" not in payload:
            raise EditError("invalid_state", "Saved metadata has no record kind.", identifier=identifier)
        return payload

    def preview(self, edits):
        """Edits run in list order; versions always describe original file bytes."""
        if not isinstance(edits, list) or not edits or len(edits) > 100:
            raise EditError("invalid_edits", "Supply between 1 and 100 edits.")
        with self.lock() as state:
            before, after, before_modes, after_modes = {}, {}, {}, {}
            def capture(file):
                if file not in before:
                    before[file], before_modes[file] = self.snapshot(file)
                    after[file], after_modes[file] = before[file], before_modes[file]
            for edit in edits:
                if not isinstance(edit, dict):
                    raise EditError("invalid_edits", "Each edit must be an object.")
                file, op = edit["file"], edit["operation"]
                capture(file)
                if op == "create_file":
                    if after[file] is not None:
                        raise EditError("file_exists", "Create requires an absent destination.", file=file)
                    mode = edit.get("mode", 0o644)
                    if type(mode) is not int or not 0 <= mode <= 0o777:
                        raise EditError("invalid_mode", "mode must be an integer from 0 to 511.")
                    after[file], after_modes[file] = edit["code"], mode
                    continue
                if edit["version"] != content_version(before[file]):
                    raise EditError("stale_version", "Read the file again and rebuild this edit.", file=file,
                                    current_version=content_version(before[file]))
                source = after[file]
                if source is None:
                    raise EditError("missing_file", "File does not exist in this batch.", file=file)
                if op == "delete_file":
                    after[file], after_modes[file] = None, None
                    continue
                if op == "move_file":
                    destination = edit["destination"]
                    capture(destination)
                    if after[destination] is not None:
                        raise EditError("file_exists", "Move requires an absent destination.", file=destination)
                    after[destination], after_modes[destination] = source, after_modes[file]
                    after[file], after_modes[file] = None, None
                    continue
                if op == "replace_range":
                    values = [edit.get(key) for key in ("start_line", "start_column", "end_line", "end_column")]
                    if any(type(value) is not int for value in values):
                        raise EditError("invalid_range", "replace_range coordinates must be integers.", file=file)
                    start = position_offset(source, edit["start_line"], edit["start_column"])
                    end = position_offset(source, edit["end_line"], edit["end_column"])
                    if end <= start:
                        raise EditError("invalid_range", "replace_range end must be after its start.", file=file)
                    after[file] = source[:start] + edit["code"] + source[end:]
                    continue
                if op == "replace_text":
                    old = edit["old"]
                    count = source.count(old) if old else 0
                    expected = edit.get("expected_matches", 1)
                    if type(expected) is not int or expected < 1 or count != expected:
                        raise EditError("match_count", "Exact text must match the requested count.",
                                        file=file, actual_matches=count, expected_matches=expected)
                    after[file] = source.replace(old, edit["new"])
                elif op in {"replace_symbol", "insert_before", "insert_after"}:
                    self.python_only(file)
                    _, first, last, node = select(source, edit["symbol"])
                    lines = source_lines(source)
                    indent = re.match(r"\s*", lines[node.lineno-1]).group()
                    newline = "\r\n" if "\r\n" in source else "\r" if "\r" in source else "\n"
                    content = edit["code"].replace("\r\n", "\n").replace("\r", "\n")
                    content = textwrap.dedent(content).strip("\n")
                    code = textwrap.indent(content, indent).replace("\n", newline) + newline
                    if op == "insert_before":
                        last = first - 1
                    elif op == "insert_after":
                        first = last + 1
                        if last and not lines[last-1].endswith(("\r", "\n")):
                            code = newline + code
                    after[file] = "".join(lines[:first-1]) + code + "".join(lines[last:])
                else:
                    raise EditError("unknown_operation", "Unknown edit operation.", operation=op)
            return self.make_plan(state, before, after, before_modes=before_modes, after_modes=after_modes)

    def make_plan(self, state, before, after, guards=None, warnings=None, before_modes=None, after_modes=None, validate_python=True):
        if before_modes is None:
            before_modes = {f: self.snapshot(f)[1] for f in before}
        if after_modes is None:
            after_modes = dict(before_modes)
        changed = {f: t for f, t in after.items()
                   if t != before[f] or after_modes.get(f) != before_modes.get(f)}
        paths = sorted(changed)
        for file in paths:
            if any(parent.as_posix() in changed for parent in Path(file).parents if parent != Path(".")):
                raise EditError("path_conflict", "A batch cannot edit both a path and its descendant.", file=file)
        for f, source in changed.items():
            if source is None:
                continue
            if not isinstance(source, str):
                raise EditError("invalid_request", "File content must be a string.", file=f)
            if "\0" in source:
                raise EditError("binary_file", "Proposed edit contains a NUL byte.", file=f)
            if len(source.encode()) > self.MAX_BYTES:
                raise EditError("file_too_large", "Result exceeds 2 MB.", file=f)
            if f.endswith(".py") and validate_python:
                try:
                    compile(source, f, "exec", dont_inherit=True)
                except SyntaxError as e:
                    raise EditError("syntax_error", "Proposed Python edit does not compile.",
                                    file=f, line=e.lineno, diagnostic=e.msg)
        if not changed:
            return {"ok": True, "status": "no_change", "files": []}
        identifier = uuid.uuid4().hex
        payload = {"kind": "plan", "before": {f: before[f] for f in changed}, "after": changed,
                   "guards": guards if guards is not None else {f: content_version(t) for f, t in before.items()},
                   "before_modes": {f: before_modes.get(f) for f in changed},
                   "after_modes": {f: after_modes.get(f) for f in changed},
                   "mode_guards": before_modes}
        self.save(state, identifier, payload)
        return {"ok": True, "status": "preview", "plan_id": identifier, "files": list(changed),
                "changes": [{"file": f, "operation": "create" if before[f] is None else "delete" if t is None else "modify",
                             "before_mode": before_modes.get(f), "after_mode": after_modes.get(f)} for f, t in changed.items()],
                **diff_page(payload["before"], changed, 0, 24000, before_modes, after_modes),
                "diagnostics": [], "warnings": warnings or [],
                "next": "If next_offset is set, use read_diff(plan_id, offset=next_offset) to review the rest. commit_edit(plan_id) applies exactly this preview."}

    def read_diff(self, plan_id, offset=0, max_chars=24000):
        """Page through the saved preview, independent of current source files."""
        if type(offset) is not int or type(max_chars) is not int or offset < 0 or not 1 <= max_chars <= 24000:
            raise EditError("invalid_range", "offset must be nonnegative; max_chars must be 1..24000.")
        with self.lock() as state:
            plan = self.load(state, plan_id)
            if plan["kind"] not in {"plan", "applied"}:
                raise EditError("invalid_plan", "Use a plan_id returned by preview, rename_symbol, or undo_edit.")
            return {"ok": True, "plan_id": plan_id, "offset": offset,
                    **diff_page(plan["before"], plan["after"], offset, max_chars,
                                plan.get("before_modes"), plan.get("after_modes"))}

    def list_previews(self, include_applied=False, offset=0, limit=20):
        """List saved edit previews so an agent can recover a lost receipt ID."""
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise EditError("invalid_range", "offset must be nonnegative; limit must be 1..100.")
        with self.lock() as state:
            records = []
            for path in state.glob("*.json"):
                if not re.fullmatch(r"[0-9a-f]{32}", path.stem) or path.is_symlink():
                    continue
                payload = self.load(state, path.stem)
                if payload.get("kind") not in ({"plan", "applied"} if include_applied else {"plan"}):
                    continue
                records.append((path.stat().st_mtime, path.stem, payload))
            records.sort(key=lambda item: (-item[0], item[1]))
            selected = records[offset:offset + limit]
            return {"ok": True, "previews": [
                {"plan_id": identifier, "status": "applied" if payload["kind"] == "applied" else "preview",
                 "files": sorted(payload.get("after", {})),
                 "transaction_id": payload.get("transaction_id"), "modified_at": modified}
                for modified, identifier, payload in selected
            ], "next_offset": offset + limit if offset + limit < len(records) else None}

    def discard_preview(self, plan_id):
        """Discard an unapplied preview; applied transaction records remain undoable."""
        with self.lock() as state:
            payload = self.load(state, plan_id)
            if payload.get("kind") != "plan" or payload.get("transaction_id"):
                raise EditError("preview_in_use", "Only an unapplied edit preview can be discarded.")
            path = state / (plan_id + ".json")
            path.unlink()
            self.sync_directory(state)
            return {"ok": True, "status": "discarded", "plan_id": plan_id}

    def transaction_files(self, journal):
        files = []
        for file in journal["before"]:
            try:
                content, mode = self.snapshot(file)
                matches = []
                for side in ("before", "after"):
                    expected_mode = journal.get(side + "_modes", {}).get(file)
                    matches.append(content == journal[side][file] and
                                   (expected_mode is None or mode == expected_mode))
                status = "both" if all(matches) else "before" if matches[0] else "after" if matches[1] else "conflict"
                files.append({"file": file, "state": status, "version": content_version(content), "mode": mode})
            except (EditError, OSError) as error:
                files.append({"file": file, "state": "conflict", "message": str(error)})
        return files

    def journals(self, state):
        for path in sorted(state.glob("*.json")):
            if not re.fullmatch(r"[0-9a-f]{32}", path.stem):
                continue
            payload = self.load(state, path.stem)
            if payload.get("kind") in {"pending", "undo", "rolled_back"}:
                yield path.stem, payload

    def list_transactions(self, include_completed=False, offset=0, limit=20):
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 100:
            raise EditError("invalid_range", "offset must be nonnegative; limit must be 1..100.")
        with self.lock() as state:
            journals = ((identifier, journal) for identifier, journal in self.journals(state)
                        if include_completed or journal["kind"] == "pending")
            items, next_offset = [], None
            for index, (identifier, journal) in enumerate(journals):
                if index < offset:
                    continue
                if len(items) == limit:
                    next_offset = offset + limit
                    break
                items.append({"transaction_id": identifier, "status": journal["kind"],
                              "plan_id": journal.get("plan_id"), "files": self.transaction_files(journal)})
            return {"ok": True, "transactions": items, "next_offset": next_offset}

    def clean_created_dirs(self, journal):
        for name in reversed(journal.get("created_dirs", [])):
            path = self.path(name)
            try:
                path.rmdir()
                self.sync_directory(path.parent)
            except FileNotFoundError:
                pass
            except OSError as error:
                if error.errno not in {errno.ENOTEMPTY, errno.EEXIST}:
                    raise

    def apply_journal(self, journal, side):
        files = self.transaction_files(journal)
        conflicts = [f for f in files if f["state"] == "conflict"]
        if conflicts:
            raise EditError("recovery_conflict", "Files differ from both recorded states; no recovery writes were made.", files=conflicts)
        for item in files:
            if item["state"] in {side, "both"}:
                continue
            file = item["file"]
            self.write_snapshot(file, journal[side][file], journal.get(side + "_modes", {}).get(file))
        if side == "before":
            self.clean_created_dirs(journal)

    def finalize_transaction(self, state, identifier, journal, side):
        if journal.get("plan_id"):
            plan = self.load(state, journal["plan_id"])
            plan["kind"] = "applied" if side == "after" else "plan"
            plan["transaction_id"] = identifier
            self.save(state, journal["plan_id"], plan)
        journal["kind"] = "undo" if side == "after" else "rolled_back"
        self.save(state, identifier, journal)

    def validate_guards(self, plan, skip=(), code="stale_version"):
        if plan.get("python_inventory") is not None and self.python_files() != plan["python_inventory"]:
            raise EditError(code, "Python file inventory changed; rebuild the rename.")
        for file, version in plan["guards"].items():
            if file in skip:
                continue
            content, mode = self.snapshot(file)
            if content_version(content) != version or ("mode_guards" in plan and mode != plan["mode_guards"].get(file)):
                raise EditError(code, "File changed since preview; rebuild the edit.", file=file)

    def recover_transaction(self, transaction_id, action):
        if action not in {"rollback", "finish"}:
            raise EditError("invalid_request", "action must be rollback or finish.")
        with self.lock() as state:
            journal = self.load(state, transaction_id)
            if journal["kind"] != "pending":
                raise EditError("invalid_transaction", "Only pending transactions can be recovered.")
            side = "before" if action == "rollback" else "after"
            if side == "after" and journal.get("plan_id"):
                plan = self.load(state, journal["plan_id"])
                self.validate_guards(plan, skip=journal["before"], code="recovery_conflict")
            self.apply_journal(journal, side)
            self.finalize_transaction(state, transaction_id, journal, side)
            return {"ok": True, "status": "rolled_back" if side == "before" else "applied",
                    "transaction_id": transaction_id,
                    "undo_id": transaction_id if side == "after" else None}

    def commit_edit(self, plan_id):
        with self.lock() as state:
            pending = [identifier for identifier, journal in self.journals(state) if journal["kind"] == "pending"]
            if pending:
                raise EditError("recovery_required", "Inspect and recover pending transactions before committing.", transaction_ids=pending)
            plan = self.load(state, plan_id)
            if plan["kind"] != "plan":
                raise EditError("used_plan", "This plan has already been applied.")
            self.validate_guards(plan)
            created_dirs = set()
            for file, content in plan["after"].items():
                if content is not None:
                    parent = self.path(file).parent
                    while not parent.exists():
                        created_dirs.add(parent.relative_to(self.root).as_posix())
                        parent = parent.parent
            identifier = uuid.uuid4().hex
            journal = {"kind": "pending", "plan_id": plan_id, "before": plan["before"], "after": plan["after"],
                       "before_modes": plan["before_modes"] if "before_modes" in plan else {f: self.snapshot(f)[1] for f in plan["before"]},
                       "after_modes": plan["after_modes"] if "after_modes" in plan else {f: self.snapshot(f)[1] for f in plan["after"]},
                       "created_dirs": sorted(created_dirs, key=lambda name: (len(Path(name).parts), name))}
            self.save(state, identifier, journal)
            try:
                self.apply_journal(journal, "after")
            except Exception:
                try:
                    self.apply_journal(journal, "before")
                    self.finalize_transaction(state, identifier, journal, "before")
                except Exception as recovery_error:
                    raise EditError("recovery_required", "Write or rollback failed; inspect the pending transaction.",
                                    transaction_ids=[identifier], diagnostic=str(recovery_error)) from recovery_error
                raise
            try:
                self.finalize_transaction(state, identifier, journal, "after")
            except Exception as error:
                raise EditError("recovery_required", "Files were written but transaction finalization failed.",
                                transaction_ids=[identifier], diagnostic=str(error)) from error
            return {"ok": True, "status": "applied", "undo_id": identifier,
                    "files": [{"file": f, "version": content_version(t),
                               "status": "deleted" if t is None else "present"} for f, t in plan["after"].items()]}

    def undo_edit(self, undo_id):
        with self.lock() as state:
            journal = self.load(state, undo_id)
            if journal["kind"] != "undo":
                raise EditError("invalid_undo", "This transaction is not available for undo.")
            for file in self.transaction_files(journal):
                if file["state"] not in {"after", "both"}:
                    raise EditError("undo_conflict", "File has subsequent edits; undo will not overwrite them.", file=file["file"])
            return self.make_plan(state, journal["after"], journal["before"],
                                  before_modes=journal.get("after_modes"), after_modes=journal.get("before_modes"), validate_python=False)

    def python_files(self):
        files = []
        for root, dirs, names in os.walk(self.root, followlinks=False):
            dirs[:] = sorted(d for d in dirs if d not in self.SKIP and not (Path(root)/d).is_symlink())
            for name in sorted(names):
                p = Path(root) / name
                if name.endswith(".py") and not p.is_symlink():
                    files.append(p.relative_to(self.root).as_posix())
        return sorted(files)

    def rename_symbol(self, file, symbol, new_name, version):
        self.python_only(file)
        if not new_name.isidentifier() or keyword.iskeyword(new_name):
            raise EditError("invalid_name", "Use a valid non-keyword Python identifier.")
        try:
            from rope.base.project import Project
            from rope.base.change import ChangeContents, ChangeSet
            from rope.refactor.rename import Rename
        except ImportError:
            raise EditError("missing_dependency", "Install Python refactoring support: pip install '.[python]'")
        with self.lock() as state, tempfile.TemporaryDirectory(prefix="comfyedit-rename-") as tmp:
            inventory = self.python_files()
            if len(inventory) > 2000:
                raise EditError("project_too_large", "Rename currently supports at most 2,000 Python files.")
            before = {f: self.read_bytes(f).decode() for f in inventory}
            if file not in before or digest(before[file].encode()) != version:
                raise EditError("stale_version", "Read the file again before renaming.")
            _, _, _, node = select(before[file], symbol)
            lines = source_lines(before[file])
            match = re.search(r"\b(?:def|class)\s+(" + re.escape(node.name) + r")\b", lines[node.lineno-1])
            offset = sum(map(len, lines[:node.lineno-1])) + match.start(1)
            for f, source in before.items():
                p = Path(tmp) / f
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(source.encode())
            # Isolated source snapshot: no project configuration or code is executed.
            project = Project(tmp, ropefolder=None)
            after = dict(before)
            try:
                changes = Rename(project, project.get_resource(file), offset).get_changes(new_name, docs=False)
                def collect(change):
                    if isinstance(change, ChangeSet):
                        for child in change.changes:
                            collect(child)
                    elif isinstance(change, ChangeContents):
                        after[change.resource.path] = change.new_contents
                    else:
                        raise EditError("unsupported_change", "Rename requested a non-content change.")
                collect(changes)
            finally:
                project.close()
            result = self.make_plan(state, before, after,
                         warnings=["Rope uses static inference. Dynamic attributes, reflection, and external callers may be missed. Review the diff."])
            if "plan_id" in result:
                plan = self.load(state, result["plan_id"])
                plan["python_inventory"] = inventory
                self.save(state, result["plan_id"], plan)
            return result
