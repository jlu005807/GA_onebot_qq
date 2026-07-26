import asyncio
import contextlib
import os
import threading
from datetime import datetime

from onebot_paths import setup_sys_path

setup_sys_path()

from agentmain import GeneraticAgent
from chatapp_common import ensure_single_instance, redirect_log, require_runtime
from onebot_app import OneBotApp
from onebot_config import load_config
from onebot_state import OneBotState


def _prune_old_logs(keep: int) -> None:
    """只保留最近 keep 个启动日志；每次启动都新建文件，不清理会一直堆积。"""
    if keep <= 0:
        return
    log_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "temp")
    try:
        entries = [
            entry
            for entry in os.scandir(log_dir)
            if entry.is_file()
            and entry.name.startswith("onebot_")
            and entry.name.endswith(".log")
        ]
    except OSError:
        return
    try:
        entries.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
    except OSError:
        return
    for entry in entries[keep:]:
        try:
            os.remove(entry.path)
        except OSError:
            pass


def _build_timestamped_log_name(log_name: str) -> str:
    base = os.path.basename((log_name or "onebot.log").strip()) or "onebot.log"
    stem, ext = os.path.splitext(base)
    if not stem:
        stem = "onebot"
    if not ext:
        ext = ".log"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{stem}_{ts}{ext}"


async def _serve(app: OneBotApp) -> None:
    # Ctrl+C 时先停止收新消息、等在途任务收敛，再退出，避免半途中断工具调用
    try:
        await app.connect_and_run()
    except asyncio.CancelledError:
        pass
    finally:
        # 本协程可能正处于被取消状态，清理属于尽力而为，不能让它再抛出来
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await app.aclose()


def run() -> None:
    # 入口：加载配置并启动 OneBot
    config = load_config()

    agent = GeneraticAgent()
    agent.verbose = False

    state = OneBotState(agent=agent, user_tasks={})

    _lock_sock = ensure_single_instance(config.lock_port, "OneBot")
    require_runtime(agent, "OneBot")
    _prune_old_logs(config.log_keep)
    run_log_file = _build_timestamped_log_name(config.log_file)
    redirect_log(__file__, run_log_file, "OneBot", config.allowed_users)

    # 启动 GA 后台线程
    threading.Thread(target=agent.run, daemon=True).start()

    app = OneBotApp(state, config)
    # 启动 WebSocket 事件循环
    try:
        asyncio.run(_serve(app))
    except KeyboardInterrupt:
        print("[OneBot] interrupted, shutting down...")
    finally:
        agent.abort()
        _lock_sock.close()


if __name__ == "__main__":
    run()
