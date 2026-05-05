import asyncio
from collections import deque
import inspect
import json
import sys
import uuid
from typing import Any, Deque, Dict, List, Optional

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
    is_transient_status_message,
    is_at_bot,
    match_trigger_word,
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
HISTORY_MAXLEN = 40
HISTORY_TEXT_MAXLEN = 300


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
        self._pending_calls: Dict[str, asyncio.Future] = {}
        self._chat_histories: Dict[str, Deque[str]] = {}

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
        self._emit_log(f"[OneBot] Bot QQ: {self.bot_qq}")
        return True

    async def send_text(self, chat_id, content, *, msg_id=None, is_group=False, **ctx):
        if not self.ws:
            self._emit_log("[OneBot] ws not connected, cannot send")
            return

        content = normalize_outgoing_content(content or "")
        if not content or is_transient_status_message(content):
            return
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
                self._emit_log(f"[OneBot] send error: {exc}")

    def _get_or_create_queue(self, user_id: str) -> asyncio.Queue:
        if user_id not in self.state.user_queues:
            self.state.user_queues[user_id] = asyncio.Queue(maxsize=self.config.max_queue_size)
        return self.state.user_queues[user_id]

    def _history_key(self, chat_id: str, is_group: bool) -> str:
        prefix = "g" if is_group else "p"
        return f"{prefix}:{chat_id}"

    def _get_or_create_history(self, key: str) -> Deque[str]:
        history = self._chat_histories.get(key)
        if history is None:
            history = deque(maxlen=HISTORY_MAXLEN)
            self._chat_histories[key] = history
        return history

    def _append_history_message(
        self,
        *,
        chat_id: str,
        is_group: bool,
        sender_qq: str,
        sender_nickname: str,
        content: str,
    ) -> None:
        text = (content or "").replace("\r", " ").replace("\n", " ").strip()
        if not text:
            return
        if len(text) > HISTORY_TEXT_MAXLEN:
            text = text[:HISTORY_TEXT_MAXLEN] + "..."

        display = sender_nickname.strip() or sender_qq
        line = f"{display}({sender_qq}): {text}" if sender_qq else f"{display}: {text}"

        key = self._history_key(chat_id, is_group)
        history = self._get_or_create_history(key)
        history.append(line)

    def _get_recent_history_messages(
        self, *, chat_id: str, is_group: bool, count: int
    ) -> List[str]:
        if count <= 0:
            return []
        key = self._history_key(chat_id, is_group)
        history = self._chat_histories.get(key)
        if not history:
            return []
        return list(history)[-count:]

    def _log_prompt_preview(self, chat_id, prompt: str, *, is_group: bool, sender_qq: str) -> None:
        one_line = (prompt or "").replace("\r", " ").replace("\n", " | ")
        if len(one_line) > 600:
            one_line = one_line[:600] + "...(truncated)"
        self._emit_log(
            f"[OneBot->GA] chat={chat_id} group={1 if is_group else 0} sender={sender_qq} prompt={one_line}"
        )

    def _emit_log(self, message: str) -> None:
        print(message)
        real_stdout = getattr(sys, "__stdout__", None)
        if real_stdout and real_stdout is not sys.stdout:
            try:
                real_stdout.write(message + "\n")
                real_stdout.flush()
            except Exception:
                pass

    async def _call_action(
        self, action: str, params: Dict[str, Any], timeout: float = 8.0
    ) -> Optional[Dict[str, Any]]:
        if not self.ws:
            return None
        echo = f"call_{uuid.uuid4().hex}"
        loop = asyncio.get_running_loop()
        fut: asyncio.Future = loop.create_future()
        self._pending_calls[echo] = fut
        payload = {"action": action, "params": params, "echo": echo}
        try:
            await self.ws.send(json.dumps(payload))
            result = await asyncio.wait_for(fut, timeout=timeout)
            if isinstance(result, dict):
                return result
            return None
        except Exception:
            return None
        finally:
            self._pending_calls.pop(echo, None)

    def _segment_has_source(self, data: Dict[str, Any]) -> bool:
        url = str(data.get("url", "")).strip()
        if url.startswith("http://") or url.startswith("https://"):
            return True
        path = str(data.get("path", "")).strip()
        if path:
            return True
        file_ref = str(data.get("file", "")).strip()
        if file_ref:
            if file_ref.startswith("http://") or file_ref.startswith("https://"):
                return True
            if file_ref.startswith("base64://") or file_ref.startswith("data:"):
                return True
            if len(file_ref) > 2 and (":\\" in file_ref or file_ref.startswith("/")):
                return True
        base64_data = str(data.get("base64", "")).strip()
        if base64_data:
            return True
        return False

    def _extract_source_fields(self, resp: Optional[Dict[str, Any]]) -> Dict[str, Any]:
        if not isinstance(resp, dict):
            return {}
        if resp.get("status") != "ok":
            return {}
        payload = resp.get("data")
        if isinstance(payload, str):
            return {"file": payload}
        if not isinstance(payload, dict):
            return {}
        source: Dict[str, Any] = {}
        for key in ("url", "file", "path", "file_name", "file_size"):
            value = payload.get(key)
            if value not in (None, ""):
                source[key] = value
        base64_data = payload.get("base64")
        if base64_data:
            source["file"] = f"base64://{base64_data}"
        return source

    async def _hydrate_file_segment(
        self, data: Dict[str, Any], *, is_group: bool, group_id: str, user_id: str
    ) -> Dict[str, Any]:
        if self._segment_has_source(data):
            return data

        identifiers = []
        for key in ("file_id", "id", "fid", "file"):
            value = str(data.get(key, "")).strip()
            if value and value not in identifiers:
                identifiers.append(value)

        # 1) 先拿直链（NapCat 文件处理建议）
        for file_id in identifiers:
            if is_group and group_id:
                group_param_candidates = [{"group_id": group_id}, {"group": group_id}]
                busid = str(data.get("busid", "")).strip()
                for group_params in group_param_candidates:
                    url_params = {"file_id": file_id, **group_params}
                    if busid:
                        url_params["busid"] = busid
                    resp = await self._call_action(
                        "get_group_file_url",
                        url_params,
                        timeout=5.0,
                    )
                    source = self._extract_source_fields(resp)
                    if source:
                        data.update(source)
                        return data
            elif user_id:
                for private_params in ({"file_id": file_id}, {"user_id": user_id, "file_id": file_id}):
                    resp = await self._call_action(
                        "get_private_file_url",
                        private_params,
                        timeout=5.0,
                    )
                    source = self._extract_source_fields(resp)
                    if source:
                        data.update(source)
                        return data

        # 2) 再通过 get_file 拉本地路径或链接
        for file_id in identifiers:
            resp = await self._call_action("get_file", {"file_id": file_id}, timeout=6.0)
            source = self._extract_source_fields(resp)
            if source:
                data.update(source)
                return data
            resp = await self._call_action("get_file", {"file": file_id}, timeout=6.0)
            source = self._extract_source_fields(resp)
            if source:
                data.update(source)
                return data

        return data

    async def _hydrate_attachment_segments(
        self, segments, *, is_group: bool, group_id: str, user_id: str
    ):
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            seg_type = str(seg.get("type", "")).strip()
            if seg_type != "file":
                continue
            data = seg.get("data", {})
            if not isinstance(data, dict):
                continue
            updated = await self._hydrate_file_segment(
                data, is_group=is_group, group_id=group_id, user_id=user_id
            )
            seg["data"] = updated
        return segments

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
                        history_messages,
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
                            include_admin_policy=bool(self.config.admin_set),
                            sender_nickname=sender_nickname,
                            sender_qq=sender_qq,
                            at_mentions=at_mentions,
                            history_messages=history_messages,
                            plain_text_hint=self.config.plain_text_hint,
                        )
                        self._log_prompt_preview(
                            chat_id,
                            prompt,
                            is_group=is_group,
                            sender_qq=sender_qq,
                        )
                        await self.run_agent(
                            chat_id, prompt, msg_id=message_id, is_group=is_group
                        )
                    await asyncio.sleep(1)
                except Exception as exc:
                    self._emit_log(f"[OneBot] queue item error: {exc}")
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
        sender_nickname = sender.get("nickname", "") or sender.get("card", "") or ""

        raw_user_id = sender.get("user_id") or event.get("user_id")
        user_id = str(raw_user_id).strip() if raw_user_id is not None else ""
        group_id = str(event.get("group_id", "")) if is_group else ""
        chat_id = group_id if is_group else user_id

        if not user_id:
            self._emit_log("[OneBot] missing user_id, ignore message")
            return

        # Ignore self messages to prevent loops.
        if self._ensure_bot_qq(event) and user_id == self.bot_qq:
            return

        # Group handling rules.
        if is_group:
            if not self.config.allow_group:
                return
            if not group_id:
                self._emit_log("[OneBot] missing group_id, ignore group message")
                return
            if (
                not public_access(self.config.allowed_groups)
                and group_id not in self.config.allowed_groups
            ):
                return
            if not self._ensure_bot_qq(event):
                self._emit_log("[OneBot] cannot get bot_qq, ignoring group msg")
                return

        is_admin = self._is_admin_user(user_id)
        content = extract_text(raw_msg)

        if not self._is_allowed_user(user_id, is_admin):
            self._emit_log(f"[OneBot] unauthorized user: {user_id}")
            return

        history_messages = self._get_recent_history_messages(
            chat_id=chat_id,
            is_group=is_group,
            count=self.config.context_messages,
        )
        self._append_history_message(
            chat_id=chat_id,
            is_group=is_group,
            sender_qq=user_id,
            sender_nickname=sender_nickname,
            content=content,
        )

        if is_group:
            at_bot = is_at_bot(raw_msg, self.bot_qq)
            hit_word = match_trigger_word(content, self.config.group_trigger_words)
            if self.config.group_require_at is True and not at_bot and not hit_word:
                return
            if hit_word:
                self._emit_log(
                    f"[OneBot] group trigger matched: '{hit_word}' from user={user_id} group={group_id}"
                )

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
        segments = await self._hydrate_attachment_segments(
            segments, is_group=is_group, group_id=group_id, user_id=user_id
        )
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
                    history_messages,
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
                self._emit_log(f"[OneBot] connecting to {display_url} ...")
                connect_kwargs = {
                    "ping_interval": 20,
                    "ping_timeout": 60,
                    "close_timeout": 10,
                }
                if headers:
                    connect_kwargs[header_arg] = headers
                async with websockets.connect(connect_url, **connect_kwargs) as ws:
                    self.ws = ws
                    self._emit_log("[OneBot] connected!")

                    heartbeat_task = asyncio.create_task(self._heartbeat())
                    try:
                        async for raw_msg in ws:
                            try:
                                event = json.loads(raw_msg)
                            except json.JSONDecodeError:
                                self._emit_log(f"[OneBot] invalid JSON: {raw_msg[:200]}")
                                continue

                            echo = event.get("echo")
                            if echo and echo in self._pending_calls:
                                fut = self._pending_calls.get(echo)
                                if fut and not fut.done():
                                    fut.set_result(event)
                                continue

                            if event.get("post_type") == "meta_event":
                                continue

                            if event.get("retcode") == 1403:
                                self._emit_log(
                                    "[OneBot] token verification failed. Check NapCat access_token and ONEBOT_ACCESS_TOKEN."
                                )
                                await ws.close()
                                break

                            asyncio.create_task(self.handle_event(event))
                    finally:
                        heartbeat_task.cancel()
                        for fut in list(self._pending_calls.values()):
                            if fut and not fut.done():
                                fut.set_exception(RuntimeError("websocket disconnected"))
                        self._pending_calls.clear()
                        self.ws = None
            except websockets.exceptions.ConnectionClosed as exc:
                self._emit_log(f"[OneBot] connection closed: {exc}")
            except ConnectionRefusedError:
                self._emit_log("[OneBot] connection refused, is NapCat running?")
            except Exception as exc:
                self._emit_log(f"[OneBot] error: {exc}")

            self._emit_log("[OneBot] reconnect in 5s...")
            await asyncio.sleep(5)

    async def _heartbeat(self):
        while True:
            try:
                await asyncio.sleep(15)
            except asyncio.CancelledError:
                break
