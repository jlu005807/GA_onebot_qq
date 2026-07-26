import asyncio
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Deque, Dict, List, Optional, Sequence, Set

DEFAULT_MAX_QUEUE_SIZE = 5
DEFAULT_MAX_MSG_LENGTH = 500
DEFAULT_MAX_FILE_BYTES = 10 * 1024 * 1024
DEFAULT_PROCESSED_ID_CACHE = 2000


@dataclass
class QueuedMessage:
    """一条排队待交给 Agent 处理的消息（替代此前的 10 元组，避免位置错位）。"""

    content: str
    attachments: List[Dict[str, Any]]
    message_id: Any
    chat_id: str
    is_group: bool
    is_admin: bool
    sender_qq: str
    sender_nickname: str
    at_mentions: Sequence[str] = ()
    history_messages: Sequence[str] = ()


@dataclass
class OneBotState:
    # 运行期状态：消息去重、队列、序号等
    agent: Any
    user_tasks: Dict[str, Any]
    processed_ids: Deque[str] = field(
        default_factory=lambda: deque(maxlen=DEFAULT_PROCESSED_ID_CACHE)
    )
    processed_id_set: Set[str] = field(default_factory=set)
    msg_id_counter: int = 1
    seq_lock: threading.Lock = field(default_factory=threading.Lock)
    user_queues: Dict[str, asyncio.Queue] = field(default_factory=dict)
    # user_id -> 正在消费该用户队列的 drain 任务；建任务时同步登记，避免重复启动
    user_drains: Dict[str, "asyncio.Task[None]"] = field(default_factory=dict)

    def next_msg_id(self) -> int:
        with self.seq_lock:
            self.msg_id_counter += 1
            return self.msg_id_counter

    def seen_message(self, message_id: Optional[Any]) -> bool:
        """记录并判断消息是否已处理过。

        统一转成字符串再比对，避免 OneBot 实现混用 int/str message_id 时去重失效；
        用 set 做 O(1) 成员判断，deque 只负责维持淘汰顺序。
        """
        if message_id is None:
            return False
        key = str(message_id)
        if key in self.processed_id_set:
            return True
        maxlen = self.processed_ids.maxlen
        if maxlen is not None and len(self.processed_ids) >= maxlen:
            self.processed_id_set.discard(self.processed_ids[0])
        self.processed_ids.append(key)
        self.processed_id_set.add(key)
        return False
