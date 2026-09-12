"""Run a complete ComfyEdit workflow in a disposable project, without an LLM."""
import json
from pathlib import Path
import tempfile
import time

from comfyedit import Editor
from comfyedit.cli import dispatch


def main():
    with tempfile.TemporaryDirectory(prefix="comfyedit-example-") as directory:
        root = Path(directory)
        (root / "solver.py").write_text("def answer():\n    return 1\n")
        editor = Editor(root)
        calls, input_chars, output_chars = 0, 0, 0
        started = time.perf_counter()

        def call(tool, **args):
            nonlocal calls, input_chars, output_chars
            request = dict(tool=tool, **args)
            result = dispatch(editor, request)
            calls += 1
            input_chars += len(json.dumps(request, ensure_ascii=False))
            output_chars += len(json.dumps(result, ensure_ascii=False))
            if not result["ok"]:
                raise RuntimeError(result)
            return result

        match = call("search_code", query="return 1", pattern="*.py")["matches"][0]
        preview = call("preview", edits=[
            dict(operation="replace_text", file=match["file"], version=match["version"], old="return 1", new="return 42"),
            dict(operation="create_file", file="tests/test_solver.py",
                 code="from solver import answer\n\ndef test_answer():\n    assert answer() == 42\n"),
        ])
        print(preview["diff"])
        cursor = preview["next_offset"]
        while cursor is not None:
            page = call("read_diff", plan_id=preview["plan_id"], offset=cursor)
            print(page["diff"], end="")
            cursor = page["next_offset"]
        applied = call("commit_edit", plan_id=preview["plan_id"])
        assert (root / "solver.py").read_text() == "def answer():\n    return 42\n"
        assert (root / "tests/test_solver.py").is_file()
        reverse = call("undo_edit", undo_id=applied["undo_id"])
        call("commit_edit", plan_id=reverse["plan_id"])
        assert (root / "solver.py").read_text() == "def answer():\n    return 1\n"
        assert not (root / "tests/test_solver.py").exists()
        print(json.dumps(dict(ok=True, calls=calls, input_chars=input_chars, output_chars=output_chars,
                              elapsed_seconds=round(time.perf_counter() - started, 4)), indent=2))
        # These are observed workflow counts, not tokenizer counts or a comparative benchmark.


if __name__ == "__main__":
    main()
