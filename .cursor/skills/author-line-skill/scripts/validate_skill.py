"""Validate business skills under skills/: metadata, references, tool dependencies and variable outputs.

Usage:
    python validate_skill.py skills/<name> [--strict]
    python validate_skill.py --all skills [--strict]

Exit code 1 when any error is found (or any warning with --strict).
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path

import yaml

NAME = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
VERSION = re.compile(r"^\d+\.\d+\.\d+$")
VARIABLE = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]{0,99}$")
RESERVED = {"skills", "host_capabilities", "active_plan", "variable_index"}
KINDS = {"table", "json", "text", "chart", "report", "sql", "code"}
LINK = re.compile(r"\]\(([^)#\s]+)(?:#[^)]*)?\)")
INLINE_PATH = re.compile(r"`((?:references|scripts)/[^`\s]+)`")
PENDING = re.compile(r"<待查证[：:]")


def project_root(start: Path) -> Path:
    for candidate in [start, *start.parents]:
        if (candidate / "src" / "line_engineer").is_dir():
            return candidate
    raise SystemExit("未找到 problem_analysis 项目根目录（需要 src/line_engineer）")


def registered_tools(root: Path) -> set[str]:
    sys.path.insert(0, str(root / "src"))
    from line_engineer.orchestration.host import TOOLS
    return set(TOOLS)


def validate(directory: Path, tools: set[str]) -> tuple[list[str], list[str]]:
    errors, warnings = [], []
    skill_file = directory / "SKILL.md"
    if not skill_file.is_file():
        return [f"缺少 SKILL.md"], []
    raw = skill_file.read_text(encoding="utf-8-sig")
    parts = raw.split("---", 2)
    if not raw.startswith("---") or len(parts) < 3:
        return ["SKILL.md 缺少 YAML 元数据"], []
    try:
        front = yaml.safe_load(parts[1]) or {}
    except yaml.YAMLError as exc:
        return [f"元数据不是有效 YAML：{exc}"], []
    body = parts[2].strip()

    name = str(front.get("name", ""))
    if not NAME.match(name):
        errors.append(f"name 须为小写字母、数字和连字符：{name!r}")
    if name != directory.name:
        errors.append(f"name {name!r} 与目录名 {directory.name!r} 不一致")
    description = str(front.get("description", "")).strip()
    if not 20 <= len(description) <= 500:
        errors.append(f"description 长度应为 20—500 字，当前 {len(description)}")
    metadata = front.get("metadata") or {}
    if not VERSION.match(str(metadata.get("version", ""))):
        errors.append("metadata.version 须为语义化版本，例如 '1.0.0'")
    if not str(metadata.get("title", "")).strip():
        errors.append("metadata.title 不能为空")
    if len(body) < 80:
        errors.append("正文过短，需写明输入、流程、判断规则和输出约定")

    referenced = set()
    for target in LINK.findall(body) + INLINE_PATH.findall(body):
        if re.match(r"^[a-z]+://", target):
            continue
        path = (directory / target).resolve()
        if not path.is_relative_to(directory.resolve()):
            errors.append(f"引用超出技能目录：{target}")
        elif not path.exists():
            errors.append(f"引用路径不存在：{target}")
        else:
            referenced.add(path)
    resources = [p for p in directory.rglob("*") if p.is_file() and "__pycache__" not in p.parts and {"references", "scripts"} & set(p.relative_to(directory).parts[:1])]
    for resource in resources:
        if resource.resolve() not in referenced and not any(resource.resolve().is_relative_to(r) for r in referenced if r.is_dir()):
            warnings.append(f"资源未在正文中引用，无法按需披露：{resource.relative_to(directory).as_posix()}")

    tools_file = directory / "tools.json"
    if not tools_file.exists():
        warnings.append("缺少 tools.json，技能内部不能调用任何宿主工具")
    else:
        declared = json.loads(tools_file.read_text(encoding="utf-8-sig"))
        declared = declared.get("tools", []) if isinstance(declared, dict) else declared
        names = [t if isinstance(t, str) else t.get("name") for t in declared]
        for tool in names:
            if tool not in tools:
                errors.append(f"工具未在宿主注册：{tool}")
        if "shell.execute" in names:
            warnings.append("shell.execute 仅在宿主检测到 PowerShell/Bash 时注册，需提供 Python 实现路径")
        if len(names) != len(set(names)):
            warnings.append("tools.json 存在重复工具")

    for script in directory.glob("scripts/**/*.py"):
        try:
            compile(script.read_text(encoding="utf-8-sig"), str(script), "exec")
        except SyntaxError as exc:
            errors.append(f"脚本无法编译 {script.relative_to(directory).as_posix()}：{exc}")
        if script.parent == directory / "scripts" and importlib.util.find_spec(script.stem) is not None:
            errors.append(f"脚本模块名与标准库或已安装包重名，python.execute 中无法导入：{script.name}")

    acceptance = directory / "acceptance.json"
    if not acceptance.exists():
        warnings.append("缺少 acceptance.json 验收样例")
    else:
        try:
            cases = json.loads(acceptance.read_text(encoding="utf-8-sig")).get("cases", [])
        except json.JSONDecodeError as exc:
            cases = []
            errors.append(f"acceptance.json 不是有效 JSON：{exc}")
        if acceptance.exists() and not cases:
            errors.append("acceptance.json 至少需要一个验收样例")
        for case in cases:
            label = case.get("id", "?")
            if not str(case.get("request", "")).strip():
                errors.append(f"验收样例 {label} 缺少 request")
            for output in (case.get("expect") or {}).get("outputs", []):
                variable = str(output.get("name", ""))
                if not VARIABLE.match(variable) or variable in RESERVED or variable.endswith("_preview"):
                    errors.append(f"验收样例 {label} 的输出变量名无效：{variable!r}")
                if output.get("kind") not in KINDS:
                    errors.append(f"验收样例 {label} 的输出类型无效：{output.get('kind')!r}")
                if output.get("kind") == "table" and not output.get("columns"):
                    warnings.append(f"验收样例 {label} 的表格输出 {variable} 未声明关键列")

    for path in [skill_file, *directory.rglob("*.md"), *directory.rglob("*.json")]:
        if PENDING.search(path.read_text(encoding="utf-8-sig")):
            errors.append(f"存在未查证的占位内容：{path.relative_to(directory).as_posix()}")
    return errors, list(dict.fromkeys(warnings))


def main(argv=None):
    parser = argparse.ArgumentParser(description="校验产线业务 skill")
    parser.add_argument("path", type=Path, help="技能目录；配合 --all 时为技能根目录")
    parser.add_argument("--all", action="store_true", help="校验目录下的全部技能")
    parser.add_argument("--strict", action="store_true", help="警告视为错误")
    args = parser.parse_args(argv)
    root = project_root(args.path.resolve())
    tools = registered_tools(root)
    targets = sorted(p.parent for p in args.path.glob("*/SKILL.md")) if args.all else [args.path]
    failed = False
    for target in targets:
        errors, warnings = validate(target.resolve(), tools)
        state = "失败" if errors or (args.strict and warnings) else "通过"
        print(f"[{state}] {target.name}")
        for item in errors:
            print(f"  错误：{item}")
        for item in warnings:
            print(f"  警告：{item}")
        failed |= bool(errors) or (args.strict and bool(warnings))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
