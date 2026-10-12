from __future__ import annotations
import asyncio
import json
import os
import sys
from pathlib import Path
import pandas as pd
import psutil
from ..storage.database import uid


class ProcessRunner:
    def __init__(self, settings, variables):
        self.settings, self.variables = settings, variables
        self.processes = {}

    async def execute(self, session_id, code, inputs, producer=None):
        directory = self.settings.data_dir / "workspaces" / session_id / uid("exec_")
        directory.mkdir(parents=True)
        code_path = directory / "analysis.py"
        code_path.write_text(code, encoding="utf-8")
        response_path = directory / "response.json"
        request = {"inputs": inputs, "output_dir": str(directory / "outputs"), "code_path": str(code_path), "response_path": str(response_path)}
        request_path = directory / "request.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        env = os.environ.copy()
        # Generated code receives data snapshots, not application credentials.
        env.pop("LINE_ENGINEER_API_KEY", None)
        env["PYTHONPATH"] = str(self.settings.root / "src")
        env["PYTHONIOENCODING"] = "utf-8"
        process = await asyncio.create_subprocess_exec(sys.executable, "-m", "line_engineer.execution.worker", str(request_path),
            cwd=directory, env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        self.processes[session_id] = process
        try:
            out, err = await asyncio.wait_for(process.communicate(), timeout=self.settings.values["execution_timeout"])
        except (asyncio.TimeoutError, asyncio.CancelledError):
            self.kill(session_id)
            await process.wait()
            raise
        finally:
            self.processes.pop(session_id, None)
        logs = out.decode("utf-8", errors="replace")[-20000:] + err.decode("utf-8", errors="replace")[-10000:]
        if not response_path.exists():
            raise RuntimeError(f"执行进程没有返回结果: {logs}")
        response = json.loads(response_path.read_text(encoding="utf-8"))
        if not response["ok"]:
            raise RuntimeError(response.get("error", "代码执行失败") + "\n" + logs)
        # Validate all staged results before committing any; each variable+preview commit is atomic.
        staged = []
        for output in response["outputs"]:
            path = Path(output["path"]).resolve()
            if not path.is_relative_to((directory / "outputs").resolve()):
                raise ValueError("输出路径超出本次执行目录")
            value = pd.read_parquet(path) if output["kind"] == "table" else json.loads(path.read_text(encoding="utf-8"))
            staged.append((output, value))
        outputs = [self.variables.save(session_id, item["name"], value, item["description"], item["kind"], item["metadata"], producer, response["reads"]) for item, value in staged]
        return {"outputs": [self.variables.public(m) for m in outputs], "stdout": logs, "reads": response["reads"]}

    def kill(self, session_id):
        process = self.processes.get(session_id)
        if not process:
            return
        try:
            parent = psutil.Process(process.pid)
            for child in parent.children(recursive=True):
                child.kill()
            parent.kill()
        except psutil.NoSuchProcess:
            pass
