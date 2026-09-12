"""Bounded discovery without executing project code or shell commands."""
import fnmatch
import os
from pathlib import Path
import subprocess


class DiscoveryMixin:
    def workspace_files(self, pattern="*"):
        from .engine import EditError
        if not isinstance(pattern, str) or not pattern or len(pattern) > 1000:
            raise EditError("invalid_request", "pattern must be a nonempty glob of at most 1,000 characters.")
        # ls-files respects ignored untracked files while retaining tracked files.
        # Disable fsmonitor: user Git configuration can otherwise execute a hook.
        try:
            result = subprocess.run(["git", "-c", "core.fsmonitor=false", "ls-files",
                "--cached", "--others", "--exclude-standard", "-z"], cwd=self.root,
                stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=10)
            names = result.stdout.decode("utf-8").split("\0") if result.returncode == 0 else None
        except (FileNotFoundError, subprocess.TimeoutExpired, UnicodeDecodeError):
            names = None
        if names is None:
            names = []
            for root, dirs, files in os.walk(self.root, followlinks=False):
                dirs[:] = sorted(d for d in dirs if d not in self.SKIP and not (Path(root) / d).is_symlink())
                names.extend((Path(root) / name).relative_to(self.root).as_posix() for name in files)
                if len(names) > 10000:
                    raise EditError("project_too_large", "Discovery supports at most 10,000 files; scope the root more narrowly.")
        selected = []
        for name in sorted(set(names)):
            if not name or any(part in self.SKIP for part in Path(name).parts) or not fnmatch.fnmatchcase(name, pattern):
                continue
            try:
                path = self.path(name)
                if path.is_file():
                    selected.append(name)
            except EditError:
                continue
        if len(selected) > 10000:
            raise EditError("project_too_large", "Discovery supports at most 10,000 matching files; narrow the pattern.")
        return selected

    def list_files(self, pattern="*", offset=0, limit=100, version=None):
        from .engine import EditError, digest
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise EditError("invalid_range", "offset must be nonnegative; limit must be 1..200.")
        files = self.workspace_files(pattern)
        current_version = digest("\0".join(files).encode())
        if version is not None and version != current_version:
            raise EditError("stale_version", "File inventory changed; restart discovery.", current_version=current_version)
        return {"ok": True, "files": files[offset:offset + limit], "version": current_version,
                "total_files": len(files), "next_offset": offset + limit if offset + limit < len(files) else None}

    def search_code(self, query, pattern="*", offset=0, max_results=50, version=None):
        from .engine import EditError, digest, source_lines
        if not isinstance(query, str) or not query or len(query) > 1000 or "\n" in query or "\r" in query:
            raise EditError("invalid_request", "query must be a nonempty, single-line literal of at most 1,000 characters.")
        if type(offset) is not int or offset < 0 or type(max_results) is not int or not 1 <= max_results <= 100:
            raise EditError("invalid_range", "offset must be nonnegative; max_results must be 1..100.")
        matches, skipped, seen, scanned_bytes, snapshots = [], [], 0, 0, []
        for file in self.workspace_files(pattern):
            try:
                data = self.read_bytes(file)
            except EditError as error:
                if error.code not in {"encoding", "binary_file", "file_too_large", "missing_file"}:
                    raise
                skipped.append({"file": file, "code": error.code})
                continue
            scanned_bytes += len(data)
            if scanned_bytes > 20_000_000:
                raise EditError("search_too_large", "Search scans at most 20 MB per call; narrow the file pattern.")
            snapshots.append((file, data, digest(data)))
        search_version = digest("\0".join(
            [file + "\0" + file_version for file, _, file_version in snapshots] +
            [item["file"] + "\0skip:" + item["code"] for item in skipped]).encode())
        if version is not None and version != search_version:
            raise EditError("stale_version", "Search results changed; restart the search.",
                            current_version=search_version)
        for file, data, file_version in snapshots:
            for number, line in enumerate(source_lines(data.decode()), 1):
                position = 0
                while (column := line.find(query, position)) != -1:
                    position = column + len(query)
                    if seen >= offset:
                        if len(matches) == max_results:
                            return {"ok": True, "matches": matches, "next_offset": offset + max_results,
                                    "version": search_version, "skipped": skipped[:20], "skipped_count": len(skipped)}
                        start = max(0, column - 80)
                        text = line[start:start + 240].rstrip("\r\n")
                        matches.append({"file": file, "version": file_version, "line": number,
                            "column": column + 1, "match_length": len(query), "text": text,
                            "text_start_column": start + 1,
                            "text_truncated": start > 0 or start + 240 < len(line.rstrip("\r\n"))})
                    seen += 1
        return {"ok": True, "matches": matches, "next_offset": None, "version": search_version,
                "skipped": skipped[:20], "skipped_count": len(skipped)}
