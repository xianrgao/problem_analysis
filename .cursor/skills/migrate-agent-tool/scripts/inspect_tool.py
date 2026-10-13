"""Static inspection of an existing Python tool before wrapping it in an adapter.

Usage:
    python inspect_tool.py path/to/tool.py [--json]

Reports entry points, imports, environment variable names read, logging,
raise and return sites. Values of environment variables are never read.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

LOGGING = {"print", "debug", "info", "warning", "warn", "error", "exception", "critical", "log"}


def _name(node) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name(node.value)}.{node.attr}"
    if isinstance(node, ast.Call):
        return _name(node.func)
    return type(node).__name__


def _literal(node):
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def inspect_source(source: str, filename: str = "<tool>") -> dict:
    tree = ast.parse(source, filename)
    report = {"file": filename, "module_doc": ast.get_docstring(tree), "entry_points": [], "imports": [], "env_vars": [],
              "credential_hints": [], "logging": [], "raises": [], "returns": [], "has_main": False, "reads_stdin": False}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            report["imports"] += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            report["imports"].append(node.module or ".")
        elif isinstance(node, ast.Call):
            func = _name(node.func)
            if func in ("os.getenv", "os.environ.get", "getenv") and node.args and _literal(node.args[0]):
                report["env_vars"].append(_literal(node.args[0]))
            if func.split(".")[-1] in LOGGING and (func == "print" or "log" in func.lower()):
                report["logging"].append({"line": node.lineno, "call": func})
            if func in ("sys.stdin.read", "input", "json.load") or func.startswith("sys.stdin"):
                report["reads_stdin"] = True
        elif isinstance(node, ast.Subscript) and _name(node.value) == "os.environ" and _literal(node.slice):
            report["env_vars"].append(_literal(node.slice))
        elif isinstance(node, ast.Raise):
            report["raises"].append({"line": node.lineno, "exception": _name(node.exc) if node.exc else "re-raise"})
        elif isinstance(node, ast.If) and isinstance(node.test, ast.Compare) and _name(node.test.left) == "__name__":
            report["has_main"] = True
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and not node.name.startswith("_"):
            report["entry_points"].append(_function(node))
        elif isinstance(node, ast.ClassDef):
            methods = [_function(item, node.name) for item in node.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and not item.name.startswith("_")]
            report["entry_points"] += methods
    for function in [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
        for node in ast.walk(function):
            if isinstance(node, ast.Return) and node.value is not None:
                report["returns"].append({"function": function.name, "line": node.lineno, "expression": ast.unparse(node.value)[:120]})
    report["imports"] = sorted(set(report["imports"]))
    report["env_vars"] = sorted(set(report["env_vars"]))
    keywords = ("token", "password", "secret", "key", "auth", "credential", "cookie")
    report["credential_hints"] = [v for v in report["env_vars"] if any(k in v.lower() for k in keywords)]
    return report


def _function(node, owner=None) -> dict:
    return {"name": f"{owner}.{node.name}" if owner else node.name, "line": node.lineno, "async": isinstance(node, ast.AsyncFunctionDef),
            "signature": f"({ast.unparse(node.args)})", "doc": (ast.get_docstring(node) or "").split("\n")[0]}


def render(report: dict) -> str:
    lines = [f"文件：{report['file']}", f"模块说明：{(report['module_doc'] or '（无）').splitlines()[0]}", "", "入口："]
    lines += [f"  - {e['name']}{e['signature']}  第 {e['line']} 行{'（async）' if e['async'] else ''}  {e['doc']}" for e in report["entry_points"]] or ["  （无公开函数）"]
    lines += ["", f"脚本入口 __main__：{'有' if report['has_main'] else '无'}；读取 stdin：{'是' if report['reads_stdin'] else '否'}",
              "", "依赖导入：" + ("、".join(report["imports"]) or "（无）"),
              "读取的环境变量（仅名称）：" + ("、".join(report["env_vars"]) or "（无）"),
              "可能的凭据来源：" + ("、".join(report["credential_hints"]) or "（未发现，需人工确认配置文件或凭据管理器）"),
              "", f"日志／打印：{len(report['logging'])} 处" + (f"（首处第 {report['logging'][0]['line']} 行 {report['logging'][0]['call']}）" if report["logging"] else ""),
              "异常：" + ("、".join(f"{r['exception']}@{r['line']}" for r in report["raises"]) or "（无显式 raise）"), "", "返回值："]
    lines += [f"  - {r['function']} 第 {r['line']} 行：{r['expression']}" for r in report["returns"]] or ["  （无显式返回值）"]
    lines += ["", "下一步：对照 references/inspection-checklist.md 确认鉴权、副作用与输出列，再选择 python 或 script 驱动。"]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="静态检查已有 Python 工具")
    parser.add_argument("path", type=Path)
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    args = parser.parse_args(argv)
    report = inspect_source(args.path.read_text(encoding="utf-8-sig"), str(args.path))
    print(json.dumps(report, ensure_ascii=False, indent=2) if args.json else render(report))
    return 0


if __name__ == "__main__":
    sys.exit(main())
