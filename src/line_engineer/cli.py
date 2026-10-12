"""Command line entry: serve the local workbench or prepare demo data."""
from __future__ import annotations
import argparse
import webbrowser
from pathlib import Path


def main(argv=None):
    parser = argparse.ArgumentParser(prog="line-engineer", description="LINEA 产线硅基工程师本地工作台")
    parser.add_argument("--data-dir", type=Path, help="应用数据目录，默认使用 LINE_ENGINEER_DATA 或项目 data 目录")
    parser.add_argument("--demo-days", type=int, default=90, help="模拟数据天数")
    commands = parser.add_subparsers(dest="command")
    serve = commands.add_parser("serve", help="启动本机服务（默认命令）")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true", help="启动后打开浏览器")
    commands.add_parser("init-demo", help="生成或校验模拟 MOS、Splunk 与曲线数据")
    args = parser.parse_args(argv)

    from .application import Application
    application = Application(args.data_dir, demo_days=args.demo_days)
    if args.command == "init-demo":
        manifest = application.prepare_demo()
        print(f"模拟数据已就绪：{manifest['start']} 至 {manifest['end']}，{manifest['table_counts']}")
        return

    import uvicorn
    from .api.server import create_app
    port = getattr(args, "port", 8765)
    if getattr(args, "open", False):
        webbrowser.open(f"http://127.0.0.1:{port}")
    # Local single-user service: bind loopback only.
    uvicorn.run(create_app(application), host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
