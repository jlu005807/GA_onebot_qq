import asyncio
import os
import signal
import threading
from datetime import datetime
from typing import Optional, Tuple

from onebot_paths import setup_sys_path

setup_sys_path()

from agentmain import GeneraticAgent
from chatapp_common import ensure_single_instance, redirect_log, require_runtime
from onebot_app import OneBotApp
from onebot_config import load_config
from onebot_state import OneBotState

DEFAULT_LOG_NAME = "onebot.log"


def log_dir_for(script_file: str) -> str:
    # 与 chatapp_common.redirect_log 的算法保持一致：<项目根>/temp
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(script_file))), "temp")


def _log_stem_ext(log_name: str) -> Tuple[str, str]:
    base = os.path.basename((log_name or DEFAULT_LOG_NAME).strip()) or DEFAULT_LOG_NAME
    stem, ext = os.path.splitext(base)
    return (stem or "onebot"), (ext or ".log")


def _build_timestamped_log_name(log_name: str, now: Optional[datetime] = None) -> str:
    stem, ext = _log_stem_ext(log_name)
    ts = (now or datetime.now()).strftime("%Y%m%d_%H%M%S")
    return f"{stem}_{ts}{ext}"


def _prune_old_logs(log_name: str, keep: int, log_dir: Optional[str] = None) -> int:
    """只保留最近 keep 个启动日志，返回删除数量。

    匹配用的前缀必须由 log_name 推导，不能写死 "onebot_"：日志基名是可配置的
    （ONEBOT_LOG_FILE），写死会让 ONEBOT_LOG_KEEP 在自定义基名下完全失效。
    本函数在日志文件创建之后调用，新文件最新、必然被保留。
    """
    if keep <= 0:
        return 0
    stem, ext = _log_stem_ext(log_name)
    prefix = f"{stem}_"
    directory = log_dir if log_dir is not None else log_dir_for(__file__)
    try:
        entries = [
            entry
            for entry in os.scandir(directory)
            if entry.is_file() and entry.name.startswith(prefix) and entry.name.endswith(ext)
        ]
        entries.sort(key=lambda entry: entry.stat().st_mtime, reverse=True)
    except OSError:
        return 0

    removed = 0
    for entry in entries[keep:]:
        try:
            os.remove(entry.path)
            removed += 1
        except OSError:
            pass
    return removed


def _install_stop_handlers(loop: asyncio.AbstractEventLoop, on_stop) -> None:
    """把 SIGINT/SIGTERM 变成一次协作式停机请求。

    默认的 SIGINT 会直接抛 KeyboardInterrupt 打断事件循环，在途的 Agent 轮次
    就地失联。这里改成唤醒事件循环去走正常的收敛流程；再按一次才强制退出。
    """
    hits = {"count": 0}

    def _handler(signum, frame):
        hits["count"] += 1
        if hits["count"] >= 2:
            raise KeyboardInterrupt
        loop.call_soon_threadsafe(on_stop)

    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            signal.signal(sig, _handler)
        except (ValueError, OSError, AttributeError):
            # 非主线程或平台不支持时退回默认行为
            pass


async def _serve(app: OneBotApp) -> None:
    loop = asyncio.get_running_loop()

    def _on_stop() -> None:
        print("[OneBot] shutdown requested, finishing in-flight work...")
        app.request_close()

    _install_stop_handlers(loop, _on_stop)
    try:
        await app.connect_and_run()
    finally:
        # CancelledError 属于 BaseException，会穿过这里继续向上传播，
        # 这样 asyncio.run 才能把它转回 KeyboardInterrupt。
        try:
            await app.aclose()
        except Exception as exc:
            print(f"[OneBot] shutdown cleanup failed: {exc!r}")


def run() -> None:
    # 入口：加载配置并启动 OneBot
    config = load_config()

    agent = GeneraticAgent()
    agent.verbose = False

    state = OneBotState(agent=agent, user_tasks={})

    _lock_sock = ensure_single_instance(config.lock_port, "OneBot")
    require_runtime(agent, "OneBot")
    run_log_file = _build_timestamped_log_name(config.log_file)
    redirect_log(__file__, run_log_file, "OneBot", config.allowed_users)
    _prune_old_logs(config.log_file, config.log_keep)

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
        try:
            _lock_sock.close()
        except OSError:
            pass
        print("[OneBot] stopped.")


if __name__ == "__main__":
    run()
