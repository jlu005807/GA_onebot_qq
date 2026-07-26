"""每用户队列的所有权协议回归测试。

这些用例覆盖曾经导致「消息静默丢失」的竞态：drain 任务空闲超时后把队列从
state.user_queues 里删掉，而生产者早已持有旧队列对象，投进去便再无人消费。

OneBotApp 依赖父项目 GenericAgent（chatapp_common / agentmain）。不在该环境下
运行时整个模块跳过，其余纯逻辑测试不受影响。
"""

import asyncio
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

    def test_aclose_cancels_outstanding_tasks(self):
        async def scenario():
            async def forever():
                await asyncio.sleep(3600)

            task = self.app._spawn(forever())
            await self.app.aclose()
            self.assertTrue(task.cancelled() or task.done())
            self.assertTrue(self.app._closing)

        asyncio.run(scenario())


if __name__ == "__main__":
    unittest.main()
