import asyncio
import json
import re

from onebot_paths import setup_sys_path

setup_sys_path()

from chatapp_common import AgentChatMixin, public_access, split_text
from onebot_config import OneBotConfig
from onebot_state import MAX_MSG_LENGTH, MAX_QUEUE_SIZE, OneBotState

try:
    import websockets
except ImportError:
    print("Please install websockets: pip install websockets")
    raise SystemExit(1)


# OneBot 事件处理与消息转发
class OneBotApp(AgentChatMixin):
    label, source, split_limit = "OneBot", "onebot", 1500

    def __init__(self, state: OneBotState, config: OneBotConfig):
        super().__init__(state.agent, state.user_tasks)
        self.state = state
        self.config = config
        self.ws = None
        self.bot_qq = ""  # 机器人自身QQ号，连接后获取

    async def send_text(self, chat_id, content, *, msg_id=None, is_group=False, **ctx):
        """发送文本消息到 QQ"""
        if not self.ws:
            print("[OneBot] ws not connected, cannot send")
            return
        action = "send_group_msg" if is_group else "send_private_msg"
        for part in split_text(content, self.split_limit):
            params = {"group_id": int(chat_id)} if is_group else {"user_id": int(chat_id)}
            params["message"] = {"type": "text", "data": {"text": part}}
            if msg_id:
                params["message"] = [
                    {"type": "reply", "data": {"id": str(msg_id)}},
                    {"type": "text", "data": {"text": part}},
                ]
            payload = {
                "action": action,
                "params": params,
                "echo": str(self.state.next_msg_id()),
            }
            try:
                await self.ws.send(json.dumps(payload))
            except Exception as exc:
                print(f"[OneBot] send error: {exc}")

    def _is_at_bot(self, raw_msg):
        """检查消息是否@了机器人"""
        if isinstance(raw_msg, str):
            return f"[CQ:at,qq={self.bot_qq}]" in raw_msg
        if isinstance(raw_msg, list):
            for seg in raw_msg:
                if isinstance(seg, dict) and seg.get("type") == "at":
                    if str(seg.get("data", {}).get("qq", "")) == self.bot_qq:
                        return True
            return False
        return False

    async def _process_user_queue(self, user_id, chat_id, is_group, message_id):
        """从用户队列中逐条处理消息"""
        queue = self.state.user_queues.get(user_id)
        if not queue:
            return

        self.state.processing_users.add(user_id)
        try:
            while not queue.empty():
                content = await queue.get()
                if content.startswith("/"):
                    await self.handle_command(chat_id, content, msg_id=message_id, is_group=is_group)
                else:
                    await self.run_agent(chat_id, content, msg_id=message_id, is_group=is_group)
                await asyncio.sleep(1)  # 间隔避免刷屏
        finally:
            self.state.processing_users.discard(user_id)
            if user_id in self.state.user_queues and self.state.user_queues[user_id].empty():
                del self.state.user_queues[user_id]

    def _strip_at(self, content):
        """移除消息中的@标签"""
        content = re.sub(r"\[CQ:at,qq=\d+\]\s*", "", content).strip()
        return content

    async def handle_event(self, event: dict):
        """处理来自 OneBot 的事件"""
        post_type = event.get("post_type", "")

        if not self.bot_qq:
            self_id = event.get("self_id")
            if self_id:
                self.bot_qq = str(self_id)
                print(f"[OneBot] Bot QQ: {self.bot_qq}")

        if post_type != "message":
            return

        # 消息去重，避免重复处理
        message_id = event.get("message_id")
        if message_id in self.state.processed_ids:
            return
        self.state.processed_ids.append(message_id)

        msg_type = event.get("message_type")  # "private" or "group"
        is_group = msg_type == "group"

        sender = event.get("sender", {})
        user_id = str(sender.get("user_id", "unknown"))
        group_id = str(event.get("group_id", "")) if is_group else ""
        chat_id = group_id if is_group else user_id

        is_admin = user_id in self.config.admin_set

        raw_msg = event.get("message", "")
        if isinstance(raw_msg, str):
            content = raw_msg.strip()
        elif isinstance(raw_msg, list):
            parts = []
            for seg in raw_msg:
                if isinstance(seg, dict) and seg.get("type") == "text":
                    parts.append(seg.get("data", {}).get("text", ""))
                elif isinstance(seg, str):
                    parts.append(seg)
            content = "".join(parts).strip()
        else:
            content = str(raw_msg).strip()

        if not content:
            return

        # 权限检查
        if not public_access(self.config.allowed_users) and user_id not in self.config.allowed_users:
            print(f"[OneBot] unauthorized user: {user_id}")
            return

        if len(content) > MAX_MSG_LENGTH:
            await self.send_text(
                chat_id,
                f"⚠️ 消息过长（{len(content)}字，上限{MAX_MSG_LENGTH}字），请精简后重发。",
                msg_id=message_id,
                is_group=is_group,
            )
            return

        # 群聊仅在@机器人时响应
        if is_group:
            if not self.bot_qq:
                self_id = event.get("self_id")
                if self_id:
                    self.bot_qq = str(self_id)
                    print(f"[OneBot] Bot QQ initialized late: {self.bot_qq}")
                else:
                    print("[OneBot] WARNING: cannot get bot_qq, ignoring group msg")
                    return
            if not self._is_at_bot(raw_msg):
                return

        if is_group:
            content = self._strip_at(content)

        if not content:
            return

        tag = f"群{group_id}" if is_group else "私聊"
        print(f"[OneBot] {tag} 消息 from {user_id} {'[ADMIN]' if is_admin else ''}: {content}")

        # 命令优先处理
        if content.startswith("/"):
            cmd = content.split()[0].lower()
            admin_cmds = ["/stop", "/new", "/restore", "/continue"]

            if cmd in admin_cmds and not is_admin:
                return await self.send_text(
                    chat_id,
                    "❌ 仅管理员可执行此命令",
                    msg_id=message_id,
                    is_group=is_group,
                )

            return await self.handle_command(chat_id, content, msg_id=message_id, is_group=is_group)

        # 非管理员：敏感操作拦截
        if not is_admin:
            danger_keywords = [
                "创建文件",
                "写入",
                "删除",
                "修改文件",
                "编辑文件",
                "file_write",
                "file_patch",
                "file_read",
                "移动文件",
                "重命名",
                "复制文件",
                "压缩",
                "解压",
                "查看目录",
                "列出文件",
                "目录列表",
                "ls ",
                "dir ",
                "tree",
                "目录结构",
                "文件夹",
                "浏览目录",
                "查目录",
                "看看目录",
                "folder",
                "explorer",
                "find ",
                "find.exe",
                "查看文件",
                "列出目录",
                "show dir",
                "list files",
                "关机",
                "重启",
                "shutdown",
                "restart",
                "注销",
                "logoff",
                "进程",
                "杀进程",
                "taskkill",
                "kill",
                "结束进程",
                "启动服务",
                "停止服务",
                "小程序",
                "线程",
                "运行的程序",
                "任务管理器",
                "tasklist",
                "wmic",
                "ps ",
                "top",
                "pkill",
                "进程管理",
                "服务管理",
                "服务列表",
                "正在运行",
                "后台程序",
                "关闭屏幕",
                "屏幕",
                "显示器",
                "休眠",
                "睡眠",
                "待机",
                "唤醒",
                "硬件",
                "电源管理",
                "powercfg",
                "关屏",
                "bat",
                ".cmd",
                "批处理",
                "脚本文件",
                "网络配置",
                "端口",
                "防火墙",
                "iptables",
                "netstat",
                "ping",
                "curl",
                "wget",
                "环境变量",
                "密码",
                "密钥",
                "token",
                "secret",
                "配置文件",
                ".env",
                "执行脚本",
                "运行命令",
                "cmd",
                "powershell",
                "bash",
                "code_run",
                "系统信息",
                "内存",
                "CPU",
                "磁盘",
                "sysinfo",
                "systeminfo",
                "浏览器",
                "打开网页",
                "打开链接",
                "网页截图",
                "web_scan",
                "web_execute",
                "selenium",
                "浏览器操作",
                "网页操作",
                "Chrome",
                "Firefox",
            ]
            has_danger = any(kw in content for kw in danger_keywords)
            if has_danger:
                return await self.send_text(
                    chat_id,
                    "⚠️ 安全限制：该操作仅限管理员执行。\n如需使用文件/系统/进程管理功能，请联系管理员。",
                    msg_id=message_id,
                    is_group=is_group,
                )

        # 按用户排队限流
        if user_id not in self.state.user_queues:
            self.state.user_queues[user_id] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)

        queue = self.state.user_queues[user_id]
        if queue.full():
            await self.send_text(
                chat_id,
                "⏳ 消息队列已满(5条)，请等待当前回复完成",
                msg_id=message_id,
                is_group=is_group,
            )
            return

        await queue.put(content)

        if user_id not in self.state.processing_users:
            asyncio.create_task(self._process_user_queue(user_id, chat_id, is_group, message_id))

    async def connect_and_run(self):
        """连接到 OneBot 的反向 WebSocket 并处理消息"""
        headers = {}
        if self.config.access_token:
            headers["Authorization"] = f"Bearer {self.config.access_token}"

        # 断线自动重连
        while True:
            try:
                print(f"[OneBot] connecting to {self.config.ws_url} ...")
                async with websockets.connect(
                    self.config.ws_url,
                    additional_headers=headers,
                    ping_interval=20,
                    ping_timeout=60,
                    close_timeout=10,
                ) as ws:
                    self.ws = ws
                    print("[OneBot] connected!")

                    heartbeat_task = asyncio.create_task(self._heartbeat())

                    try:
                        async for raw_msg in ws:
                            try:
                                event = json.loads(raw_msg)
                                if event.get("post_type") == "meta_event":
                                    continue
                                asyncio.create_task(self.handle_event(event))
                            except json.JSONDecodeError:
                                print(f"[OneBot] invalid JSON: {raw_msg[:200]}")
                            except Exception as exc:
                                print(f"[OneBot] handle_event error: {exc}")
                    finally:
                        heartbeat_task.cancel()
                        self.ws = None

            except websockets.exceptions.ConnectionClosed as exc:
                print(f"[OneBot] connection closed: {exc}")
            except ConnectionRefusedError:
                print("[OneBot] connection refused, is NapCat running?")
            except Exception as exc:
                print(f"[OneBot] error: {exc}")

            print("[OneBot] reconnect in 5s...")
            await asyncio.sleep(5)

    async def _heartbeat(self):
        """发送心跳（如果需要）"""
        while True:
            try:
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                break