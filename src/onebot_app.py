import asyncio
import json
import os
import re
import urllib.request
import uuid
from urllib.parse import urlparse

from onebot_paths import setup_sys_path

setup_sys_path()

from chatapp_common import AgentChatMixin, public_access, split_text
from onebot_config import OneBotConfig
from onebot_state import OneBotState

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
        # 附件落盘目录
        self.data_dir = self.config.data_dir
        self._data_dirs = {
            "image": os.path.join(self.data_dir, "image"),
            "record": os.path.join(self.data_dir, "record"),
            "file": os.path.join(self.data_dir, "file"),
        }
        for path in self._data_dirs.values():
            os.makedirs(path, exist_ok=True)

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

    async def _process_user_queue(self, user_id):
        """从用户队列中逐条处理消息"""
        queue = self.state.user_queues.get(user_id)
        if not queue:
            return

        self.state.processing_users.add(user_id)
        try:
            while not queue.empty():
                content, attachments, message_id, chat_id, is_group = await queue.get()
                if content and content.startswith("/"):
                    await self.handle_command(chat_id, content, msg_id=message_id, is_group=is_group)
                else:
                    prompt = self._build_agent_content(content, attachments)
                    await self.run_agent(chat_id, prompt, msg_id=message_id, is_group=is_group)
                await asyncio.sleep(1)  # 间隔避免刷屏
        finally:
            self.state.processing_users.discard(user_id)
            if user_id in self.state.user_queues and self.state.user_queues[user_id].empty():
                del self.state.user_queues[user_id]

    def _strip_at(self, content):
        """移除消息中的@标签"""
        content = re.sub(r"\[CQ:at,qq=\d+\]\s*", "", content).strip()
        return content

    def _extract_text(self, raw_msg):
        """抽取文本并移除 CQ 码"""
        if isinstance(raw_msg, str):
            content = raw_msg
        elif isinstance(raw_msg, list):
            parts = []
            for seg in raw_msg:
                if isinstance(seg, dict) and seg.get("type") == "text":
                    parts.append(seg.get("data", {}).get("text", ""))
                elif isinstance(seg, str):
                    parts.append(seg)
            content = "".join(parts)
        else:
            content = str(raw_msg)

        content = re.sub(r"\[CQ:[^\]]+\]", "", content)
        return content.strip()

    def _extract_segments(self, raw_msg):
        if isinstance(raw_msg, list):
            return [seg for seg in raw_msg if isinstance(seg, dict)]
        if isinstance(raw_msg, str) and "[CQ:" in raw_msg:
            return self._parse_cq_segments(raw_msg)
        return []

    def _parse_cq_segments(self, raw_msg: str):
        segments = []
        for code in re.findall(r"\[CQ:[^\]]+\]", raw_msg):
            seg = self._parse_cq_segment(code)
            if seg:
                segments.append(seg)
        return segments

    def _parse_cq_segment(self, code: str):
        if not code.startswith("[CQ:") or not code.endswith("]"):
            return None
        inner = code[4:-1]
        if not inner:
            return None
        parts = inner.split(",")
        seg_type = parts[0].strip()
        data = {}
        for part in parts[1:]:
            if "=" in part:
                key, value = part.split("=", 1)
                data[key.strip()] = value.strip()
        return {"type": seg_type, "data": data}

    def _format_attachments(self, attachments):
        lines = []
        for idx, item in enumerate(attachments, start=1):
            size_kb = max(1, item["size"] // 1024)
            lines.append(
                f"附件{idx}: type={item['type']} path={item['path']} size={size_kb}KB"
            )
        return "\n".join(lines)

    def _build_agent_content(self, content: str, attachments) -> str:
        """为模型附加纯文本提示词与附件路径"""
        parts = []
        if content:
            parts.append(content)
        if attachments:
            parts.append(self._format_attachments(attachments))
        hint = self.config.plain_text_hint
        if hint:
            parts.append(hint)
        return "\n\n".join(parts)

    def _get_segment_url(self, data: dict) -> str:
        url = str(data.get("url", "")).strip()
        if url:
            return url
        file_ref = str(data.get("file", "")).strip()
        if file_ref.startswith("http://") or file_ref.startswith("https://"):
            return file_ref
        return ""

    def _build_filename(self, name_hint: str, url: str, default_ext: str) -> str:
        base = ""
        ext = ""
        if name_hint:
            base, ext = os.path.splitext(os.path.basename(name_hint))
        if not ext and url:
            ext = os.path.splitext(urlparse(url).path)[1]
        if not ext:
            ext = default_ext
        if not base:
            base = "file"
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
        if not base:
            base = "file"
        suffix = uuid.uuid4().hex[:8]
        return f"{base}_{suffix}{ext}"

    def _download_file_sync(self, url: str, dest_path: str, max_bytes: int) -> int:
        req = urllib.request.Request(url, headers={"User-Agent": "OneBot"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                length = resp.headers.get("Content-Length")
                if length:
                    try:
                        if int(length) > max_bytes:
                            return 0
                    except ValueError:
                        pass
                os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                total = 0
                with open(dest_path, "wb") as handle:
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            break
                        handle.write(chunk)
            if total > max_bytes:
                try:
                    os.remove(dest_path)
                except OSError:
                    pass
                return 0
            return total
        except Exception as exc:
            try:
                if os.path.exists(dest_path):
                    os.remove(dest_path)
            except OSError:
                pass
            print(f"[OneBot] download error: {exc}")
            return 0

    async def _download_to_local(self, url: str, seg_type: str, name_hint: str):
        save_dir = self._data_dirs.get(seg_type, self.data_dir)
        default_ext = ".jpg" if seg_type == "image" else ".silk" if seg_type == "record" else ".bin"
        filename = self._build_filename(name_hint, url, default_ext)
        dest_path = os.path.join(save_dir, filename)
        size = await asyncio.to_thread(
            self._download_file_sync, url, dest_path, self.config.max_file_bytes
        )
        if not size:
            return None
        return {"type": seg_type, "path": os.path.abspath(dest_path), "size": size}

    async def _download_attachments(self, raw_msg, chat_id, message_id, is_group):
        attachments = []
        errors = []
        for seg in self._extract_segments(raw_msg):
            seg_type = seg.get("type")
            if seg_type not in {"image", "record", "file"}:
                continue
            data = seg.get("data", {}) if isinstance(seg, dict) else {}
            url = self._get_segment_url(data)
            if not url:
                errors.append(f"{seg_type} 缺少下载地址")
                continue
            name_hint = str(data.get("name") or data.get("file") or "").strip()
            saved = await self._download_to_local(url, seg_type, name_hint)
            if not saved:
                errors.append(f"{seg_type} 下载失败或超过大小限制")
                continue
            attachments.append(saved)

        if errors:
            await self.send_text(
                chat_id,
                "⚠️ 附件未保存: " + "；".join(errors),
                msg_id=message_id,
                is_group=is_group,
            )
        return attachments

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
        if message_id is not None:
            if message_id in self.state.processed_ids:
                return
            self.state.processed_ids.append(message_id)

        msg_type = event.get("message_type")  # "private" or "group"
        is_group = msg_type == "group"

        sender = event.get("sender", {})
        user_id = str(sender.get("user_id", "unknown"))
        group_id = str(event.get("group_id", "")) if is_group else ""
        chat_id = group_id if is_group else user_id

        # 忽略机器人自身消息，避免回环
        if self.bot_qq and user_id == self.bot_qq:
            return

        is_admin = user_id in self.config.admin_set

        raw_msg = event.get("message", "")
        content = self._extract_text(raw_msg)

        # 权限检查
        if not public_access(self.config.allowed_users) and user_id not in self.config.allowed_users:
            print(f"[OneBot] unauthorized user: {user_id}")
            return

        if content and len(content) > self.config.max_msg_length:
            await self.send_text(
                chat_id,
                f"⚠️ 消息过长（{len(content)}字，上限{self.config.max_msg_length}字），请精简后重发。",
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

        # 命令优先处理
        if content and content.startswith("/"):
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

        attachments = await self._download_attachments(raw_msg, chat_id, message_id, is_group)

        if not content and not attachments:
            return

        tag = f"群{group_id}" if is_group else "私聊"
        display = content if content else f"[附件{len(attachments)}]"
        print(f"[OneBot] {tag} 消息 from {user_id} {'[ADMIN]' if is_admin else ''}: {display}")

        # 按用户排队限流
        if user_id not in self.state.user_queues:
            self.state.user_queues[user_id] = asyncio.Queue(maxsize=self.config.max_queue_size)

        queue = self.state.user_queues[user_id]
        if queue.full():
            await self.send_text(
                chat_id,
                f"⏳ 消息队列已满({self.config.max_queue_size}条)，请等待当前回复完成",
                msg_id=message_id,
                is_group=is_group,
            )
            return

        await queue.put((content, attachments, message_id, chat_id, is_group))

        if user_id not in self.state.processing_users:
            asyncio.create_task(self._process_user_queue(user_id))

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