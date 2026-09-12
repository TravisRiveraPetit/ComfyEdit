import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


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
                        {"read_code", "preview", "rename_symbol", "commit_edit", "undo_edit"})
                    async def call(name, args):
                        result = await session.call_tool(name, args)
                        self.assertFalse(result.isError, result)
                        return json.loads(result.content[0].text)
                    r = await call("read_code", {"file": "demo.py"})
                    p = await call("preview", {"edits": [{"operation": "replace_symbol", "file": "demo.py",
                        "version": r["version"], "symbol": "answer", "code": "def answer():\n    return 42"}]})
                    self.assertEqual(source.read_text(), "def answer():\n    return 1\n")
                    result = await call("commit_edit", {"plan_id": p["plan_id"]})
                    self.assertIn("42", source.read_text())
                    undo = await call("undo_edit", {"undo_id": result["undo_id"]})
                    await call("commit_edit", {"plan_id": undo["plan_id"]})
                    self.assertEqual(source.read_text(), "def answer():\n    return 1\n")
                    error = await call("read_code", {"file": "missing.py"})
                    self.assertEqual(error["error"]["code"], "missing_file")
