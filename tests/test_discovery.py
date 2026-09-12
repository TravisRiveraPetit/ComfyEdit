import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from comfyedit import Editor, EditError
from comfyedit.cli import dispatch


class DiscoveryTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.editor = Editor(self.root)

    def write(self, file, text):
        path = self.root / file
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def test_inventory_pages_and_version_guard(self):
        for file in ("a.py", "src/b.py", "c.txt", ".venv/hidden.py", ".comfyedit/hidden.py"):
            self.write(file, "x = 1\n")
        (self.root / "linked.py").symlink_to(self.root / "a.py")
        first = self.editor.list_files("*.py", limit=1)
        self.assertEqual(first["files"], ["a.py"])
        last = self.editor.list_files("*.py", offset=first["next_offset"], version=first["version"])
        self.assertEqual(last["files"], ["src/b.py"])
        self.assertIsNone(last["next_offset"])
        self.write("new.py", "x = 2\n")
        with self.assertRaises(EditError) as caught:
            self.editor.list_files("*.py", offset=1, version=first["version"])
        self.assertEqual(caught.exception.code, "stale_version")

    def test_without_git_discovery_still_works(self):
        self.write("src/demo.py", "x = 1\n")
        with patch("comfyedit.discovery.subprocess.run", side_effect=FileNotFoundError):
            self.assertEqual(self.editor.list_files()["files"], ["src/demo.py"])

    @unittest.skipUnless(shutil.which("git"), "Git is optional")
    def test_gitignore_honored_and_fsmonitor_never_executed(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True, capture_output=True)
        self.write(".gitignore", "ignored.py\n")
        self.write("visible.py", "x = 1\n")
        self.write("ignored.py", "x = 2\n")
        self.write("hook.sh", "#!/bin/sh\ntouch executed\n")
        (self.root / "hook.sh").chmod(0o755)
        subprocess.run(["git", "-C", str(self.root), "config", "core.fsmonitor", "./hook.sh"], check=True)
        self.assertEqual(self.editor.list_files("*.py")["files"], ["visible.py"])
        self.assertFalse((self.root / "executed").exists())

    def test_literal_unicode_matches_have_precise_versions_and_columns(self):
        self.write("a.txt", "é cat cat\n[.*] cat\n")
        first = self.editor.search_code("cat", max_results=2)
        self.assertEqual([(m["line"], m["column"]) for m in first["matches"]], [(1, 3), (1, 7)])
        self.assertEqual(first["matches"][0]["version"], self.editor.read_code("a.txt")["version"])
        last = self.editor.search_code("cat", offset=first["next_offset"], version=first["version"])
        self.assertEqual([(m["line"], m["column"]) for m in last["matches"]], [(2, 6)])
        self.assertIsNone(last["next_offset"])
        self.assertEqual(len(self.editor.search_code("[.*]")["matches"]), 1)
        self.write("a.txt", "changed\n")
        with self.assertRaises(EditError) as caught:
            self.editor.search_code("cat", offset=2, version=first["version"])
        self.assertEqual(caught.exception.code, "stale_version")

    def test_search_bounds_snippets_and_reports_skipped_binary_files(self):
        self.write("a.txt", "x" * 50000 + "needle" + "x" * 50000)
        (self.root / "binary").write_bytes(b"\xff\0")
        result = self.editor.search_code("needle")
        match = result["matches"][0]
        self.assertEqual(match["column"], 50001)
        self.assertLessEqual(len(match["text"]), 240)
        self.assertIn("needle", match["text"])
        self.assertTrue(match["text_truncated"])
        self.assertEqual(result["skipped_count"], 1)

    def test_long_line_read_pages_are_lossless_and_guarded(self):
        source = "é🌱" * 15000 + "\r\nlast\n"
        self.write("a.txt", source)
        page = self.editor.read_code("a.txt", max_chars=997)
        version = page["version"]
        pieces = []
        while True:
            self.assertLessEqual(len(page["text"]), 997)
            pieces.append(page["text"])
            if page["next_line"] is None:
                break
            page = self.editor.read_code("a.txt", start_line=page["next_line"],
                start_column=page["next_column"], max_chars=997, version=version)
        self.assertEqual("".join(pieces), source)
        self.write("a.txt", "changed")
        error = dispatch(self.editor, dict(tool="read_code", file="a.txt", version=version, start_line=1, start_column=998))
        self.assertEqual(error["error"]["code"], "stale_version")

    def test_outline_can_be_disabled_or_paged(self):
        self.write("a.py", "".join(f"def f{i}(): pass\n" for i in range(205)))
        first = self.editor.read_code("a.py", max_symbols=100, max_chars=20)
        self.assertEqual(len(first["symbols"]), 100)
        self.assertEqual(first["next_symbol_offset"], 100)
        last = self.editor.read_code("a.py", symbol_offset=200, max_chars=20)
        self.assertEqual(len(last["symbols"]), 5)
        self.assertIsNone(last["next_symbol_offset"])
        self.assertEqual(self.editor.read_code("a.py", include_symbols=False)["symbols"], [])

    def test_invalid_requests_return_errors(self):
        for request in ([], "read_code", None, {"tool": "search_code", "query": ""},
                        {"tool": "search_code", "query": "x\ny"},
                        {"tool": "list_files", "limit": 1000},
                        {"tool": "list_files", "pattern": None}):
            self.assertFalse(dispatch(self.editor, request)["ok"], json.dumps(request))


if __name__ == "__main__":
    unittest.main()
