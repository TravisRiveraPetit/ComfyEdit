import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from comfyedit import Editor, EditError
from comfyedit.cli import dispatch


class EditorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.editor = Editor(self.root)
        self.write("a.py", "class Planner:\n    def solve(self):\n        return 1\n")

    def write(self, file, content):
        (self.root / file).write_bytes(content.encode())

    def edit(self, file="a.py", **kwargs):
        return dict(file=file, version=self.editor.read_code(file)["version"], **kwargs)

    def assert_error(self, code, fn, *args, **kwargs):
        with self.assertRaises(EditError) as caught:
            fn(*args, **kwargs)
        self.assertEqual(code, caught.exception.code)

    def test_replace_symbol_preview_commit_undo(self):
        original = (self.root/"a.py").read_bytes()
        plan = self.editor.preview([self.edit(operation="replace_symbol", symbol="Planner.solve", code="def solve(self):\n    return 42")])
        self.assertEqual(original, (self.root/"a.py").read_bytes())
        applied = self.editor.commit_edit(plan["plan_id"])
        self.assertIn("        return 42", (self.root/"a.py").read_text())
        reverse = self.editor.undo_edit(applied["undo_id"])
        self.editor.commit_edit(reverse["plan_id"])
        self.assertEqual(original, (self.root/"a.py").read_bytes())

    def test_ambiguous_text(self):
        self.write("x.txt", "cat cat")
        self.assert_error("match_count", self.editor.preview, [self.edit("x.txt", operation="replace_text", old="cat", new="dog")])

    def test_stale_read(self):
        edit = self.edit(operation="replace_text", old="return 1", new="return 2")
        self.write("a.py", "x = 3\n")
        self.assert_error("stale_version", self.editor.preview, [edit])

    def test_stale_commit_all_files_unchanged(self):
        self.write("b.py", "x = 1\n")
        plan = self.editor.preview([self.edit(operation="replace_text", old="return 1", new="return 2"), self.edit("b.py", operation="replace_text", old="1", new="2")])
        self.write("b.py", "x = 3\n")
        self.assert_error("stale_version", self.editor.commit_edit, plan["plan_id"])
        self.assertIn("return 1", (self.root/"a.py").read_text())

    def test_syntax_failure(self):
        self.assert_error("syntax_error", self.editor.preview, [self.edit(operation="replace_text", old="return 1", new="return (")])

    def test_undo_conflict(self):
        p = self.editor.preview([self.edit(operation="replace_text", old="return 1", new="return 2")])
        result = self.editor.commit_edit(p["plan_id"])
        self.write("a.py", "x = 9\n")
        self.assert_error("undo_conflict", self.editor.undo_edit, result["undo_id"])

    def test_path_escape(self):
        for file in ["../x", "/etc/passwd", ".git/config", ".comfyedit/lock", "./a.py", "dir//a.py"]:
            self.assert_error("unsafe_path", self.editor.read_code, file)

    def test_symlink(self):
        (self.root/"link.py").symlink_to(self.root/"a.py")
        self.assert_error("unsafe_path", self.editor.read_code, "link.py")

    def test_decorators_and_crlf(self):
        self.write("a.py", "class P:\r\n    @staticmethod\r\n    def f():\r\n        return 1\r\n")
        p = self.editor.preview([self.edit(operation="replace_symbol", symbol="P.f", code="@staticmethod\ndef f():\n    return 2")])
        self.editor.commit_edit(p["plan_id"])
        self.assertEqual((self.root/"a.py").read_bytes(), b"class P:\r\n    @staticmethod\r\n    def f():\r\n        return 2\r\n")

    def test_insertion(self):
        p = self.editor.preview([self.edit(operation="insert_after", symbol="Planner.solve", code="def other(self):\n    return 3")])
        self.editor.commit_edit(p["plan_id"])
        self.assertIn("Planner.other", [s["symbol"] for s in self.editor.read_code("a.py")["symbols"]])

    def test_ordered_batch(self):
        p = self.editor.preview([self.edit(operation="replace_text", old="return 1", new="return 2"), self.edit(operation="replace_text", old="return 2", new="return 3")])
        self.editor.commit_edit(p["plan_id"])
        self.assertIn("return 3", (self.root/"a.py").read_text())

    def test_ambiguous_symbol(self):
        self.write("a.py", "def f(): pass\ndef f(): pass\n")
        self.assert_error("symbol_not_unique", self.editor.preview, [self.edit(operation="replace_symbol", symbol="f", code="def f(): return 2")])

    def test_reused_plan(self):
        p = self.editor.preview([self.edit(operation="replace_text", old="return 1", new="return 2")])
        self.editor.commit_edit(p["plan_id"])
        self.assert_error("used_plan", self.editor.commit_edit, p["plan_id"])

    def test_write_failure_rolls_back(self):
        self.write("b.py", "x = 1\n")
        before = (self.root/"a.py").read_bytes()
        p = self.editor.preview([self.edit(operation="replace_text", old="return 1", new="return 2"), self.edit("b.py", operation="replace_text", old="1", new="2")])
        real = self.editor.atomic_write
        def failing(path, data):
            if path == self.root/"b.py":
                raise OSError("simulated write failure")
            return real(path, data)
        with patch.object(self.editor, "atomic_write", side_effect=failing):
            with self.assertRaises(OSError):
                self.editor.commit_edit(p["plan_id"])
        self.assertEqual(before, (self.root/"a.py").read_bytes())

    def test_modes_preserved(self):
        (self.root/"a.py").chmod(0o755)
        p = self.editor.preview([self.edit(operation="replace_text", old="return 1", new="return 2")])
        self.editor.commit_edit(p["plan_id"])
        self.assertEqual((self.root/"a.py").stat().st_mode & 0o777, 0o755)

    def test_structured_error(self):
        r = dispatch(self.editor, {"tool": "preview", "edits": [{}]})
        self.assertFalse(r["ok"])
        self.assertEqual(r["error"]["code"], "invalid_request")

    def test_rename_cross_file(self):
        self.write("b.py", "from a import Planner\nanswer = Planner().solve()\n\nclass Other:\n    def solve(self): return 9\n\nx = Other().solve()\n")
        p = self.editor.rename_symbol("a.py", "Planner.solve", "find_plan", self.editor.read_code("a.py")["version"])
        self.editor.commit_edit(p["plan_id"])
        content = (self.root/"b.py").read_text()
        self.assertIn("Planner().find_plan()", content)
        self.assertIn("Other().solve()", content)
        self.assertIn("def find_plan", (self.root/"a.py").read_text())

    def test_rename_after_unicode_line_separator(self):
        prefix = 'text = "a\u2028b"\n'
        self.write("a.py", prefix + "def solve(): return 1\n")
        self.write("b.py", "from a import solve\nanswer = solve()\n")
        plan = self.editor.rename_symbol("a.py", "solve", "find_plan", self.editor.read_code("a.py")["version"])
        self.assertEqual(self.editor.read_diff(plan["plan_id"])["diff"], plan["diff"])
        self.editor.commit_edit(plan["plan_id"])
        self.assertEqual((self.root / "a.py").read_text(), prefix + "def find_plan(): return 1\n")
        self.assertEqual((self.root / "b.py").read_text(), "from a import find_plan\nanswer = find_plan()\n")

    def test_rename_inventory_guard(self):
        p = self.editor.rename_symbol("a.py", "Planner.solve", "find_plan", self.editor.read_code("a.py")["version"])
        self.write("new.py", "from a import Planner\nx = Planner().solve()\n")
        self.assert_error("stale_version", self.editor.commit_edit, p["plan_id"])


if __name__ == "__main__":
    unittest.main()
