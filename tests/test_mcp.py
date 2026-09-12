import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

try:
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
except ImportError:
    ClientSession = None


@unittest.skipIf(ClientSession is None, "Install .[agent] for MCP tests")
class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_real_stdio_edit_and_undo(self):
        with tempfile.TemporaryDirectory() as root:
            source = Path(root)/"demo.py"
            source.write_text("def answer():\n    return 1\n")
            env = dict(os.environ)
            env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1]/"src")
            params = StdioServerParameters(command=sys.executable,
                args=["-m", "comfyedit", "--root", root, "--mcp"], env=env)
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    listed = await session.list_tools()
                    self.assertEqual({x.name for x in listed.tools},
                        {"read_code", "read_diff", "preview", "rename_symbol", "commit_edit", "undo_edit",
                         "list_files", "search_code", "list_previews", "discard_preview",
                         "list_transactions", "recover_transaction", "validate", "find_references"})
                    async def call(name, args):
                        result = await session.call_tool(name, args)
                        self.assertFalse(result.isError, result)
                        return json.loads(result.content[0].text)
                    r = await call("read_code", {"file": "demo.py"})
                    p = await call("preview", {"edits": [{"operation": "replace_symbol", "file": "demo.py",
                        "version": r["version"], "symbol": "answer", "code": "def answer():\n    return 42"}]})
                    self.assertEqual(source.read_text(), "def answer():\n    return 1\n")
                    tools = {tool.name: tool for tool in listed.tools}
                    self.assertTrue(tools["read_diff"].annotations.readOnlyHint)
                    pieces, offset = [], 0
                    while offset is not None:
                        page = await call("read_diff", {"plan_id": p["plan_id"], "offset": offset, "max_chars": 7})
                        self.assertTrue(page["ok"])
                        pieces.append(page["diff"])
                        offset = page["next_offset"]
                    self.assertEqual("".join(pieces), p["diff"])
                    invalid = await call("read_diff", {"plan_id": p["plan_id"], "offset": -1})
                    self.assertEqual(invalid["error"]["code"], "invalid_range")
                    first = await call("read_code", {"file": "demo.py", "symbol": "answer", "max_lines": 1})
                    last = await call("read_code", {"file": "demo.py", "symbol": "answer", "start_line": first["next_line"]})
                    self.assertEqual(last["text"], "    return 1\n")
                    self.assertIsNone(last["next_line"])
                    result = await call("commit_edit", {"plan_id": p["plan_id"]})
                    self.assertIn("42", source.read_text())
                    undo = await call("undo_edit", {"undo_id": result["undo_id"]})
                    await call("commit_edit", {"plan_id": undo["plan_id"]})
                    self.assertEqual(source.read_text(), "def answer():\n    return 1\n")
                    regex_read = await call("read_code", {"file": "demo.py"})
                    regex_plan = await call("preview", {"edits": [{
                        "operation": "replace_regex", "file": "demo.py", "version": regex_read["version"],
                        "pattern": "return (\\d+)", "replacement": "return \\g<1> + 0",
                        "expected_matches": 1}]})
                    self.assertEqual(regex_plan["match_reports"][0]["edit_index"], 0)
                    regex_result = await call("commit_edit", {"plan_id": regex_plan["plan_id"]})
                    self.assertIn("return 1 + 0", source.read_text())
                    regex_undo = await call("undo_edit", {"undo_id": regex_result["undo_id"]})
                    await call("commit_edit", {"plan_id": regex_undo["plan_id"]})
                    error = await call("read_code", {"file": "missing.py"})
                    self.assertEqual(error["error"]["code"], "missing_file")
                    files = await call("list_files", {"pattern": "*.py"})
                    self.assertEqual(files["files"], ["demo.py"])
                    matches = await call("search_code", {"query": "return 1"})
                    self.assertEqual(matches["matches"][0]["line"], 2)
                    validation = await call("validate", {"command": [sys.executable, "-c", "print('mcp ok')"]})
                    self.assertEqual(validation["status"], "passed")
                    self.assertEqual(validation["stdout"], "mcp ok\n")
                    references = await call("find_references", {"file": "demo.py", "symbol": "answer", "version": r["version"]})
                    self.assertEqual(references["references"][0]["kind"], "definition")
                    created = await call("preview", {"edits": [
                        {"operation": "create_file", "file": "new.py", "code": "x = 1\n"},
                        {"operation": "move_file", "file": "new.py", "version": None, "destination": "src/moved.py"},
                        {"operation": "create_file", "file": "throwaway.txt", "code": ""},
                        {"operation": "delete_file", "file": "throwaway.txt", "version": None},
                    ]})
                    self.assertTrue(created["ok"], created)
                    committed = await call("commit_edit", {"plan_id": created["plan_id"]})
                    self.assertTrue(committed["ok"], committed)
                    self.assertEqual((Path(root) / "src/moved.py").read_text(), "x = 1\n")
                    reverse = await call("undo_edit", {"undo_id": committed["undo_id"]})
                    await call("commit_edit", {"plan_id": reverse["plan_id"]})
                    self.assertFalse((Path(root) / "src/moved.py").exists())
                    from comfyedit import Editor
                    from unittest.mock import patch
                    editor = Editor(root)
                    pending = editor.preview([{"operation": "create_file", "file": "pending.txt", "code": "pending"}])
                    with patch.object(editor, "write_snapshot", side_effect=SystemExit):
                        with self.assertRaises(SystemExit):
                            editor.commit_edit(pending["plan_id"])
                    transactions = await call("list_transactions", {})
                    identifier = transactions["transactions"][0]["transaction_id"]
                    recovery = await call("recover_transaction", {"transaction_id": identifier, "action": "finish"})
                    self.assertTrue(recovery["ok"], recovery)
                    self.assertEqual((Path(root) / "pending.txt").read_text(), "pending")
