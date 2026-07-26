"""send_text 与事件分发路径的测试。

这两条路径是本次重构改动最多的地方，此前完全没有测试：单条消息固定用一个
ws 对象、chat_id 非数字的守卫、拆分后只在首条加引用、发送失败即停、
sender 为 null 的归一化、以及 /llm 的管理员限制。

OneBotApp 依赖父项目 GenericAgent，不在该环境下运行时整个模块跳过。
"""

import asyncio
import json
import shutil
import tempfile
import unittest
from types import SimpleNamespace

try:
    import onebot_app
    from onebot_app import ADMIN_COMMANDS, OneBotApp
    from onebot_state import OneBotState

    IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - 取决于运行环境
    IMPORT_ERROR = exc


class FakeWs:
    """记录发出去的 payload；fail_after 用于模拟中途断开。"""

    def __init__(self, fail_after=None):
        self.sent = []
        self.fail_after = fail_after
        self.closed = False

    async def send(self, raw):
        if self.fail_after is not None and len(self.sent) >= self.fail_after:
            raise ConnectionResetError("connection lost")
        self.sent.append(json.loads(raw))

    async def close(self):
        self.closed = True

    def payloads(self):
        return self.sent

    def segments(self, index):
        message = self.sent[index]["params"]["message"]
        return message if isinstance(message, list) else [message]


def _config(tmp, **overrides):
    base = dict(
        data_dir=tmp,
        max_file_bytes=1024,
        max_queue_size=5,
        context_messages=0,
        admin_set=set(),
        plain_text_hint="",
        access_token="",
        ws_url="ws://127.0.0.1:8080/onebot/v11/ws",
        split_limit=1500,
        local_source_dirs=(),
        attachment_ttl_hours=0,
        allowed_users={"*"},
        allowed_groups={"*"},
        allow_group=True,
        group_require_at=None,
        group_trigger_words=(),
        max_msg_length=500,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


@unittest.skipIf(IMPORT_ERROR is not None, f"GenericAgent unavailable: {IMPORT_ERROR}")
class AppTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="onebot_send_")
        self.logs = []
        self.app = OneBotApp(OneBotState(agent=None, user_tasks={}), self._make_config())
        self.app._emit_log = self.logs.append
        self.ws = FakeWs()
        self.app.ws = self.ws

    def _make_config(self):
        return _config(self.tmp)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def logged(self):
        return "\n".join(self.logs)


class SendTextTest(AppTestCase):
    def test_private_message_targets_user_id(self):
        asyncio.run(self.app.send_text("10001", "hi"))
        payload = self.ws.payloads()[0]
        self.assertEqual(payload["action"], "send_private_msg")
        self.assertEqual(payload["params"]["user_id"], 10001)

    def test_group_message_targets_group_id(self):
        asyncio.run(self.app.send_text("900001", "hi", is_group=True))
        payload = self.ws.payloads()[0]
        self.assertEqual(payload["action"], "send_group_msg")
        self.assertEqual(payload["params"]["group_id"], 900001)

    def test_non_numeric_chat_id_sends_nothing(self):
        asyncio.run(self.app.send_text("not-a-number", "hi"))
        self.assertEqual(self.ws.payloads(), [])
        self.assertIn("invalid chat_id", self.logged())

    def test_none_chat_id_sends_nothing(self):
        asyncio.run(self.app.send_text(None, "hi"))
        self.assertEqual(self.ws.payloads(), [])

    def test_no_connection_sends_nothing(self):
        self.app.ws = None
        asyncio.run(self.app.send_text("10001", "hi"))
        self.assertIn("ws not connected", self.logged())

    def test_blank_and_status_content_is_dropped(self):
        for content in ("", "   ", "思考中...", "LLM Running (Turn 2)..."):
            asyncio.run(self.app.send_text("10001", content))
        self.assertEqual(self.ws.payloads(), [])

    def test_reply_segment_only_on_the_first_chunk(self):
        self.app.split_limit = 20
        asyncio.run(self.app.send_text("10001", "x" * 90, msg_id=555))
        payloads = self.ws.payloads()
        self.assertGreater(len(payloads), 1)
        replies = [
            seg
            for index in range(len(payloads))
            for seg in self.ws.segments(index)
            if seg.get("type") == "reply"
        ]
        self.assertEqual(len(replies), 1)
        self.assertEqual(replies[0]["data"]["id"], "555")
        self.assertEqual(self.ws.segments(0)[0]["type"], "reply")

    def test_at_code_becomes_an_at_segment(self):
        asyncio.run(self.app.send_text("900001", "hi [CQ:at,qq=222] there", is_group=True))
        kinds = [seg["type"] for seg in self.ws.segments(0)]
        self.assertIn("at", kinds)

    def test_send_stops_after_a_failure(self):
        """回归：中途断开时不能继续尝试后续分片，用户只会看到半截消息。"""
        self.app.split_limit = 20
        self.app.ws = FakeWs(fail_after=1)
        asyncio.run(self.app.send_text("10001", "y" * 200))
        self.assertEqual(len(self.app.ws.payloads()), 1)
        self.assertIn("send error", self.logged())

    def test_one_ws_object_is_used_for_the_whole_message(self):
        """回归：重连时逐片重新读 self.ws，曾在中途换连接。"""
        self.app.split_limit = 20
        first = self.ws

        async def scenario():
            task = asyncio.ensure_future(self.app.send_text("10001", "z" * 200))
            await asyncio.sleep(0)
            self.app.ws = FakeWs()  # 模拟重连换了对象
            await task

        asyncio.run(scenario())
        self.assertEqual(self.app.ws.payloads(), [])
        self.assertGreater(len(first.payloads()), 1)

    def test_echo_is_unique_per_chunk(self):
        self.app.split_limit = 20
        asyncio.run(self.app.send_text("10001", "w" * 100))
        echoes = [payload["echo"] for payload in self.ws.payloads()]
        self.assertEqual(len(echoes), len(set(echoes)))


class DispatchTest(AppTestCase):
    def setUp(self):
        super().setUp()
        # 本组只关心「分发是否决定入队」，不让 drain 介入：
        # drain 一旦跑起来就会消费掉消息并按约定回收队列条目。
        self.app._ensure_drain_task = lambda user_id: None

    def _event(self, **overrides):
        event = {
            "post_type": "message",
            "message_type": "private",
            "message_id": 1,
            "self_id": 999,
            "user_id": 10001,
            "sender": {"user_id": 10001, "nickname": "小明"},
            "message": "hello",
        }
        event.update(overrides)
        return event

    def test_non_message_events_are_ignored(self):
        asyncio.run(self.app.handle_event({"post_type": "meta_event"}))
        self.assertEqual(self.ws.payloads(), [])

    def test_null_sender_does_not_crash(self):
        """回归：event.get("sender", {}) 在 sender 显式为 null 时返回 None。"""
        asyncio.run(self.app.handle_event(self._event(sender=None)))
        self.assertIn("10001", self.app.state.user_queues)

    def test_non_dict_sender_does_not_crash(self):
        asyncio.run(self.app.handle_event(self._event(sender="junk")))
        self.assertIn("10001", self.app.state.user_queues)

    def test_missing_user_id_is_ignored(self):
        asyncio.run(self.app.handle_event(self._event(sender={}, user_id=None)))
        self.assertIn("missing user_id", self.logged())

    def test_self_messages_are_ignored(self):
        event = self._event(user_id=999, sender={"user_id": 999, "nickname": "bot"})
        asyncio.run(self.app.handle_event(event))
        self.assertEqual(self.app.state.user_queues, {})

    def test_duplicate_message_id_is_processed_once(self):
        asyncio.run(self.app.handle_event(self._event()))
        queue = self.app.state.user_queues["10001"]
        self.assertEqual(queue.qsize(), 1)
        asyncio.run(self.app.handle_event(self._event()))
        self.assertEqual(queue.qsize(), 1)

    def test_string_and_int_message_ids_are_the_same_message(self):
        asyncio.run(self.app.handle_event(self._event(message_id=7)))
        queue = self.app.state.user_queues["10001"]
        asyncio.run(self.app.handle_event(self._event(message_id="7")))
        self.assertEqual(queue.qsize(), 1)

    def test_overlong_message_is_refused(self):
        asyncio.run(self.app.handle_event(self._event(message="a" * 501)))
        self.assertEqual(self.app.state.user_queues, {})
        self.assertIn("Message too long", json.dumps(self.ws.payloads(), ensure_ascii=False))

    def test_group_message_is_queued_under_the_group_chat_id(self):
        event = self._event(message_type="group", group_id=900001, message_id=2)
        asyncio.run(self.app.handle_event(event))
        item = self.app.state.user_queues["10001"].get_nowait()
        self.assertEqual(item.chat_id, "900001")
        self.assertTrue(item.is_group)

    def test_group_is_ignored_when_disabled(self):
        self.app.config.allow_group = False
        event = self._event(message_type="group", group_id=900001, message_id=3)
        asyncio.run(self.app.handle_event(event))
        self.assertEqual(self.app.state.user_queues, {})

    def test_group_not_in_allowlist_is_ignored(self):
        self.app.config.allowed_groups = {"900002"}
        event = self._event(message_type="group", group_id=900001, message_id=4)
        asyncio.run(self.app.handle_event(event))
        self.assertEqual(self.app.state.user_queues, {})

    def test_unauthorized_user_is_ignored(self):
        self.app.config.allowed_users = {"20002"}
        asyncio.run(self.app.handle_event(self._event()))
        self.assertEqual(self.app.state.user_queues, {})
        self.assertIn("unauthorized user", self.logged())

    def test_group_require_at_blocks_plain_messages(self):
        self.app.config.group_require_at = True
        self.app.bot_qq = "999"
        event = self._event(message_type="group", group_id=900001, message_id=5)
        asyncio.run(self.app.handle_event(event))
        self.assertEqual(self.app.state.user_queues, {})

    def test_group_trigger_word_bypasses_require_at(self):
        self.app.config.group_require_at = True
        self.app.config.group_trigger_words = ("小宇",)
        self.app.bot_qq = "999"
        event = self._event(
            message_type="group", group_id=900001, message_id=6, message="小宇 你好"
        )
        asyncio.run(self.app.handle_event(event))
        self.assertIn("10001", self.app.state.user_queues)


class AdminCommandGateTest(AppTestCase):
    def setUp(self):
        super().setUp()
        self.app._ensure_drain_task = lambda user_id: None

    def _make_config(self):
        return _config(self.tmp, admin_set={"70007"})

    def _command_event(self, command, user_id=10001):
        return {
            "post_type": "message",
            "message_type": "private",
            "message_id": 11,
            "self_id": 999,
            "user_id": user_id,
            "sender": {"user_id": user_id, "nickname": "u"},
            "message": command,
        }

    def test_llm_is_admin_gated(self):
        # /llm 会切换整个进程的 LLM 后端，必须与 /stop 同级
        self.assertIn("/llm", ADMIN_COMMANDS)

    def test_non_admin_is_refused_for_every_admin_command(self):
        for command in sorted(ADMIN_COMMANDS):
            with self.subTest(command=command):
                self.app.ws = FakeWs()
                self.app.state.processed_ids.clear()
                self.app.state.processed_id_set.clear()
                asyncio.run(self.app.handle_event(self._command_event(command)))
                text = json.dumps(self.app.ws.payloads(), ensure_ascii=False)
                self.assertIn("admin-only", text)

    def test_admin_command_gate_is_case_insensitive(self):
        asyncio.run(self.app.handle_event(self._command_event("/STOP")))
        self.assertIn("admin-only", json.dumps(self.ws.payloads(), ensure_ascii=False))

    def test_commands_never_enter_the_queue(self):
        asyncio.run(self.app.handle_event(self._command_event("/stop")))
        self.assertEqual(self.app.state.user_queues, {})


class HistoryTest(AppTestCase):
    def test_history_is_not_collected_when_disabled(self):
        self.app._append_history_message(
            chat_id="10001", is_group=False, sender_qq="10001", sender_nickname="u", content="hi"
        )
        self.assertEqual(self.app._chat_histories, {})

    def test_history_is_collected_when_enabled(self):
        self.app.config.context_messages = 3
        for index in range(2):
            self.app._append_history_message(
                chat_id="10001",
                is_group=False,
                sender_qq="10001",
                sender_nickname="u",
                content=f"m{index}",
            )
        recent = self.app._get_recent_history_messages(
            chat_id="10001", is_group=False, count=3
        )
        self.assertEqual(len(recent), 2)
        self.assertIn("m1", recent[-1])

    def test_history_chat_count_is_bounded(self):
        self.app.config.context_messages = 3
        for index in range(onebot_app.HISTORY_MAX_CHATS + 25):
            self.app._append_history_message(
                chat_id=str(index),
                is_group=False,
                sender_qq="1",
                sender_nickname="u",
                content="x",
            )
        self.assertLessEqual(len(self.app._chat_histories), onebot_app.HISTORY_MAX_CHATS)

    def test_private_and_group_histories_are_separate(self):
        self.app.config.context_messages = 3
        self.app._append_history_message(
            chat_id="1", is_group=False, sender_qq="1", sender_nickname="u", content="private"
        )
        self.app._append_history_message(
            chat_id="1", is_group=True, sender_qq="1", sender_nickname="u", content="group"
        )
        private = self.app._get_recent_history_messages(chat_id="1", is_group=False, count=5)
        group = self.app._get_recent_history_messages(chat_id="1", is_group=True, count=5)
        self.assertEqual(len(private), 1)
        self.assertEqual(len(group), 1)
        self.assertIn("private", private[0])
        self.assertIn("group", group[0])


if __name__ == "__main__":
    unittest.main()
