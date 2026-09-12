"""JSON on stdin avoids shell escaping of code. stdout contains only results."""
import argparse
import json
import sys
from .engine import Editor, EditError


def dispatch(editor, request):
    try:
        if not isinstance(request, dict):
            raise EditError("invalid_request", "Request must be a JSON object.")
        request = dict(request)
        operation = request.pop("tool")
        allowed = {"read_code", "read_diff", "preview", "rename_symbol", "commit_edit", "undo_edit",
                   "list_files", "search_code", "list_previews", "discard_preview",
                   "list_transactions", "recover_transaction", "validate"}
        if operation not in allowed:
            raise EditError("unknown_tool", "Choose a supported tool.", tools=sorted(allowed))
        return getattr(editor, operation)(**request)
    except EditError as e:
        return e.result()
    except (KeyError, TypeError, ValueError, AttributeError) as e:
        return EditError("invalid_request", str(e)).result()
    except SyntaxError as e:
        return EditError("syntax_error", e.msg, line=e.lineno).result()
    except OSError as e:
        return EditError("io_error", str(e)).result()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Project root; all tool paths are relative to it")
    parser.add_argument("--mcp", action="store_true", help="Run an MCP stdio server")
    args = parser.parse_args()
    if args.mcp:
        from .server import serve
        serve(args.root)
        return
    try:
        result = dispatch(Editor(args.root), json.load(sys.stdin))
    except (ValueError, OSError, EditError) as e:
        result = e.result() if isinstance(e, EditError) else EditError("invalid_request", str(e)).result()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["ok"] else 1)
