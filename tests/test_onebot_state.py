import unittest
from collections import deque

from onebot_state import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_MSG_LENGTH,
    DEFAULT_MAX_QUEUE_SIZE,
    OneBotState,
    QueuedMessage,
)


def _state(**kwargs):
    return OneBotState(agent=None, user_tasks={}, **kwargs)


class DefaultsTest(unittest.TestCase):
    def test_defaults_are_sane(self):
        self.assertGreater(DEFAULT_MAX_QUEUE_SIZE, 0)
        self.assertGreater(DEFAULT_MAX_MSG_LENGTH, 0)
        self.assertGreater(DEFAULT_MAX_FILE_BYTES, 0)

    def test_fresh_state_is_isolated(self):
        first, second = _state(), _state()
        first.user_queues["a"] = object()
        self.assertEqual(second.user_queues, {})


class NextMsgIdTest(unittest.TestCase):
    def test_ids_are_strictly_increasing(self):
        state = _state()
        ids = [state.next_msg_id() for _ in range(5)]
        self.assertEqual(ids, sorted(ids))
        self.assertEqual(len(set(ids)), 5)


class SeenMessageTest(unittest.TestCase):
    def test_first_sighting_is_new(self):
        self.assertFalse(_state().seen_message(1))

    def test_repeat_is_detected(self):
        state = _state()
        self.assertFalse(state.seen_message(1))
        self.assertTrue(state.seen_message(1))

    def test_none_is_never_deduped(self):
        state = _state()
        self.assertFalse(state.seen_message(None))
        self.assertFalse(state.seen_message(None))

    def test_int_and_str_ids_are_the_same_message(self):
        # OneBot 实现可能混用 int / str，归一化后才不会漏判重复
        state = _state()
        self.assertFalse(state.seen_message(12345))
        self.assertTrue(state.seen_message("12345"))

    def test_eviction_keeps_set_and_deque_in_sync(self):
        state = _state(processed_ids=deque(maxlen=3))
        for i in range(3):
            self.assertFalse(state.seen_message(i))
        self.assertFalse(state.seen_message(3))  # 淘汰 "0"

        self.assertEqual(len(state.processed_ids), 3)
        self.assertEqual(len(state.processed_id_set), 3)
        self.assertEqual(set(state.processed_ids), state.processed_id_set)

        # 被淘汰的 id 可以再次出现，不会永久占用内存
        self.assertFalse(state.seen_message(0))
        self.assertTrue(state.seen_message(3))

    def test_set_never_outgrows_deque(self):
        state = _state(processed_ids=deque(maxlen=10))
        for i in range(500):
            state.seen_message(i)
        self.assertEqual(len(state.processed_ids), 10)
        self.assertEqual(len(state.processed_id_set), 10)


class QueuedMessageTest(unittest.TestCase):
    def test_fields_are_addressed_by_name(self):
        item = QueuedMessage(
            content="hi",
            attachments=[],
            message_id=7,
            chat_id="900001",
            is_group=True,
            is_admin=False,
            sender_qq="10001",
            sender_nickname="小明",
        )
        self.assertEqual(item.content, "hi")
        self.assertEqual(item.chat_id, "900001")
        self.assertTrue(item.is_group)
        self.assertFalse(item.is_admin)
        self.assertEqual(item.at_mentions, ())
        self.assertEqual(item.history_messages, ())


if __name__ == "__main__":
    unittest.main()
