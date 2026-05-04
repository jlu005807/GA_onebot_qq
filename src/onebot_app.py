import asyncio
import inspect
import json

from onebot_paths import setup_sys_path

setup_sys_path()

from chatapp_common import AgentChatMixin, public_access, split_text
from onebot_attachment import OneBotAttachmentManager, cleanup_attachments
from onebot_config import OneBotConfig
from onebot_message import (
    build_agent_prompt,
    extract_at_mentions,
    extract_segments,
    extract_text,
    is_at_bot,
    normalize_outgoing_content,
    parse_send_content,
)
from onebot_state import OneBotState
from onebot_ws import build_ws_connect_url, mask_ws_url

try:
    import websockets
except ImportError:
    print("Please install websockets: pip install websockets")
    raise SystemExit(1)


QUEUE_IDLE_SECONDS = 5


class OneBotApp(AgentChatMixin):
    label, source, split_limit = "OneBot", "onebot", 1500

    def __init__(self, state: OneBotState, config: OneBotConfig):
        super().__init__(state.agent, state.user_tasks)
        self.state = state
        self.config = config
        self.ws = None
        self.bot_qq = ""
        self.attachment_manager = OneBotAttachmentManager(
            data_dir=self.config.data_dir,
            max_file_bytes=self.config.max_file_bytes,
        )

    def _is_admin_user(self, user_id: str) -> bool:
        return user_id in self.config.admin_set

    def _is_allowed_user(self, user_id: str, is_admin: bool) -> bool:
        if is_admin:
            return True
        if public_access(self.config.allowed_users):
            return True
        return user_id in self.config.allowed_users

    def _ensure_bot_qq(self, event: dict) -> bool:
        if self.bot_qq:
            return True
        self_id = event.get("self_id")
        if not self_id:
            return False
        self.bot_qq = str(self_id)
        print(f"[OneBot] Bot QQ: {self.bot_qq}")
        return True

    async def send_text(self, chat_id, content, *, msg_id=None, is_group=False, **ctx):
        if not self.ws:
            print("[OneBot] ws not connected, cannot send")
            return

        content = normalize_outgoing_content(content or "")
        action = "send_group_msg" if is_group else "send_private_msg"

        for part in split_text(content, self.split_limit):
            params = {"group_id": int(chat_id)} if is_group else {"user_id": int(chat_id)}
            msg_segments = []
            if msg_id:
                msg_segments.append({"type": "reply", "data": {"id": str(msg_id)}})
            part_segments = parse_send_content(part)
            if part_segments:
                msg_segments.extend(part_segments)
            else:
                msg_segments.append({"type": "text", "data": {"text": part}})
            params["message"] = msg_segments if len(msg_segments) > 1 else msg_segments[0]

            payload = {
                "action": action,
                "params": params,
                "echo": str(self.state.next_msg_id()),
            }
            try:
                await self.ws.send(json.dumps(payload))
            except Exception as exc:
                print(f"[OneBot] send error: {exc}")

    def _get_or_create_queue(self, user_id: str) -> asyncio.Queue:
        if user_id not in self.state.user_queues:
            self.state.user_queues[user_id] = asyncio.Queue(maxsize=self.config.max_queue_size)
        return self.state.user_queues[user_id]

    async def _process_user_queue(self, user_id: str) -> None:
        queue = self.state.user_queues.get(user_id)
        if not queue:
            return

        self.state.processing_users.add(user_id)
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=QUEUE_IDLE_SECONDS)
                except asyncio.TimeoutError:
                    break

                try:
                    (
                        content,
                        attachments,
                        message_id,
                        chat_id,
                        is_group,
                        is_admin,
                        sender_qq,
                        sender_nickname,
                        at_mentions,
                    ) = item

                    if content and content.startswith("/"):
                        await self.handle_command(
                            chat_id, content, msg_id=message_id, is_group=is_group
                        )
                    else:
                        prompt = build_agent_prompt(
                            content,
                            attachments,
                            is_group=is_group,
                            is_admin=is_admin,
                            sender_nickname=sender_nickname,
                            sender_qq=sender_qq,
                            at_mentions=at_mentions,
                            plain_text_hint=self.config.plain_text_hint,
                        )
                        await self.run_agent(
                            chat_id, prompt, msg_id=message_id, is_group=is_group
                        )
                    await asyncio.sleep(1)
                except Exception as exc:
                    print(f"[OneBot] queue item error: {exc}")
                finally:
                    queue.task_done()
        finally:
            self.state.processing_users.discard(user_id)
            if user_id in self.state.user_queues and self.state.user_queues[user_id].empty():
                del self.state.user_queues[user_id]

    async def _handle_message_event(self, event: dict) -> None:
        raw_msg = event.get("message", "")
        message_id = event.get("message_id")
        if message_id is not None:
            if message_id in self.state.processed_ids:
                return
            self.state.processed_ids.append(message_id)

        msg_type = event.get("message_type")
        is_group = msg_type == "group"
        sender = event.get("sender", {})

        raw_user_id = sender.get("user_id") or event.get("user_id")
        user_id = str(raw_user_id).strip() if raw_user_id is not None else ""
        group_id = str(event.get("group_id", "")) if is_group else ""
        chat_id = group_id if is_group else user_id

        if not user_id:
            print("[OneBot] missing user_id, ignore message")
            return

        # Ignore self messages to prevent loops.
        if self._ensure_bot_qq(event) and user_id == self.bot_qq:
            return

        # Group handling rules.
        if is_group:
            if not self.config.allow_group:
                return
            if not group_id:
                print("[OneBot] missing group_id, ignore group message")
                return
            if (
                not public_access(self.config.allowed_groups)
                and group_id not in self.config.allowed_groups
            ):
                return
            if not self._ensure_bot_qq(event):
                print("[OneBot] cannot get bot_qq, ignoring group msg")
                return
            if not is_at_bot(raw_msg, self.bot_qq):
                return

        is_admin = self._is_admin_user(user_id)
        content = extract_text(raw_msg)

        if not self._is_allowed_user(user_id, is_admin):
            print(f"[OneBot] unauthorized user: {user_id}")
            return

        if content and len(content) > self.config.max_msg_length:
            await self.send_text(
                chat_id,
                f"[OneBot] Message too long ({len(content)} chars, max {self.config.max_msg_length}).",
                msg_id=message_id,
                is_group=is_group,
            )
            return

        if content and content.startswith("/"):
            cmd = content.split()[0].lower()
            admin_cmds = ["/stop", "/new", "/restore", "/continue"]
            if cmd in admin_cmds and not is_admin:
                await self.send_text(
                    chat_id,
                    "[OneBot] This command is admin-only.",
                    msg_id=message_id,
                    is_group=is_group,
                )
                return
            await self.handle_command(chat_id, content, msg_id=message_id, is_group=is_group)
            return

        queue = self._get_or_create_queue(user_id)
        if queue.full():
            await self.send_text(
                chat_id,
                f"[OneBot] Queue is full ({self.config.max_queue_size}). Please wait.",
                msg_id=message_id,
                is_group=is_group,
            )
            return

        segments = extract_segments(raw_msg)
        attachments, errors = await self.attachment_manager.download_attachments(segments)
        if errors:
            await self.send_text(
                chat_id,
                "[OneBot] Attachment save failed: " + "; ".join(errors),
                msg_id=message_id,
                is_group=is_group,
            )

        if not content and not attachments:
            return

        sender_nickname = sender.get("nickname", "") or sender.get("card", "") or ""
        at_mentions = extract_at_mentions(raw_msg, self.bot_qq)

        try:
            queue.put_nowait(
                (
                    content,
                    attachments,
                    message_id,
                    chat_id,
                    is_group,
                    is_admin,
                    user_id,
                    sender_nickname,
                    at_mentions,
                )
            )
        except asyncio.QueueFull:
            cleanup_attachments(attachments)
            await self.send_text(
                chat_id,
                f"[OneBot] Queue is full ({self.config.max_queue_size}). Please wait.",
                msg_id=message_id,
                is_group=is_group,
            )
            return

        if user_id not in self.state.processing_users:
            asyncio.create_task(self._process_user_queue(user_id))

    async def handle_event(self, event: dict) -> None:
        post_type = event.get("post_type", "")
        if post_type != "message":
            return
        await self._handle_message_event(event)

    async def connect_and_run(self):
        connect_url = build_ws_connect_url(self.config.ws_url, self.config.access_token)
        display_url = mask_ws_url(connect_url)

        headers = {}
        if self.config.access_token:
            headers["Authorization"] = f"Bearer {self.config.access_token}"
        connect_sig = inspect.signature(websockets.connect)
        header_arg = "additional_headers" if "additional_headers" in connect_sig.parameters else "extra_headers"

        while True:
            try:
                print(f"[OneBot] connecting to {display_url} ...")
                connect_kwargs = {
                    "ping_interval": 20,
                    "ping_timeout": 60,
                    "close_timeout": 10,
                }
                if headers:
                    connect_kwargs[header_arg] = headers
                async with websockets.connect(connect_url, **connect_kwargs) as ws:
                    self.ws = ws
                    print("[OneBot] connected!")

                    heartbeat_task = asyncio.create_task(self._heartbeat())
                    try:
                        async for raw_msg in ws:
                            try:
                                event = json.loads(raw_msg)
                            except json.JSONDecodeError:
                                print(f"[OneBot] invalid JSON: {raw_msg[:200]}")
                                continue

                            if event.get("post_type") == "meta_event":
                                continue

                            if event.get("retcode") == 1403:
                                print(
                                    "[OneBot] token verification failed. Check NapCat access_token and ONEBOT_ACCESS_TOKEN."
                                )
                                await ws.close()
                                break

                            asyncio.create_task(self.handle_event(event))
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
        while True:
            try:
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                break
