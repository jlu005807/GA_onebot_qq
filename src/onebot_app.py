import asyncio, json, os, sys, threading, time
from collections import deque

# 将 GA 根目录和 frontends 目录加入路径
GA_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, GA_ROOT)
sys.path.insert(0, os.path.join(GA_ROOT, "frontends"))
from agentmain import GeneraticAgent
from chatapp_common import AgentChatMixin, ensure_single_instance, public_access, redirect_log, require_runtime, split_text, clean_reply, extract_files, strip_files, build_done_text

try:
    import websockets
except ImportError:
    print("Please install websockets: pip install websockets")
    sys.exit(1)

# ── 配置 ──────────────────────────────────────────────────────────
# 从 mykey.py 读取配置（如果有的话），否则使用默认值
from llmcore import mykeys

LAGRANGE_WS = str(mykeys.get("onebot_ws_url", "") or "ws://127.0.0.1:8080/onebot/v11/ws").strip()
ADMIN_QQ = str(mykeys.get("admin_qq", "") or "").strip()
ALLOWED = {str(x).strip() for x in mykeys.get("onebot_allowed_users", mykeys.get("qq_allowed_users", ["*"])) if str(x).strip()}
ACCESS_TOKEN = str(mykeys.get("onebot_access_token", "") or "").strip()

# 管理员集合（支持多个，逗号分隔）
ADMIN_SET = {x.strip() for x in ADMIN_QQ.split(",") if x.strip()}

agent = GeneraticAgent()
agent.verbose = False

PROCESSED_IDS = deque(maxlen=2000)
USER_TASKS = {}
MSG_ID_COUNTER = 1
SEQ_LOCK = threading.Lock()

# ── 消息队列：每个用户独立队列，最多5条，一次处理一个 ──
USER_QUEUES = {}       # {user_id: asyncio.Queue}
PROCESSING_USERS = set()  # 正在处理的用户
MAX_QUEUE_SIZE = 5
MAX_MSG_LENGTH = 500  # 单条消息最大字符数


def _next_msg_id():
    global MSG_ID_COUNTER
    with SEQ_LOCK:
        MSG_ID_COUNTER += 1
        return MSG_ID_COUNTER


# ── OneBotApp (仿 qqapp.py 结构) ─────────────────────────────────
class OneBotApp(AgentChatMixin):
    label, source, split_limit = "OneBot", "onebot", 1500

    def __init__(self):
        super().__init__(agent, USER_TASKS)
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
                "echo": str(_next_msg_id()),
            }
            try:
                await self.ws.send(json.dumps(payload))
            except Exception as e:
                print(f"[OneBot] send error: {e}")

    def _is_at_bot(self, raw_msg):
        """检查消息是否@了机器人"""
        if isinstance(raw_msg, str):
            return f"[CQ:at,qq={self.bot_qq}]" in raw_msg
        elif isinstance(raw_msg, list):
            for seg in raw_msg:
                if isinstance(seg, dict) and seg.get("type") == "at":
                    if str(seg.get("data", {}).get("qq", "")) == self.bot_qq:
                        return True
            return False
        return False

    async def _process_user_queue(self, user_id, chat_id, is_group, message_id):
        """从用户队列中逐条处理消息"""
        queue = USER_QUEUES.get(user_id)
        if not queue:
            return
        
        PROCESSING_USERS.add(user_id)
        try:
            while not queue.empty():
                content = await queue.get()
                # 命令处理
                if content.startswith("/"):
                    await self.handle_command(chat_id, content, msg_id=message_id, is_group=is_group)
                else:
                    await self.run_agent(chat_id, content, msg_id=message_id, is_group=is_group)
                await asyncio.sleep(1)  # 间隔避免刷屏
        finally:
            PROCESSING_USERS.discard(user_id)
            if user_id in USER_QUEUES and USER_QUEUES[user_id].empty():
                del USER_QUEUES[user_id]

    def _strip_at(self, content):
        """移除消息中的@标签"""
        import re
        # CQ码格式
        content = re.sub(r'\[CQ:at,qq=\d+\]\s*', '', content).strip()
        return content

    async def handle_event(self, event: dict):
        """处理来自 OneBot 的事件"""
        post_type = event.get("post_type", "")
        
        # 获取机器人自身QQ号（从 connect 事件或首次消息）
        if not self.bot_qq:
            self_id = event.get("self_id")
            if self_id:
                self.bot_qq = str(self_id)
                print(f"[OneBot] Bot QQ: {self.bot_qq}")

        if post_type != "message":
            return

        message_id = event.get("message_id")
        if message_id in PROCESSED_IDS:
            return
        PROCESSED_IDS.append(message_id)

        msg_type = event.get("message_type")  # "private" or "group"
        is_group = msg_type == "group"

        # 提取用户ID
        sender = event.get("sender", {})
        user_id = str(sender.get("user_id", "unknown"))
        group_id = str(event.get("group_id", "")) if is_group else ""
        chat_id = group_id if is_group else user_id

        # 判断是否为管理员
        is_admin = user_id in ADMIN_SET

        # 提取消息文本
        raw_msg = event.get("message", "")
        if isinstance(raw_msg, str):
            content = raw_msg.strip()
        elif isinstance(raw_msg, list):
            # OneBot v11 CQ 消息格式
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
        if not public_access(ALLOWED) and user_id not in ALLOWED:
            print(f"[OneBot] unauthorized user: {user_id}")
            return

        # 长消息拦截
        if len(content) > MAX_MSG_LENGTH:
            await self.send_text(chat_id, f"⚠️ 消息过长（{len(content)}字，上限{MAX_MSG_LENGTH}字），请精简后重发。", msg_id=message_id, is_group=is_group)
            return

        # 群聊必须@才回复
        if is_group:
            # 确保bot_qq已初始化
            if not self.bot_qq:
                self_id = event.get("self_id")
                if self_id:
                    self.bot_qq = str(self_id)
                    print(f"[OneBot] Bot QQ initialized late: {self.bot_qq}")
                else:
                    print(f"[OneBot] WARNING: cannot get bot_qq, ignoring group msg")
                    return
            # 检查是否@
            if not self._is_at_bot(raw_msg):
                return  # 未@机器人，忽略

        # 移除@标签
        if is_group:
            content = self._strip_at(content)

        if not content:
            return

        tag = f"群{group_id}" if is_group else "私聊"
        print(f"[OneBot] {tag} 消息 from {user_id} {'[ADMIN]' if is_admin else ''}: {content}")

        # 命令处理
        if content.startswith("/"):
            cmd = content.split()[0].lower()
            # 查询命令：所有人可用
            public_cmds = ["/help", "/status", "/llm"]
            # 管理命令：仅管理员可用
            admin_cmds = ["/stop", "/new", "/restore", "/continue"]
            
            if cmd in admin_cmds and not is_admin:
                return await self.send_text(chat_id, "❌ 仅管理员可执行此命令", msg_id=message_id, is_group=is_group)
            
            return await self.handle_command(chat_id, content, msg_id=message_id, is_group=is_group)

        # 普通消息：非管理员安全限制
        if not is_admin:
            # 敏感操作关键词（文件/系统/进程/网络/敏感信息）
            danger_keywords = [
                # 文件操作
                "创建文件", "写入", "删除", "修改文件", "编辑文件", "file_write", "file_patch", "file_read",
                "移动文件", "重命名", "复制文件", "压缩", "解压",
                # 目录操作
                "查看目录", "列出文件", "目录列表", "ls ", "dir ", "tree", "目录结构", "文件夹", "浏览目录", "查目录", "看看目录", "folder", "explorer", "find ", "find.exe", "查看文件", "列出目录", "show dir", "list files",
                # 系统级操作
                "关机", "重启", "shutdown", "restart", "注销", "logoff",
                # 进程管理
                "进程", "杀进程", "taskkill", "kill", "结束进程", "启动服务", "停止服务",
                "小程序", "线程", "运行的程序", "任务管理器", "tasklist", "wmic", "ps ", "top", "pkill", "进程管理", "服务管理", "服务列表", "正在运行", "后台程序",
                # 硬件操作
                "关闭屏幕", "屏幕", "显示器", "休眠", "睡眠", "待机", "唤醒", "硬件", "电源管理", "powercfg", "关屏",
                # 批处理
                "bat", ".cmd", "批处理", "脚本文件",
                # 网络操作
                "网络配置", "端口", "防火墙", "iptables", "netstat", "ping", "curl", "wget",
                # 敏感信息
                "环境变量", "密码", "密钥", "token", "secret", "配置文件", ".env",
                # 脚本执行
                "执行脚本", "运行命令", "cmd", "powershell", "bash", "code_run",
                # 系统信息
                "系统信息", "内存", "CPU", "磁盘", "sysinfo", "systeminfo",
                # 浏览器操作
                "浏览器", "打开网页", "打开链接", "网页截图", "web_scan", "web_execute", "selenium", "浏览器操作", "网页操作", "Chrome", "Firefox",
            ]
            has_danger = any(kw in content for kw in danger_keywords)
            if has_danger:
                return await self.send_text(chat_id, "⚠️ 安全限制：该操作仅限管理员执行。\n如需使用文件/系统/进程管理功能，请联系管理员。", msg_id=message_id, is_group=is_group)

        # 使用队列限流：每个用户最多排队5条，一次处理一个
        if user_id not in USER_QUEUES:
            USER_QUEUES[user_id] = asyncio.Queue(maxsize=MAX_QUEUE_SIZE)
        
        queue = USER_QUEUES[user_id]
        if queue.full():
            await self.send_text(chat_id, "⏳ 消息队列已满(5条)，请等待当前回复完成", msg_id=message_id, is_group=is_group)
            return
        
        await queue.put(content)
        
        # 如果该用户没有正在处理的任务，启动队列处理器
        if user_id not in PROCESSING_USERS:
            asyncio.create_task(self._process_user_queue(user_id, chat_id, is_group, message_id))

    async def connect_and_run(self):
        """连接到 OneBot 的反向 WebSocket 并处理消息"""
        headers = {}
        if ACCESS_TOKEN:
            headers["Authorization"] = f"Bearer {ACCESS_TOKEN}"

        while True:
            try:
                print(f"[OneBot] connecting to {LAGRANGE_WS} ...")
                async with websockets.connect(
                    LAGRANGE_WS,
                    additional_headers=headers,
                    ping_interval=20,
                    ping_timeout=60,
                    close_timeout=10,
                ) as ws:
                    self.ws = ws
                    print("[OneBot] connected!")

                    # 注册心跳任务
                    heartbeat_task = asyncio.create_task(self._heartbeat(ws))

                    try:
                        async for raw_msg in ws:
                            try:
                                event = json.loads(raw_msg)
                                if event.get("post_type") == "meta_event":
                                    # 生命周期事件，忽略
                                    continue
                                asyncio.create_task(self.handle_event(event))
                            except json.JSONDecodeError:
                                print(f"[OneBot] invalid JSON: {raw_msg[:200]}")
                            except Exception as e:
                                print(f"[OneBot] handle_event error: {e}")
                    finally:
                        heartbeat_task.cancel()
                        self.ws = None

            except websockets.exceptions.ConnectionClosed as e:
                print(f"[OneBot] connection closed: {e}")
            except ConnectionRefusedError:
                print(f"[OneBot] connection refused, is NapCat running?")
            except Exception as e:
                print(f"[OneBot] error: {e}")

            print("[OneBot] reconnect in 5s...")
            await asyncio.sleep(5)

    async def _heartbeat(self, ws):
        """发送心跳（如果需要）"""
        while True:
            try:
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                break


# ── 启动入口 ─────────────────────────────────────────────────────
if __name__ == "__main__":
    _LOCK_SOCK = ensure_single_instance(19529, "OneBot")  # 用不同端口避免冲突
    require_runtime(agent, "OneBot")
    redirect_log(__file__, "onebot.log", "OneBot", ALLOWED)

    # 启动 GA 后台线程
    threading.Thread(target=agent.run, daemon=True).start()

    # 启动 WebSocket 事件循环
    app = OneBotApp()
    asyncio.run(app.connect_and_run())