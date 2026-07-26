"""每用户队列的所有权协议回归测试。

这些用例覆盖曾经导致「消息静默丢失」的竞态：drain 任务空闲超时后把队列从
state.user_queues 里删掉，而生产者早已持有旧队列对象，投进去便再无人消费。

OneBotApp 依赖父项目 GenericAgent（chatapp_common / agentmain）。不在该环境下
运行时整个模块跳过，其余纯逻辑测试不受影响。
"""

import asyncio
import contextlib
import io
import os
import shutil
import tempfile
import unittest
from types import SimpleNamespace

try:
    import onebot_app
    from onebot_app import OneBotApp
    from onebot_state import OneBotState, QueuedMessage

    IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - 取决于运行环境
    IMPORT_ERROR = exc


def _make_item(content="hi", user_id="10001"):
    return QueuedMessage(
        content=content,
        attachments=[],
        message_id=1,
        chat_id=user_id,
        is_group=False,
        is_admin=False,
        sender_qq=user_id,
        sender_nickname="",
    )


@unittest.skipIf(IMPORT_ERROR is not None, f"GenericAgent unavailable: {IMPORT_ERROR}")
class UserQueueOwnershipTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="onebot_app_test_")
        self._idle = onebot_app.QUEUE_IDLE_SECONDS
        self._interval = onebot_app.QUEUE_ITEM_INTERVAL_SECONDS
        onebot_app.QUEUE_IDLE_SECONDS = 0.05
        onebot_app.QUEUE_ITEM_INTERVAL_SECONDS = 0

        config = SimpleNamespace(
            data_dir=self.tmp,
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
        )
        self.state = OneBotState(agent=None, user_tasks={})
        self.app = OneBotApp(self.state, config)

        self.handled = []

        async def _record(item):
            self.handled.append(item)

        self.app._handle_queue_item = _record

    def tearDown(self):
        onebot_app.QUEUE_IDLE_SECONDS = self._idle
        onebot_app.QUEUE_ITEM_INTERVAL_SECONDS = self._interval
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _enqueue(self, user_id, item):
        """复刻生产者路径：取队列 -> 入队 -> 确保 drain（三步之间没有 await）。"""
        queue = self.app._get_or_create_queue(user_id)
        queue.put_nowait(item)
        self.app._ensure_drain_task(user_id)

    def test_item_is_consumed(self):
        async def scenario():
            self._enqueue("10001", _make_item("hello"))
            await asyncio.wait_for(self.state.user_drains["10001"], timeout=5)

        asyncio.run(scenario())
        self.assertEqual([i.content for i in self.handled], ["hello"])

    def test_drain_releases_ownership_when_idle(self):
        async def scenario():
            self._enqueue("10001", _make_item())
            await asyncio.wait_for(self.state.user_drains["10001"], timeout=5)

        asyncio.run(scenario())
        self.assertNotIn("10001", self.state.user_drains)
        self.assertNotIn("10001", self.state.user_queues)

    def test_message_after_release_is_not_lost(self):
        """回归：drain 释放所有权后紧接着到达的消息必须由新 drain 消费。"""

        async def scenario():
            self._enqueue("10001", _make_item("first"))
            await asyncio.wait_for(self.state.user_drains["10001"], timeout=5)
            self.assertNotIn("10001", self.state.user_queues)

            self._enqueue("10001", _make_item("second"))
            await asyncio.wait_for(self.state.user_drains["10001"], timeout=5)

        asyncio.run(scenario())
        self.assertEqual([i.content for i in self.handled], ["first", "second"])

    def test_only_one_drain_task_per_user(self):
        """回归：连续两次投递不能各起一个 drain，否则会并行消费同一队列。"""

        async def scenario():
            self._enqueue("10001", _make_item("a"))
            first = self.state.user_drains["10001"]
            self._enqueue("10001", _make_item("b"))
            second = self.state.user_drains["10001"]
            self.assertIs(first, second)
            await asyncio.wait_for(first, timeout=5)

        asyncio.run(scenario())
        self.assertEqual([i.content for i in self.handled], ["a", "b"])

    def test_pending_item_keeps_drain_alive(self):
        """队列非空时 drain 不得注销所有权，否则积压消息无人处理。"""

        async def scenario():
            self._enqueue("10001", _make_item("a"))
            self._enqueue("10001", _make_item("b"))
            self._enqueue("10001", _make_item("c"))
            await asyncio.wait_for(self.state.user_drains["10001"], timeout=5)

        asyncio.run(scenario())
        self.assertEqual([i.content for i in self.handled], ["a", "b", "c"])

    def test_release_drain_refuses_while_queue_is_not_empty(self):
        """不变式：队列非空时绝不交还所有权。"""

        async def scenario():
            queue = self.app._get_or_create_queue("10001")
            queue.put_nowait(_make_item("stuck"))
            sentinel = object()
            self.state.user_drains["10001"] = sentinel

            self.assertFalse(self.app._release_drain("10001", sentinel, queue))
            self.assertIs(self.state.user_drains["10001"], sentinel)
            self.assertIn("10001", self.state.user_queues)

            queue.get_nowait()
            self.assertTrue(self.app._release_drain("10001", sentinel, queue))
            self.assertNotIn("10001", self.state.user_drains)
            self.assertNotIn("10001", self.state.user_queues)

        asyncio.run(scenario())

    def test_non_owner_cannot_release_anything(self):
        """回归：非持有者曾能删掉在跑 drain 的队列，留下「有 drain 没队列」的半状态。"""

        async def scenario():
            queue = self.app._get_or_create_queue("10001")
            owner, other = object(), object()
            self.state.user_drains["10001"] = owner

            self.assertFalse(self.app._release_drain("10001", other, queue))
            self.assertIs(self.state.user_drains["10001"], owner)
            self.assertIs(self.state.user_queues["10001"], queue)

        asyncio.run(scenario())

    def test_release_refuses_when_queue_is_not_the_registered_one(self):
        """回归：drain 持有的队列已被换掉时不能再释放，否则会删掉别人的新队列。"""

        async def scenario():
            stale = asyncio.Queue()
            fresh = self.app._get_or_create_queue("10001")
            owner = object()
            self.state.user_drains["10001"] = owner

            self.assertFalse(self.app._release_drain("10001", owner, stale))
            self.assertIs(self.state.user_queues["10001"], fresh)
            self.assertIs(self.state.user_drains["10001"], owner)

        asyncio.run(scenario())

    def test_drain_exit_leaves_both_dicts_consistent(self):
        """任何退出路径后都不应留下「有队列没 drain」。"""

        async def scenario():
            self._enqueue("10001", _make_item("a"))
            await asyncio.wait_for(self.state.user_drains["10001"], timeout=5)

        asyncio.run(scenario())
        self.assertNotIn("10001", self.state.user_drains)
        self.assertNotIn("10001", self.state.user_queues)

    def test_separate_users_get_separate_drains(self):
        async def scenario():
            self._enqueue("10001", _make_item("a", "10001"))
            self._enqueue("20002", _make_item("b", "20002"))
            self.assertIsNot(self.state.user_drains["10001"], self.state.user_drains["20002"])
            await asyncio.wait_for(
                asyncio.gather(self.state.user_drains["10001"], self.state.user_drains["20002"]),
                timeout=5,
            )

        asyncio.run(scenario())
        self.assertEqual(sorted(i.content for i in self.handled), ["a", "b"])

    def test_handler_exception_does_not_kill_the_drain(self):
        async def boom(item):
            self.handled.append(item)
            if item.content == "bad":
                raise RuntimeError("handler failed")

        self.app._handle_queue_item = boom

        async def scenario():
            self._enqueue("10001", _make_item("bad"))
            self._enqueue("10001", _make_item("good"))
            await asyncio.wait_for(self.state.user_drains["10001"], timeout=5)

        asyncio.run(scenario())
        self.assertEqual([i.content for i in self.handled], ["bad", "good"])


@unittest.skipIf(IMPORT_ERROR is not None, f"GenericAgent unavailable: {IMPORT_ERROR}")
class BackgroundTaskTrackingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="onebot_app_test_")
        config = SimpleNamespace(
            data_dir=self.tmp,
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
        )
        self.app = OneBotApp(OneBotState(agent=None, user_tasks={}), config)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_spawned_task_is_referenced_then_released(self):
        async def scenario():
            async def work():
                await asyncio.sleep(0)

            task = self.app._spawn(work())
            # 运行期间必须持有强引用，否则可能被 GC 中途回收
            self.assertIn(task, self.app._bg_tasks)
            await task
            await asyncio.sleep(0)
            self.assertNotIn(task, self.app._bg_tasks)

        asyncio.run(scenario())

    def test_failing_task_is_logged_not_swallowed(self):
        logs = []
        self.app._emit_log = logs.append

        async def scenario():
            async def boom():
                raise RuntimeError("kaboom")

            task = self.app._spawn(boom())
            await asyncio.gather(task, return_exceptions=True)
            await asyncio.sleep(0)

        asyncio.run(scenario())
        self.assertTrue(any("kaboom" in line for line in logs), logs)

    def _emit_and_capture(self, message):
        """走真正的 _emit_log，而不是替换掉它——要验证的正是「日志出口有没有接上脱敏」。"""
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            self.app._emit_log(message)
        return buffer.getvalue()

    def test_real_log_sink_redacts_token(self):
        self.app.config.access_token = "s3cr3t/tok en"
        self.app._secrets = None
        for line in (
            "connect failed for ws://h/p?access_token=s3cr3t/tok en",
            "url encoded: s3cr3t%2Ftok%20en",
            "form encoded: s3cr3t%2Ftok+en",
        ):
            self.assertNotIn("s3cr3t", self._emit_and_capture(line))

    def test_real_log_sink_redacts_token_carried_in_ws_url(self):
        # 令牌也可以直接写在 ONEBOT_WS_URL 里，此时 access_token 是空的
        self.app.config.access_token = ""
        self.app.config.ws_url = "ws://h:1/p?access_token=urltok123"
        self.app._secrets = None
        output = self._emit_and_capture("InvalidStatus for ws://h:1/p?access_token=urltok123")
        self.assertNotIn("urltok123", output)
        self.assertIn("***", output)

    def test_real_log_sink_passes_through_without_token(self):
        self.app.config.access_token = ""
        self.app.config.ws_url = "ws://h:1/p"
        self.app._secrets = None
        self.assertIn("plain message", self._emit_and_capture("plain message"))

    def test_aclose_cancels_tasks_that_outlast_the_grace_period(self):
        async def scenario():
            async def forever():
                await asyncio.sleep(3600)

            task = self.app._spawn(forever())
            # 加超时保护：aclose 若不再取消，这里必须失败而不是把整个套件挂死
            await asyncio.wait_for(self.app.aclose(grace_seconds=0.05), timeout=5)
            self.assertTrue(task.cancelled())
            self.assertTrue(self.app._closing)

        asyncio.run(scenario())

    def test_aclose_lets_a_finishing_task_converge(self):
        """回归：aclose 曾直接取消在途任务，把已经算完的回答打断在发送之前。"""
        finished = []

        async def scenario():
            async def short():
                await asyncio.sleep(0.05)
                finished.append("done")

            task = self.app._spawn(short())
            await asyncio.wait_for(self.app.aclose(grace_seconds=5), timeout=10)
            self.assertFalse(task.cancelled())

        asyncio.run(scenario())
        self.assertEqual(finished, ["done"])

    def test_aclose_cleans_up_attachments_of_dropped_messages(self):
        async def scenario():
            path = os.path.join(self.tmp, "queued.jpg")
            with open(path, "wb") as handle:
                handle.write(b"x")
            item = _make_item("with file")
            item.attachments = [{"type": "image", "path": path, "size": 1}]
            self.app._get_or_create_queue("10001").put_nowait(item)

            await asyncio.wait_for(self.app.aclose(grace_seconds=0), timeout=5)
            self.assertFalse(os.path.exists(path), "停机丢弃的消息附件必须删掉")

        asyncio.run(scenario())

    def test_request_close_is_synchronous_and_idempotent(self):
        async def scenario():
            self.app.request_close()
            self.assertTrue(self.app._closing)
            self.app.request_close()

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
