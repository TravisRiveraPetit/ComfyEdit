"""Optional Rope-backed semantic reference discovery."""
import importlib.util
from pathlib import Path
import tempfile
import unittest

from comfyedit import Editor, EditError


@unittest.skipUnless(importlib.util.find_spec("rope"), "Install .[python] for semantic tests")
class SemanticTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.editor = Editor(self.root)
        (self.root / "a.py").write_text("def answer(value):\n    return value\n\nanswer(1)\n")
        (self.root / "b.py").write_text("from a import answer\nresult = answer(2)\n")

    def test_find_references_returns_kinds_locations_and_inventory_guard(self):
        read = self.editor.read_code("a.py", symbol="answer")
        result = self.editor.find_references("a.py", "answer", read["version"], limit=2)
        self.assertTrue(result["ok"])
        self.assertFalse(result["complete"])
        self.assertEqual(result["total"], 4)
        self.assertEqual([(item["file"], item["kind"]) for item in result["references"]],
                         [("a.py", "definition"), ("a.py", "call")])
        self.assertIsNotNone(result["next_offset"])
        next_page = self.editor.find_references("a.py", "answer", read["version"], offset=result["next_offset"],
                                                inventory_version=result["inventory_version"])
        self.assertEqual([(item["file"], item["kind"]) for item in next_page["references"]],
                         [("b.py", "import"), ("b.py", "call")])
        (self.root / "b.py").write_text("from a import answer\nresult = answer(99)\n")
        self.assert_error("stale_version", self.editor.find_references, "a.py", "answer", read["version"],
                          offset=result["next_offset"], inventory_version=result["inventory_version"])
        self.assert_error("stale_version", self.editor.find_references, "a.py", "answer", read["version"],
                          inventory_version="stale")

    def test_ambiguous_or_missing_symbol_is_actionable(self):
        read = self.editor.read_code("a.py")
        with self.assertRaises(EditError) as caught:
            self.editor.find_references("a.py", "missing", read["version"])
        self.assertEqual(caught.exception.code, "symbol_not_unique")

    def test_reference_range_ending_at_eof_uses_final_line_without_newline(self):
        (self.root / "c.py").write_text("from a import answer\nx = answer")
        read = self.editor.read_code("a.py", symbol="answer")
        result = self.editor.find_references("a.py", "answer", read["version"])
        reference = next(item for item in result["references"] if item["file"] == "c.py" and item["line"] == 2)
        self.assertEqual((reference["line"], reference["column"], reference["end_line"], reference["end_column"]),
                         (2, 5, 2, 11))

    def test_crlf_references_and_renames_keep_logical_columns_and_newlines(self):
        target = self.root / "crlf.py"
        target.write_bytes(b"def answer(value):\r\n    return value\r\n")
        caller = self.root / "caller.py"
        caller.write_bytes(b"from crlf import answer\r\nresult = answer(1)\r\n")
        read = self.editor.read_code("crlf.py", symbol="answer")
        references = self.editor.find_references("crlf.py", "answer", read["version"])
        call = next(item for item in references["references"] if item["file"] == "caller.py" and item["kind"] == "call")
        self.assertEqual((call["line"], call["column"], call["end_line"], call["end_column"]), (2, 10, 2, 16))
        plan = self.editor.rename_symbol("crlf.py", "answer", "respond", read["version"])
        self.assertNotIn("-    return value", plan["diff"])
        self.assertNotIn("+    return value", plan["diff"])
        self.editor.commit_edit(plan["plan_id"])
        self.assertNotIn(b"\n", target.read_bytes().replace(b"\r\n", b""))
        self.assertIn(b"respond", caller.read_bytes())

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(EditError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)


if __name__ == "__main__":
    unittest.main()
