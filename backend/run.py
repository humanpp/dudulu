"""开发启动入口（Windows 友好，推荐日常使用）。

为什么不用 `uvicorn main:app --reload`：
    uvicorn 的 reloader 在 Windows 上 Ctrl+C 后**可能卡住**——
    父进程的主循环阻塞在 watchfiles 的 Rust watcher 里
    （`for changes in self: next(watcher)`），Python 层的信号处理器要等
    控制权回到 Python 才跑；子进程已经优雅退出并打印
    "Finished server process [pid]"，父进程却在
    `basereload.shutdown()` 的 `self.process.join()`（无超时）里等，
    于是 cmd 不返回（2026-10-18 实测）。

本脚本**单进程**启动（无 reloader），Ctrl+C 由 uvicorn.Server 自己处理，
干净退出、不留残余。

用法：
    python run.py                  # http://127.0.0.1:8000
    python run.py --port 8080
    python run.py --host 0.0.0.0   # 局域网可访问
    python run.py --reload         # 需要热重载时（沿用 uvicorn reloader，
                                   #  Windows 下 Ctrl+C 若卡住再按一次）
"""

from __future__ import annotations

import argparse
import logging
import sys

import uvicorn


def parse_args() -> argparse.Namespace:
    """解析命令行参数。"""
    parser = argparse.ArgumentParser(description="dudulu 后端（开发启动）")
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址（默认 127.0.0.1；0.0.0.0 = 局域网可见）")
    parser.add_argument("--port", type=int, default=8000, help="端口")
    parser.add_argument("--reload", action="store_true",
                        help="开启热重载（uvicorn reloader）")
    parser.add_argument("--log-level", default="info",
                        choices=["critical", "error", "warning", "info",
                                 "debug", "trace"])
    return parser.parse_args()


def main() -> int:
    """启动 uvicorn（单进程；Ctrl+C 干净退出）。"""
    args = parse_args()
    if args.reload and sys.platform == "win32":
        print("提示：Windows 下 --reload 的 Ctrl+C 可能卡在 watchfiles，"
              "若命令行不返回，再按一次 Ctrl+C 即可。", flush=True)
    config = uvicorn.Config(
        "main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level=args.log_level,
    )
    server = uvicorn.Server(config)
    print(f"dudulu 启动中 → http://{args.host}:{args.port}/  "
          f"(API 文档 /docs)", flush=True)
    try:
        server.run()
    except KeyboardInterrupt:
        # 单进程模式下 uvicorn 已处理；这里兜底防止 traceback
        pass
    finally:
        logging.shutdown()
    print("dudulu 已退出", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
