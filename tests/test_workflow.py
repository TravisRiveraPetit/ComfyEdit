"""File lifecycle and crash recovery against real temporary project trees."""
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from comfyedit import Editor, EditError


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.editor = Editor(self.root)
        (self.root / "a.py").write_text("value = 1\n")
        (self.root / "a.py").chmod(0o755)

    def edit(self, operation, file="a.py", **fields):
        return dict(operation=operation, file=file,
                    version=self.editor.read_code(file)["version"], **fields)

    def assert_error(self, code, function, *args, **kwargs):
        with self.assertRaises(EditError) as caught:
            function(*args, **kwargs)
        self.assertEqual(caught.exception.code, code)
        return caught.exception

    def plan(self):
        return self.editor.preview([
            self.edit("replace_text", old="1", new="2"),
            dict(operation="create_file", file="new/pkg/b.py", code="value = 3\n"),
        ])

    def test_create_move_delete_and_undo_redo(self):
        plan = self.editor.preview([
            self.edit("move_file", destination="src/moved.py"),
            dict(operation="replace_text", file="src/moved.py", version=None, old="1", new="2"),
            dict(operation="create_file", file="tests/test_new.py", code="assert True\n"),
        ])
        self.assertFalse((self.root / "src").exists())
        self.assertIn("/dev/null", plan["diff"])
        result = self.editor.commit_edit(plan["plan_id"])
        self.assertFalse((self.root / "a.py").exists())
        self.assertEqual((self.root / "src/moved.py").read_text(), "value = 2\n")
        self.assertEqual((self.root / "src/moved.py").stat().st_mode & 0o777, 0o755)
        self.assertEqual((self.root / "tests/test_new.py").stat().st_mode & 0o777, 0o644)
        self.assertIsNone(next(f["version"] for f in result["files"] if f["file"] == "a.py"))
        reverse = self.editor.undo_edit(result["undo_id"])
        undone = self.editor.commit_edit(reverse["plan_id"])
        self.assertEqual((self.root / "a.py").read_text(), "value = 1\n")
        self.assertEqual((self.root / "a.py").stat().st_mode & 0o777, 0o755)
        self.assertFalse((self.root / "src/moved.py").exists())
        self.assertFalse((self.root / "tests/test_new.py").exists())
        redo = self.editor.undo_edit(undone["undo_id"])
        self.editor.commit_edit(redo["plan_id"])
        self.assertEqual((self.root / "src/moved.py").read_text(), "value = 2\n")

    def test_previews_are_discoverable_and_unapplied_previews_can_be_discarded(self):
        plan = self.editor.preview([self.edit("replace_text", old="1", new="2")])
        listed = self.editor.list_previews()
        self.assertEqual(listed["previews"][0]["plan_id"], plan["plan_id"])
        self.assertEqual(listed["previews"][0]["status"], "preview")
        discarded = self.editor.discard_preview(plan["plan_id"])
        self.assertEqual(discarded["status"], "discarded")
        self.assertEqual(self.editor.list_previews()["previews"], [])
        with self.assertRaises(EditError) as caught:
            self.editor.read_diff(plan["plan_id"])
        self.assertEqual(caught.exception.code, "unknown_id")

    def test_applied_preview_stays_listed_and_cannot_be_discarded(self):
        plan = self.editor.preview([self.edit("replace_text", old="1", new="2")])
        result = self.editor.commit_edit(plan["plan_id"])
        listed = self.editor.list_previews(include_applied=True)
        self.assertEqual(listed["previews"][0]["status"], "applied")
        with self.assertRaises(EditError) as caught:
            self.editor.discard_preview(plan["plan_id"])
        self.assertEqual(caught.exception.code, "preview_in_use")
        self.assertEqual(self.editor.undo_edit(result["undo_id"])["ok"], True)

    def test_delete_and_restore_executable(self):
        plan = self.editor.preview([self.edit("delete_file")])
        result = self.editor.commit_edit(plan["plan_id"])
        self.assertFalse((self.root / "a.py").exists())
        reverse = self.editor.undo_edit(result["undo_id"])
        self.editor.commit_edit(reverse["plan_id"])
        self.assertEqual((self.root / "a.py").stat().st_mode & 0o777, 0o755)

    def test_empty_creation_is_reviewable(self):
        plan = self.editor.preview([dict(operation="create_file", file="empty.txt", code="")])
        self.assertIn("empty.txt", plan["diff"])
        self.assertEqual(plan["changes"][0]["operation"], "create")
        result = self.editor.commit_edit(plan["plan_id"])
        self.assertEqual((self.root / "empty.txt").read_bytes(), b"")
        reverse = self.editor.undo_edit(result["undo_id"])
        self.assertIn("empty.txt", reverse["diff"])
        self.editor.commit_edit(reverse["plan_id"])
        self.assertFalse((self.root / "empty.txt").exists())

    def test_mode_only_change_has_a_diff_and_can_be_undone(self):
        plan = self.editor.preview([self.edit("delete_file"),
            dict(operation="create_file", file="a.py", code="value = 1\n", mode=0o644)])
        self.assertIn("old mode 100755\nnew mode 100644", plan["diff"])
        result = self.editor.commit_edit(plan["plan_id"])
        self.assertEqual((self.root / "a.py").stat().st_mode & 0o777, 0o644)
        reverse = self.editor.undo_edit(result["undo_id"])
        self.editor.commit_edit(reverse["plan_id"])
        self.assertEqual((self.root / "a.py").stat().st_mode & 0o777, 0o755)

    def test_create_and_edit_share_original_absence_version(self):
        plan = self.editor.preview([
            dict(operation="create_file", file="new.py", code="value = 1\n"),
            dict(operation="replace_text", file="new.py", version=None, old="1", new="2"),
        ])
        self.editor.commit_edit(plan["plan_id"])
        self.assertEqual((self.root / "new.py").read_text(), "value = 2\n")

    def test_replace_range_handles_unicode_and_multiline_boundaries(self):
        path = self.root / "notes.txt"
        path.write_bytes("😀 first\r\nsecond line\r\nthird".encode())
        version = self.editor.read_code("notes.txt")["version"]
        plan = self.editor.preview([dict(
            operation="replace_range", file="notes.txt", version=version,
            start_line=1, start_column=3, end_line=2, end_column=7,
            code="new\r\nrow",
        )])
        self.assertIn("new", plan["diff"])
        self.editor.commit_edit(plan["plan_id"])
        self.assertEqual(path.read_bytes(), "😀 new\r\nrow line\r\nthird".encode())

    def test_replace_range_rejects_invalid_coordinates(self):
        version = self.editor.read_code("a.py")["version"]
        base = dict(operation="replace_range", file="a.py", version=version,
                    start_line=1, start_column=1, end_line=1, end_column=2, code="x")
        for key, value in (("start_line", 0), ("start_column", 99), ("end_line", 0)):
            edit = dict(base, **{key: value})
            self.assert_error("invalid_range", self.editor.preview, [edit])
        self.assert_error("invalid_range", self.editor.preview,
                          [dict(base, start_line=1, start_column=3, end_line=1, end_column=2)])

        crlf = self.root / "crlf.txt"
        crlf.write_bytes(b"a\r\nb\r\n")
        crlf_version = self.editor.read_code("crlf.txt")["version"]
        self.assert_error("invalid_range", self.editor.preview, [dict(
            operation="insert_at", file="crlf.txt", version=crlf_version,
            line=1, column=3, code="broken")])
        self.assert_error("invalid_range", self.editor.preview, [dict(
            operation="replace_range", file="crlf.txt", version=crlf_version,
            start_line=1, start_column=2, end_line=1, end_column=3, code="broken")])

    def test_insert_at_supports_empty_files_eof_and_crlf(self):
        empty = self.root / "empty.txt"
        empty.write_bytes(b"")
        empty_version = self.editor.read_code("empty.txt")["version"]
        plan = self.editor.preview([dict(operation="insert_at", file="empty.txt", version=empty_version,
                                          line=1, column=1, code="first\nsecond")])
        self.editor.commit_edit(plan["plan_id"])
        self.assertEqual(empty.read_text(), "first\nsecond")

        path = self.root / "lines.txt"
        path.write_bytes(b"a\r\nb\r\n")
        version = self.editor.read_code("lines.txt")["version"]
        plan = self.editor.preview([dict(operation="insert_at", file="lines.txt", version=version,
                                          line=3, column=1, code="c\nd")])
        self.editor.commit_edit(plan["plan_id"])
        self.assertEqual(path.read_bytes(), b"a\r\nb\r\nc\r\nd")

    def test_insert_at_batch_coordinates_use_evolving_source_and_stale_guard(self):
        version = self.editor.read_code("a.py")["version"]
        plan = self.editor.preview([
            dict(operation="insert_at", file="a.py", version=version, line=1, column=1, code="# "),
            dict(operation="insert_at", file="a.py", version=version, line=1, column=3, code="new "),
        ])
        self.editor.commit_edit(plan["plan_id"])
        self.assertEqual((self.root / "a.py").read_text(), "# new value = 1\n")

        current = self.editor.read_code("a.py")["version"]
        pending = self.editor.preview([dict(operation="insert_at", file="a.py", version=current,
                                             line=1, column=1, code="stale ")])
        (self.root / "a.py").write_text("value = 9\n")
        self.assert_error("stale_version", self.editor.commit_edit, pending["plan_id"])

    def test_create_then_delete_is_no_change(self):
        plan = self.editor.preview([dict(operation="create_file", file="new.py", code="x = 1\n"),
                                   dict(operation="delete_file", file="new.py", version=None)])
        self.assertEqual(plan["status"], "no_change")
        self.assertFalse((self.root / "new.py").exists())

    def test_destinations_never_overwritten(self):
        self.assert_error("file_exists", self.editor.preview,
                          [dict(operation="create_file", file="a.py", code="")])
        (self.root / "b.py").write_text("external = 7\n")
        self.assert_error("file_exists", self.editor.preview, [self.edit("move_file", destination="b.py")])
        self.assertEqual((self.root / "b.py").read_text(), "external = 7\n")
        plan = self.editor.preview([self.edit("move_file", destination="c.py")])
        (self.root / "c.py").write_text("external = 8\n")
        self.assert_error("stale_version", self.editor.commit_edit, plan["plan_id"])
        self.assertTrue((self.root / "a.py").exists())
        self.assertEqual((self.root / "c.py").read_text(), "external = 8\n")

    def test_undo_does_not_overwrite_recreated_deleted_file(self):
        plan = self.editor.preview([self.edit("delete_file")])
        result = self.editor.commit_edit(plan["plan_id"])
        (self.root / "a.py").write_text("external = 3\n")
        self.assert_error("undo_conflict", self.editor.undo_edit, result["undo_id"])

    def test_undo_restores_original_even_if_it_had_a_syntax_error(self):
        (self.root / "a.py").write_text("value = (\n")
        plan = self.editor.preview([self.edit("replace_text", old="value = (", new="value = 1")])
        applied = self.editor.commit_edit(plan["plan_id"])
        reverse = self.editor.undo_edit(applied["undo_id"])
        self.editor.commit_edit(reverse["plan_id"])
        self.assertEqual((self.root / "a.py").read_text(), "value = (\n")

    def test_modes_guard_commit_and_undo(self):
        plan = self.plan()
        (self.root / "a.py").chmod(0o644)
        self.assert_error("stale_version", self.editor.commit_edit, plan["plan_id"])
        (self.root / "a.py").chmod(0o755)
        result = self.editor.commit_edit(plan["plan_id"])
        (self.root / "a.py").chmod(0o644)
        self.assert_error("undo_conflict", self.editor.undo_edit, result["undo_id"])

    def test_invalid_python_and_paths_never_reach_disk(self):
        self.assert_error("syntax_error", self.editor.preview,
                          [dict(operation="create_file", file="new.py", code="def (")])
        for destination in ("../outside.py", ".git/config", ".comfyedit/other", "/tmp/outside.py"):
            self.assert_error("unsafe_path", self.editor.preview, [self.edit("move_file", destination=destination)])
        (self.root / "link").symlink_to(self.root, target_is_directory=True)
        self.assert_error("unsafe_path", self.editor.preview,
                          [dict(operation="create_file", file="link/new.py", code="")])
        self.assert_error("path_conflict", self.editor.preview,
                          [dict(operation="create_file", file="a.py/new.py", code="")])
        self.assert_error("path_conflict", self.editor.preview,
                          [dict(operation="create_file", file="new", code=""),
                           dict(operation="create_file", file="new/file", code="")])

    def test_fifo_rejected_without_blocking(self):
        os.mkfifo(self.root / "fifo")
        self.assert_error("not_file", self.editor.preview, [dict(operation="create_file", file="fifo", code="")])

    def interrupt(self):
        plan = self.plan()
        real = self.editor.write_snapshot
        def interrupted(file, content, mode):
            real(file, content, mode)
            if file == "a.py":
                raise SystemExit("simulated termination")
        with patch.object(self.editor, "write_snapshot", side_effect=interrupted):
            with self.assertRaises(SystemExit):
                self.editor.commit_edit(plan["plan_id"])
        pending = Editor(self.root).list_transactions()["transactions"]
        self.assertEqual(len(pending), 1)
        self.assertEqual([f["state"] for f in pending[0]["files"]], ["after", "before"])
        return plan, pending[0]["transaction_id"]

    def test_pending_transaction_blocks_other_commits(self):
        plan, identifier = self.interrupt()
        error = self.assert_error("recovery_required", self.editor.commit_edit, plan["plan_id"])
        self.assertEqual(error.details["transaction_ids"], [identifier])

    def test_recover_finish_then_undo(self):
        plan, identifier = self.interrupt()
        result = Editor(self.root).recover_transaction(identifier, "finish")
        self.assertEqual(result["undo_id"], identifier)
        self.assertEqual((self.root / "new/pkg/b.py").read_text(), "value = 3\n")
        self.assertEqual(self.editor.list_transactions()["transactions"], [])
        self.assert_error("used_plan", self.editor.commit_edit, plan["plan_id"])
        reverse = self.editor.undo_edit(identifier)
        self.editor.commit_edit(reverse["plan_id"])
        self.assertEqual((self.root / "a.py").read_text(), "value = 1\n")
        self.assertFalse((self.root / "new/pkg/b.py").exists())

    def test_recover_rollback_then_retry(self):
        plan, identifier = self.interrupt()
        Editor(self.root).recover_transaction(identifier, "rollback")
        self.assertEqual((self.root / "a.py").read_text(), "value = 1\n")
        self.editor.commit_edit(plan["plan_id"])
        self.assertTrue((self.root / "new/pkg/b.py").exists())

    def test_recovery_refuses_external_edits_before_any_writes(self):
        _, identifier = self.interrupt()
        (self.root / "new").mkdir()
        (self.root / "new/pkg").mkdir()
        (self.root / "new/pkg/b.py").write_text("external = 9\n")
        for action in ("rollback", "finish"):
            self.assert_error("recovery_conflict", self.editor.recover_transaction, identifier, action)
            self.assertEqual((self.root / "a.py").read_text(), "value = 2\n")
            self.assertEqual((self.root / "new/pkg/b.py").read_text(), "external = 9\n")

    def test_write_failure_removes_created_files_and_empty_parents(self):
        plan = self.editor.preview([
            dict(operation="create_file", file="new/pkg/file.py", code="x = 1\n"),
            self.edit("delete_file"),
        ])
        real = self.editor.write_snapshot
        def fail(file, content, mode):
            if file == "a.py" and content is None:
                raise OSError("injected deletion failure")
            real(file, content, mode)
        with patch.object(self.editor, "write_snapshot", side_effect=fail):
            with self.assertRaises(OSError):
                self.editor.commit_edit(plan["plan_id"])
        self.assertFalse((self.root / "new").exists())
        self.assertEqual((self.root / "a.py").read_text(), "value = 1\n")
        self.assertEqual(self.editor.list_transactions()["transactions"], [])

    def test_failed_rollback_leaves_recoverable_journal(self):
        plan = self.plan()
        real = self.editor.write_snapshot
        def fail(file, content, mode):
            if file != "a.py" or content == "value = 1\n":
                raise OSError("injected disk failure")
            real(file, content, mode)
        with patch.object(self.editor, "write_snapshot", side_effect=fail):
            error = self.assert_error("recovery_required", self.editor.commit_edit, plan["plan_id"])
        identifier = error.details["transaction_ids"][0]
        self.editor.recover_transaction(identifier, "rollback")
        self.assertEqual((self.root / "a.py").read_text(), "value = 1\n")

    def test_final_journal_failure_is_recoverable(self):
        plan = self.plan()
        real = self.editor.save
        def fail(state, identifier, payload):
            if payload["kind"] == "undo":
                raise OSError("injected finalization failure")
            real(state, identifier, payload)
        with patch.object(self.editor, "save", side_effect=fail):
            error = self.assert_error("recovery_required", self.editor.commit_edit, plan["plan_id"])
        identifier = error.details["transaction_ids"][0]
        self.editor.recover_transaction(identifier, "finish")
        self.assert_error("used_plan", self.editor.commit_edit, plan["plan_id"])
        self.assertEqual(self.editor.list_transactions()["transactions"], [])

    def test_concurrent_creations_have_one_winner(self):
        plans = [self.editor.preview([dict(operation="create_file", file="winner.txt", code=value)])
                 for value in ("first", "second")]
        processes = [subprocess.Popen([sys.executable, "-m", "comfyedit", "--root", str(self.root)],
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                    for _ in plans]
        for process, plan in zip(processes, plans):
            process.stdin.write(json.dumps(dict(tool="commit_edit", plan_id=plan["plan_id"])))
            process.stdin.close()
            process.stdin = None
        results = [json.loads(process.communicate(timeout=10)[0]) for process in processes]
        self.assertEqual(sum(result["ok"] for result in results), 1)
        loser = next(result for result in results if not result["ok"])
        self.assertEqual(loser["error"]["code"], "stale_version")
        self.assertIn((self.root / "winner.txt").read_text(), {"first", "second"})

    def test_interrupted_move_restores_or_finishes_executable(self):
        for action in ("rollback", "finish"):
            with self.subTest(action=action):
                plan = self.editor.preview([self.edit("move_file", destination="moved.py")])
                real = self.editor.write_snapshot
                def interrupted(file, content, mode):
                    real(file, content, mode)
                    if file == "a.py":
                        raise SystemExit()
                with patch.object(self.editor, "write_snapshot", side_effect=interrupted):
                    with self.assertRaises(SystemExit):
                        self.editor.commit_edit(plan["plan_id"])
                identifier = self.editor.list_transactions()["transactions"][0]["transaction_id"]
                self.editor.recover_transaction(identifier, action)
                restored = self.root / ("a.py" if action == "rollback" else "moved.py")
                self.assertEqual(restored.read_text(), "value = 1\n")
                self.assertEqual(restored.stat().st_mode & 0o777, 0o755)

    @unittest.skipUnless(importlib.util.find_spec("rope"), "Install .[python] for rename tests")
    def test_finish_recovery_keeps_rename_inventory_guard(self):
        (self.root / "a.py").write_text("def answer(): return 1\n")
        plan = self.editor.rename_symbol("a.py", "answer", "result", self.editor.read_code("a.py")["version"])
        with patch.object(self.editor, "write_snapshot", side_effect=SystemExit):
            with self.assertRaises(SystemExit):
                self.editor.commit_edit(plan["plan_id"])
        (self.root / "caller.py").write_text("from a import answer\n")
        identifier = self.editor.list_transactions()["transactions"][0]["transaction_id"]
        self.assert_error("recovery_conflict", self.editor.recover_transaction, identifier, "finish")
        self.editor.recover_transaction(identifier, "rollback")
        self.assertEqual((self.root / "a.py").read_text(), "def answer(): return 1\n")

    def test_legacy_preview_can_commit_and_undo(self):
        read = self.editor.read_code("a.py")
        identifier = "a" * 32
        with self.editor.lock() as state:
            self.editor.save(state, identifier, dict(kind="plan", before={"a.py": read["text"]},
                after={"a.py": "value = 2\n"}, guards={"a.py": read["version"]}))
        applied = self.editor.commit_edit(identifier)
        reverse = self.editor.undo_edit(applied["undo_id"])
        self.editor.commit_edit(reverse["plan_id"])
        self.assertEqual((self.root / "a.py").read_text(), "value = 1\n")
        self.assertEqual((self.root / "a.py").stat().st_mode & 0o777, 0o755)

    def test_actual_process_exit_recovered_by_new_process(self):
        plan = self.plan()
        code = '''
import os, sys
from comfyedit import Editor
editor = Editor(sys.argv[1])
real = editor.write_snapshot
def crash(file, content, mode):
    real(file, content, mode)
    os._exit(73)
editor.write_snapshot = crash
editor.commit_edit(sys.argv[2])
'''
        process = subprocess.run([sys.executable, "-c", code, str(self.root), plan["plan_id"]], capture_output=True)
        self.assertEqual(process.returncode, 73, process.stderr)
        identifier = self.editor.list_transactions()["transactions"][0]["transaction_id"]
        request = dict(tool="recover_transaction", transaction_id=identifier, action="finish")
        recovered = subprocess.run([sys.executable, "-m", "comfyedit", "--root", str(self.root)],
                                   input=json.dumps(request), capture_output=True, text=True)
        self.assertEqual(recovered.returncode, 0, recovered.stderr + recovered.stdout)
        self.assertTrue(json.loads(recovered.stdout)["ok"])
        self.assertEqual((self.root / "new/pkg/b.py").read_text(), "value = 3\n")


if __name__ == "__main__":
    unittest.main()
