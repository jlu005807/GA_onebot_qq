import asyncio
import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, Set

MAX_QUEUE_SIZE = 5
MAX_MSG_LENGTH = 500


@dataclass
class OneBotState:
    # 运行期状态：消息去重、队列、序号等
    agent: Any
    user_tasks: Dict[str, Any]
    processed_ids: deque = field(default_factory=lambda: deque(maxlen=2000))
    msg_id_counter: int = 1
    seq_lock: threading.Lock = field(default_factory=threading.Lock)
    user_queues: Dict[str, asyncio.Queue] = field(default_factory=dict)
    processing_users: Set[str] = field(default_factory=set)

    def next_msg_id(self) -> int:
        with self.seq_lock:
            self.msg_id_counter += 1
            return self.msg_id_counter
