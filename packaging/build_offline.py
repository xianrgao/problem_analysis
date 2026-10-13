"""Build the Windows 10/11 x64 offline package.

Usage:
    python packaging/build_offline.py [--build-frontend] [--download-python 3.13.x | --python-installer PATH]
                                      [--output release] [--skip-wheels] [--skip-demo] [--no-zip]
    python packaging/build_offline.py --verify release/line-engineer-<version>-win-x64

The package contains the source, prebuilt frontend, locked dependencies as
Windows x64 CPython 3.13 wheels, the optional user-level Python installer,
deterministic demo data, install/start scripts, SHA256SUMS.txt and the Chinese
migration guide. Daily use on the work computer needs no Node, Docker or
internet access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET_PYTHON = "3.13"
PLATFORM = "win_amd64"
SUMS = "SHA256SUMS.txt"
COPY = ["main.py", "pyproject.toml", "requirements.lock", "README.md", "src", "skills", "config", "examples", "docs", "tests",
        "packaging", ".cursor/skills", "web/src", "web/index.html", "web/package.json", "web/package-lock.json", "web/tsconfig.json", "web/vite.config.ts"]
IGNORE = shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache", "node_modules", "*.tsbuildinfo")


def project_version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]


def build_frontend():
    npm = shutil.which("npm")
    if not npm:
        raise SystemExit("未找到 npm，无法构建前端；可在已有 web/dist 的情况下省略 --build-frontend")
    for command in (["ci"], ["run", "build"]):
        subprocess.run([npm, *command], cwd=ROOT / "web", check=True)


def copy_sources(stage: Path):
    for item in COPY:
        source = ROOT / item
        if not source.exists():
            continue
        target = stage / item
        target.parent.mkdir(parents=True, exist_ok=True)
        if source.is_dir():
            shutil.copytree(source, target, ignore=IGNORE)
        else:
            shutil.copy2(source, target)
    dist = ROOT / "web" / "dist"
    if not (dist / "index.html").is_file():
        raise SystemExit("缺少预构建前端 web/dist，请先运行 npm run build 或使用 --build-frontend")
    shutil.copytree(dist, stage / "web" / "dist")
    for script in (ROOT / "packaging" / "windows").iterdir():
        shutil.copy2(script, stage / script.name)


def download_wheels(stage: Path) -> list[str]:
    wheels = stage / "wheels"
    subprocess.run([sys.executable, "-m", "pip", "download", "--disable-pip-version-check",
                    "-r", str(ROOT / "requirements.lock"), "-d", str(wheels), "--only-binary=:all:",
                    "--platform", PLATFORM, "--python-version", TARGET_PYTHON, "--implementation", "cp",
                    "--abi", "cp" + TARGET_PYTHON.replace(".", ""), "--abi", "abi3", "--abi", "none"], check=True)
    return sorted(p.name for p in wheels.glob("*.whl"))


def python_installer(stage: Path, path: Path | None, version: str | None) -> str | None:
    if not path and not version:
        return None
    target_dir = stage / "python"
    target_dir.mkdir()
    if version:
        if not version.startswith(TARGET_PYTHON + "."):
            raise SystemExit(f"Python 安装包版本需为 {TARGET_PYTHON}.x")
        name = f"python-{version}-amd64.exe"
        with urllib.request.urlopen(f"https://www.python.org/ftp/python/{version}/{name}", timeout=120) as response:
            (target_dir / name).write_bytes(response.read())
        return name
    if not path.name.startswith(f"python-{TARGET_PYTHON}.") or not path.name.endswith("-amd64.exe"):
        raise SystemExit(f"请提供官方 Windows x64 安装包，例如 python-{TARGET_PYTHON}.x-amd64.exe")
    shutil.copy2(path, target_dir / path.name)
    return path.name


def generate_demo_data(stage: Path) -> dict:
    sys.path.insert(0, str(ROOT / "src"))
    from line_engineer.domain.demo import generate_demo
    manifest = generate_demo(stage / "data" / "demo", days=90)
    return {k: manifest.get(k) for k in ("start", "end", "days", "table_counts", "curve_count")}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def write_checksums(stage: Path) -> int:
    files = sorted(p for p in stage.rglob("*") if p.is_file() and p.name != SUMS)
    lines = [f"{sha256(p)}  {p.relative_to(stage).as_posix()}" for p in files]
    (stage / SUMS).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return len(lines)


def verify(stage: Path) -> list[str]:
    """Return problems found against SHA256SUMS.txt (missing or modified files)."""
    stage = Path(stage)
    sums = stage / SUMS
    if not sums.is_file():
        return [f"缺少 {SUMS}"]
    problems = []
    for line in sums.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        expected, relative = line.split("  ", 1)
        path = stage / relative
        if not path.is_file():
            problems.append(f"缺少文件：{relative}")
        elif sha256(path) != expected:
            problems.append(f"校验值不一致：{relative}")
    return problems


def build(output: Path, *, frontend=False, installer: Path | None = None, python_version: str | None = None,
          wheels=True, demo=True, archive=True) -> Path:
    if frontend:
        build_frontend()
    version = project_version()
    stage = output / f"line-engineer-{version}-win-x64"
    if stage.exists():
        shutil.rmtree(stage)
    stage.mkdir(parents=True)
    copy_sources(stage)
    manifest = {"name": "line-engineer", "version": version, "built_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "target": {"os": "Windows 10/11 x64", "python": TARGET_PYTHON, "platform": PLATFORM},
                "wheels": download_wheels(stage) if wheels else [],
                "python_installer": python_installer(stage, installer, python_version),
                "demo": generate_demo_data(stage) if demo else None, "frontend": "web/dist"}
    (stage / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    count = write_checksums(stage)
    print(f"已生成离线目录 {stage}（{count} 个文件，{len(manifest['wheels'])} 个 wheel）")
    if archive:
        zip_path = output / f"{stage.name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as bundle:
            for path in sorted(stage.rglob("*")):
                if path.is_file():
                    bundle.write(path, Path(stage.name) / path.relative_to(stage))
        print(f"已打包 {zip_path}")
    return stage


def main(argv=None):
    parser = argparse.ArgumentParser(description="构建 Windows x64 离线安装包")
    parser.add_argument("--output", type=Path, default=ROOT / "release")
    parser.add_argument("--build-frontend", action="store_true", help="先执行 npm ci 与 npm run build")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--python-installer", type=Path, help="已下载的 python-3.13.x-amd64.exe")
    source.add_argument("--download-python", metavar="VERSION", help="从 python.org 下载指定 3.13.x 安装包")
    parser.add_argument("--skip-wheels", action="store_true", help="不下载 wheels（仅用于检查目录结构）")
    parser.add_argument("--skip-demo", action="store_true", help="不预生成模拟数据（首次启动时生成）")
    parser.add_argument("--no-zip", action="store_true")
    parser.add_argument("--verify", type=Path, metavar="PACKAGE_DIR", help="只校验已解压的离线目录")
    args = parser.parse_args(argv)
    if args.verify:
        problems = verify(args.verify)
        print("\n".join(problems) if problems else "校验通过")
        return 1 if problems else 0
    build(args.output, frontend=args.build_frontend, installer=args.python_installer, python_version=args.download_python,
          wheels=not args.skip_wheels, demo=not args.skip_demo, archive=not args.no_zip)
    return 0


if __name__ == "__main__":
    sys.exit(main())
