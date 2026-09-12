"""Small editing core. No network calls and no execution of project code."""
from __future__ import annotations

import ast
import contextlib
import difflib
import hashlib
import json
import keyword
import os
from pathlib import Path
import re
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


class Editor:
    MAX_BYTES = 2_000_000
    SKIP = {".git", ".comfyedit", ".venv", "venv", "node_modules", "__pycache__", ".ropeproject"}

    def __init__(self, root):
        self.root = Path(root).resolve(strict=True)
        if not self.root.is_dir():
            raise EditError("invalid_root", "Root must be a directory.")

    def path(self, name):
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
        if not p.is_file():
            raise EditError("missing_file", "File does not exist.", file=name)
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

    def read_code(self, file, symbol=None, start_line=1, max_lines=120):
        data = self.read_bytes(file)
        source = data.decode("utf-8")
        outline, diagnostics = [], []
        if file.endswith(".py"):
            try:
                outline = [{"symbol": n, "start_line": a, "end_line": b} for n, a, b, _ in symbols(source)]
            except SyntaxError as e:
                diagnostics = [{"line": e.lineno, "message": e.msg}]
        lines = source.splitlines(keepends=True)
        end = len(lines)
        if symbol:
            self.python_only(file)
            _, start_line, end, _ = select(source, symbol)
        if start_line < 1 or not 1 <= max_lines <= 1000:
            raise EditError("invalid_range", "start_line must be positive; max_lines must be 1..1000.")
        stop = min(end, start_line - 1 + max_lines)
        return {"ok": True, "file": file, "version": digest(data), "start_line": start_line,
                "end_line": stop, "text": "".join(lines[start_line-1:stop]),
                "next_line": stop+1 if stop < end else None,
                "symbols": outline, "diagnostics": diagnostics}

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
        lock = state / "lock"
        if lock.is_symlink():
            raise EditError("unsafe_path", "Lock must not be a symlink.")
        with lock.open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            yield state

    @staticmethod
    def atomic_write(path, data):
        fd, temporary = tempfile.mkstemp(prefix=".comfyedit-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, path.stat().st_mode & 0o777 if path.exists() else 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def save(self, state, identifier, payload):
        self.atomic_write(state / (identifier + ".json"), json.dumps(payload).encode())

    def load(self, state, identifier):
        if not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise EditError("invalid_id", "Expected a plan or undo ID returned by ComfyEdit.")
        p = state / (identifier + ".json")
        if p.is_symlink() or not p.is_file():
            raise EditError("unknown_id", "Plan or undo ID not found.")
        return json.loads(p.read_text())

    def preview(self, edits):
        """Edits run in list order; versions always describe original file bytes."""
        if not edits or len(edits) > 100:
            raise EditError("invalid_edits", "Supply between 1 and 100 edits.")
        with self.lock() as state:
            before, after = {}, {}
            for edit in edits:
                file = edit["file"]
                if file not in before:
                    before[file] = self.read_bytes(file).decode("utf-8")
                    after[file] = before[file]
                if edit.get("version") != digest(before[file].encode()):
                    raise EditError("stale_version", "Read the file again and rebuild this edit.", file=file,
                                    current_version=digest(before[file].encode()))
                source = after[file]
                op = edit["operation"]
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
                    lines = source.splitlines(keepends=True)
                    indent = re.match(r"\s*", lines[node.lineno-1]).group()
                    newline = "\r\n" if "\r\n" in source else "\n"
                    content = textwrap.dedent(edit["code"]).strip("\r\n")
                    code = textwrap.indent(content, indent).replace("\r\n", "\n").replace("\n", newline) + newline
                    if op == "insert_before":
                        last = first - 1
                    elif op == "insert_after":
                        first = last + 1
                        if last and not lines[last-1].endswith(("\r", "\n")):
                            code = newline + code
                    after[file] = "".join(lines[:first-1]) + code + "".join(lines[last:])
                else:
                    raise EditError("unknown_operation", "Unknown edit operation.", operation=op)
            return self.make_plan(state, before, after)

    def make_plan(self, state, before, after, guards=None, warnings=None):
        changed = {f: t for f, t in after.items() if t != before[f]}
        for f, source in changed.items():
            if len(source.encode()) > self.MAX_BYTES:
                raise EditError("file_too_large", "Result exceeds 2 MB.", file=f)
            if f.endswith(".py"):
                try:
                    compile(source, f, "exec", dont_inherit=True)
                except SyntaxError as e:
                    raise EditError("syntax_error", "Proposed Python edit does not compile.",
                                    file=f, line=e.lineno, diagnostic=e.msg)
        if not changed:
            return {"ok": True, "status": "no_change", "files": []}
        identifier = uuid.uuid4().hex
        payload = {"kind": "plan", "before": {f: before[f] for f in changed}, "after": changed,
                   "guards": guards or {f: digest(t.encode()) for f, t in before.items()}}
        self.save(state, identifier, payload)
        diff = "".join("".join(difflib.unified_diff(before[f].splitlines(keepends=True),
                    t.splitlines(keepends=True), fromfile="a/"+f, tofile="b/"+f)) for f, t in changed.items())
        return {"ok": True, "status": "preview", "plan_id": identifier, "files": list(changed),
                "diff": diff[:24000], "diff_truncated": len(diff) > 24000,
                "diagnostics": [], "warnings": warnings or [],
                "next": "commit_edit(plan_id) applies exactly this preview; read_code can inspect changed files afterward."}

    def commit_edit(self, plan_id):
        with self.lock() as state:
            plan = self.load(state, plan_id)
            if plan["kind"] != "plan":
                raise EditError("used_plan", "This plan has already been applied.")
            if plan.get("python_inventory") is not None and self.python_files() != plan["python_inventory"]:
                raise EditError("stale_version", "Python file inventory changed; rebuild the rename.")
            for f, version in plan["guards"].items():
                if digest(self.read_bytes(f)) != version:
                    raise EditError("stale_version", "File changed since preview; rebuild the edit.", file=f)
            # Journal before writes: interrupted transactions remain inspectable on disk.
            undo_id = uuid.uuid4().hex
            journal = {"kind": "pending", "before": plan["before"], "after": plan["after"]}
            self.save(state, undo_id, journal)
            written = []
            try:
                for f, content in plan["after"].items():
                    self.atomic_write(self.path(f), content.encode())
                    written.append(f)
            except Exception:
                for f in reversed(written):
                    self.atomic_write(self.path(f), plan["before"][f].encode())
                raise
            journal["kind"] = "undo"
            self.save(state, undo_id, journal)
            plan["kind"] = "applied"
            self.save(state, plan_id, plan)
            return {"ok": True, "status": "applied", "undo_id": undo_id,
                    "files": [{"file": f, "version": digest(t.encode())} for f, t in plan["after"].items()]}

    def undo_edit(self, undo_id):
        with self.lock() as state:
            journal = self.load(state, undo_id)
            if journal["kind"] != "undo":
                raise EditError("invalid_undo", "This transaction is not available for undo.")
            for f, content in journal["after"].items():
                if self.read_bytes(f) != content.encode():
                    raise EditError("undo_conflict", "File has subsequent edits; undo will not overwrite them.", file=f)
            return self.make_plan(state, journal["after"], journal["before"])

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
            lines = before[file].splitlines(keepends=True)
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
