import asyncio
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


def _build_timestamped_log_name(log_name: str) -> str:
    base = os.path.basename((log_name or "onebot.log").strip()) or "onebot.log"
    stem, ext = os.path.splitext(base)
    if not stem:
        stem = "onebot"
    if not ext:
        ext = ".log"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return f"{stem}_{ts}{ext}"


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

    # 启动 GA 后台线程
    threading.Thread(target=agent.run, daemon=True).start()

    app = OneBotApp(state, config)
    # 启动 WebSocket 事件循环
    asyncio.run(app.connect_and_run())


if __name__ == "__main__":
    run()
