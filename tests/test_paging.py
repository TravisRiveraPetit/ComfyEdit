"""Regression cases for complete, faithful source and preview inspection."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from comfyedit import Editor, EditError
from comfyedit.cli import dispatch


class PagingTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.editor = Editor(self.root)

    def write(self, file, source):
        (self.root / file).write_bytes(source.encode())

    def preview_text(self, file, old, new):
        return self.editor.preview([dict(operation="replace_text", file=file,
            version=self.editor.read_code(file)["version"], old=old, new=new)])

    def test_symbol_paging_stays_inside_decorated_definition(self):
        source = "# header\n@decorate\ndef target():\n    x = 1\n    return x\n\ndef other(): pass\n"
        self.write("a.py", source)
        first = self.editor.read_code("a.py", symbol="target", max_lines=2)
        self.assertEqual(first["text"], "@decorate\ndef target():\n")
        self.assertEqual(first["next_line"], 4)
        last = self.editor.read_code("a.py", symbol="target", start_line=first["next_line"], max_lines=2)
        self.assertEqual(last["text"], "    x = 1\n    return x\n")
        self.assertIsNone(last["next_line"])
        self.assertEqual(first["version"], last["version"])
        empty = self.editor.read_code("a.py", symbol="target", start_line=6)
        self.assertEqual(empty["text"], "")
        self.assertIsNone(empty["next_line"])

    def test_python_line_numbers_ignore_unicode_separators(self):
        for separator in ("\u2028", "\u2029", "\x85", "\v", "\f", "\x1c"):
            with self.subTest(separator=repr(separator)):
                prefix = f'text = "a{separator}b"\n'
                self.write("a.py", prefix + "def target():\n    return 1\n\nsentinel = 7\n")
                read = self.editor.read_code("a.py", symbol="target")
                self.assertEqual(read["text"], "def target():\n    return 1\n")
                plan = self.editor.preview([dict(operation="replace_symbol", file="a.py",
                    version=read["version"], symbol="target", code="def target():\n    return 2")])
                result = self.editor.commit_edit(plan["plan_id"])
                self.assertEqual((self.root / "a.py").read_bytes(),
                    (prefix + "def target():\n    return 2\n\nsentinel = 7\n").encode())
                undo = self.editor.undo_edit(result["undo_id"])
                self.editor.commit_edit(undo["plan_id"])
                self.assertIn(b"return 1", (self.root / "a.py").read_bytes())

    def test_carriage_return_lines_preserved(self):
        original = "# header\rdef target():\r    return 1\rsentinel = 7\r"
        self.write("a.py", original)
        read = self.editor.read_code("a.py", symbol="target")
        self.assertEqual(read["text"], "def target():\r    return 1\r")
        plan = self.editor.preview([dict(operation="replace_symbol", file="a.py",
            version=read["version"], symbol="target", code="def target():\n    return 2")])
        self.editor.commit_edit(plan["plan_id"])
        self.assertEqual((self.root / "a.py").read_bytes(), original.replace("1", "2").encode())

    def test_complete_large_multifile_unicode_diff(self):
        edits, expected = [], ""
        for file, old, new in [("a.txt", "é" * 15000, "🌱" * 15000),
                                ("b.txt", "before", "after")]:
            self.write(file, old + "\n")
            edits.append(dict(operation="replace_text", file=file,
                version=self.editor.read_code(file)["version"], old=old, new=new))
            expected += f"--- a/{file}\n+++ b/{file}\n@@ -1 +1 @@\n-{old}\n+{new}\n"
        plan = self.editor.preview(edits)
        self.assertTrue(plan["diff_truncated"])
        self.assertEqual(plan["next_offset"], 24000)
        self.assertEqual(plan["total_chars"], len(expected))
        pieces, offset = [plan["diff"]], plan["next_offset"]
        # Another process can inspect the persisted plan, even after sources change.
        self.write("a.txt", "external edit\n")
        reader = Editor(self.root)
        while offset is not None:
            page = reader.read_diff(plan["plan_id"], offset=offset, max_chars=997)
            self.assertEqual(page["offset"], offset)
            self.assertLessEqual(len(page["diff"]), 997)
            pieces.append(page["diff"])
            offset = page["next_offset"]
        self.assertEqual("".join(pieces), expected)
        end = reader.read_diff(plan["plan_id"], offset=len(expected))
        self.assertEqual(end["diff"], "")
        self.assertIsNone(end["next_offset"])
        self.assertFalse(end["diff_truncated"])
        with self.assertRaises(EditError) as caught:
            self.editor.commit_edit(plan["plan_id"])
        self.assertEqual(caught.exception.code, "stale_version")
        self.assertEqual((self.root / "b.txt").read_text(), "before\n")

    def test_diff_marks_missing_final_newlines(self):
        self.write("a.txt", "old")
        plan = self.preview_text("a.txt", "old", "new")
        expected = ("--- a/a.txt\n+++ b/a.txt\n@@ -1 +1 @@\n"
                    "-old\n\\ No newline at end of file\n"
                    "+new\n\\ No newline at end of file\n")
        self.assertEqual(plan["diff"], expected)
        self.assertIsNone(plan["next_offset"])
        applied = self.editor.commit_edit(plan["plan_id"])
        self.assertEqual(self.editor.read_diff(plan["plan_id"])["diff"], expected)
        reverse = self.editor.undo_edit(applied["undo_id"])
        self.assertIn("-new\n", self.editor.read_diff(reverse["plan_id"])["diff"])
        self.editor.commit_edit(reverse["plan_id"])
        self.assertEqual((self.root / "a.txt").read_bytes(), b"old")
        invalid = dispatch(self.editor, dict(tool="read_diff", plan_id=applied["undo_id"]))
        self.assertEqual(invalid["error"]["code"], "invalid_plan")

    def test_invalid_paging_requests_are_structured_errors(self):
        self.write("a.txt", "old")
        plan = self.preview_text("a.txt", "old", "new")
        for args in ({"offset": -1}, {"offset": True}, {"offset": 1.5},
                     {"offset": plan["total_chars"] + 1}, {"max_chars": 0},
                     {"max_chars": 24001}, {"max_chars": "10"}, {"max_chars": False}):
            with self.subTest(args=args):
                result = dispatch(self.editor, dict(tool="read_diff", plan_id=plan["plan_id"], **args))
                self.assertEqual(result["error"]["code"], "invalid_range")
        for args in ({"start_line": 0}, {"start_line": True}, {"max_lines": 1.5}):
            result = dispatch(self.editor, dict(tool="read_code", file="a.txt", **args))
            self.assertEqual(result["error"]["code"], "invalid_range")
        for identifier, code in [("../bad", "invalid_id"), ("0" * 32, "unknown_id")]:
            result = dispatch(self.editor, dict(tool="read_diff", plan_id=identifier))
            self.assertEqual(result["error"]["code"], code)

    def test_nul_proposal_cannot_make_file_unreadable_or_unundoable(self):
        self.write("a.txt", "old")
        with self.assertRaises(EditError) as caught:
            self.preview_text("a.txt", "old", "new\0")
        self.assertEqual(caught.exception.code, "binary_file")
        self.assertEqual((self.root / "a.txt").read_bytes(), b"old")
        self.assertEqual(list((self.root / ".comfyedit").glob("*.json")), [])

    def test_cli_reads_persisted_diff_as_json(self):
        self.write("a.txt", "old")
        plan = self.preview_text("a.txt", "old", "new")
        def call(request):
            result = subprocess.run([sys.executable, "-m", "comfyedit", "--root", str(self.root)],
                input=json.dumps(request), text=True, capture_output=True)
            self.assertEqual(result.stderr, "")
            return result.returncode, json.loads(result.stdout)
        status, page = call(dict(tool="read_diff", plan_id=plan["plan_id"], max_chars=10))
        self.assertEqual(status, 0)
        self.assertEqual(page["diff"], plan["diff"][:10])
        self.assertEqual(page["next_offset"], 10)
        status, error = call(dict(tool="read_diff", plan_id=plan["plan_id"], offset=-1))
        self.assertEqual(status, 1)
        self.assertEqual(error["error"]["code"], "invalid_range")


if __name__ == "__main__":
    unittest.main()
