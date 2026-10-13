from __future__ import annotations
import json
import sys
import traceback
from pathlib import Path
from .sdk import ExecutionContext


def main():
    request = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    ctx = ExecutionContext(request["inputs"], request["output_dir"])
    # Appended so skill scripts never shadow standard or installed modules.
    sys.path.extend(request.get("scripts") or [])
    response = {"ok": False, "outputs": [], "reads": []}
    try:
        code = Path(request["code_path"]).read_text(encoding="utf-8")
        exec(compile(code, request["code_path"], "exec"), {"ctx": ctx, "__name__": "__skill__"})
        response.update(ok=True, outputs=ctx.vars.outputs, reads=ctx.vars.reads)
    except BaseException:
        response["error"] = traceback.format_exc()
    Path(request["response_path"]).write_text(json.dumps(response, ensure_ascii=False), encoding="utf-8")
    return 0 if response["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
