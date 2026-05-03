import asyncio
import threading

from onebot_paths import setup_sys_path

setup_sys_path()

from agentmain import GeneraticAgent
from chatapp_common import ensure_single_instance, redirect_log, require_runtime
from onebot_app import OneBotApp
from onebot_config import load_config
from onebot_state import OneBotState


def run() -> None:
    # 入口：加载配置并启动 OneBot
    config = load_config()

    agent = GeneraticAgent()
    agent.verbose = False

    state = OneBotState(agent=agent, user_tasks={})

    _lock_sock = ensure_single_instance(config.lock_port, "OneBot")
    require_runtime(agent, "OneBot")
    redirect_log(__file__, config.log_file, "OneBot", config.allowed_users)

    # 启动 GA 后台线程
    threading.Thread(target=agent.run, daemon=True).start()

    app = OneBotApp(state, config)
    # 启动 WebSocket 事件循环
    asyncio.run(app.connect_and_run())


if __name__ == "__main__":
    run()
