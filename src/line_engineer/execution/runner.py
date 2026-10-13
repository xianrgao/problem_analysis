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
    """Default executor: one child process per execution, ordinary process isolation.

    This is not a hardened sandbox. A company isolation environment can replace
    it via settings ``executor.factory`` ("module:callable" receiving
    ``(settings, variables)``) as long as it provides ``execute``,
    ``execute_shell`` and ``kill`` with the same contracts. ``scripts`` lists
    the calling skill's frozen scripts directories, importable by the code.
    """

    def __init__(self, settings, variables):
        self.settings, self.variables = settings, variables
        self.processes = {}

    def interpreter(self):
        return (self.settings.values.get("executor") or {}).get("python") or sys.executable

    def environment(self):
        env = os.environ.copy()
        # Generated code receives data snapshots, not application credentials.
        env.pop("LINE_ENGINEER_API_KEY", None)
        env["PYTHONPATH"] = str(self.settings.root / "src")
        env["PYTHONIOENCODING"] = "utf-8"
        return env

    def workspace(self, session_id, prefix):
        directory = self.settings.data_dir / "workspaces" / session_id / uid(prefix)
        directory.mkdir(parents=True)
        return directory

    async def _communicate(self, session_id, process):
        self.processes[session_id] = process
        limit = self.settings.values["execution_timeout"]
        try:
            return await asyncio.wait_for(process.communicate(), timeout=limit)
        except asyncio.TimeoutError:
            self.kill(session_id)
            await process.wait()
            raise RuntimeError(f"执行超过 {limit} 秒时限，已终止进程及子进程，未发布部分结果") from None
        except asyncio.CancelledError:
            self.kill(session_id)
            await process.wait()
            raise
        finally:
            self.processes.pop(session_id, None)

    async def execute_shell(self, session_id, shell, executable, script):
        directory = self.workspace(session_id, "shell_")
        if shell == "powershell":
            path = directory / "script.ps1"
            # BOM keeps Windows PowerShell 5.1 reading Chinese text as UTF-8; output is forced to UTF-8 as well.
            preamble = "try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch { }\n$OutputEncoding = [System.Text.Encoding]::UTF8\n"
            path.write_text(preamble + script, encoding="utf-8-sig")
            command = [executable, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(path)]
        elif shell == "bash":
            path = directory / "script.sh"
            path.write_text(script, encoding="utf-8", newline="\n")
            command = [executable, str(path)]
        else:
            raise ValueError(f"不支持的 shell：{shell}")
        process = await asyncio.create_subprocess_exec(*command, cwd=directory, env=self.environment(),
                                                       stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await self._communicate(session_id, process)
        stdout, stderr = out.decode("utf-8", errors="replace")[-20000:], err.decode("utf-8", errors="replace")[-10000:]
        if process.returncode != 0:
            raise RuntimeError(f"{shell} 脚本退出码 {process.returncode}\n{stderr or stdout}")
        return {"stdout": stdout, "stderr": stderr, "exit_code": process.returncode, "shell": shell}

    async def execute(self, session_id, code, inputs, producer=None, scripts=None):
        directory = self.workspace(session_id, "exec_")
        code_path = directory / "analysis.py"
        code_path.write_text(code, encoding="utf-8")
        response_path = directory / "response.json"
        request = {"inputs": inputs, "output_dir": str(directory / "outputs"), "code_path": str(code_path), "response_path": str(response_path),
                   "scripts": list(scripts or [])}
        request_path = directory / "request.json"
        request_path.write_text(json.dumps(request, ensure_ascii=False), encoding="utf-8")
        process = await asyncio.create_subprocess_exec(self.interpreter(), "-m", "line_engineer.execution.worker", str(request_path),
            cwd=directory, env=self.environment(), stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await self._communicate(session_id, process)
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
