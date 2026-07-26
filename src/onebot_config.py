import os
from dataclasses import dataclass
from typing import Dict, Optional, Set, Tuple

from onebot_state import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_MSG_LENGTH,
    DEFAULT_MAX_QUEUE_SIZE,
)

MAX_CONTEXT_MESSAGES = 20
DEFAULT_LOCK_PORT = 19529
DEFAULT_LOG_FILE = "onebot.log"
DEFAULT_LOG_KEEP = 10
DEFAULT_ATTACHMENT_TTL_HOURS = 24
DEFAULT_SPLIT_LIMIT = 1500

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read_dotenv(path: str) -> Dict[str, str]:
    # 仅解析本地 .env，不写入环境变量
    data: Dict[str, str] = {}
    if not os.path.exists(path):
        return data
    # utf-8-sig：Windows 记事本存出的 .env 带 BOM，否则首个键名会多出
    with open(path, "r", encoding="utf-8-sig") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[len("export ") :].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key:
                continue
            if value and value[0] == value[-1] and value[0] in ("'", '"'):
                value = value[1:-1]
            else:
                # 未加引号时支持行尾注释：ONEBOT_X=1  # 说明
                hash_pos = value.find("#")
                if hash_pos > 0 and value[hash_pos - 1] in (" ", "\t"):
                    value = value[:hash_pos].strip()
                elif hash_pos == 0:
                    value = ""
            data[key] = value
    return data


_DOTENV = _read_dotenv(os.path.join(PROJECT_ROOT, ".env"))


def _mykeys() -> Dict[str, object]:
    """延迟读取 GA 的 mykeys。

    放在函数里而不是模块顶层导入，这样本模块可以在没有父项目
    GenericAgent 的环境下被直接导入和单测。
    """
    try:
        from onebot_paths import setup_sys_path

        setup_sys_path()
        from llmcore import mykeys

        return mykeys
    except Exception:
        return {}


def _get_env(key: str, default: str = "") -> str:
    # 优先环境变量，其次 .env，最后默认值
    if os.environ.get(key):
        return os.environ.get(key, "")
    if key in _DOTENV:
        return _DOTENV[key]
    return default


def _parse_set(value: str) -> Set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def _parse_int(value: str, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _parse_bool(value: str, default: bool) -> bool:
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if not normalized:
        return default
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _parse_optional_bool(value: str) -> Optional[bool]:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return None
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _parse_non_negative_int(value: str, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    if parsed < 0:
        return default
    return parsed


def _parse_csv_tuple(value: str) -> Tuple[str, ...]:
    return tuple(item.strip() for item in str(value or "").split(",") if item.strip())


def _resolve_path(path: str, base_dir: str = PROJECT_ROOT) -> str:
    """相对路径基于项目根目录解析。

    此前基于 os.getcwd()，从别的目录启动就会把 data/ 撒到别处，
    而 README 与日志都按项目内路径描述，容易让人以为附件没落盘。
    """
    if not path:
        return base_dir
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(base_dir, path))


@dataclass(frozen=True)
class OneBotConfig:
    ws_url: str
    admin_set: Set[str]
    allowed_users: Set[str]
    allow_group: bool
    allowed_groups: Set[str]
    group_require_at: Optional[bool]
    group_trigger_words: Tuple[str, ...]
    context_messages: int
    access_token: str
    plain_text_hint: str
    max_queue_size: int
    max_msg_length: int
    data_dir: str
    max_file_bytes: int
    lock_port: int
    log_file: str
    log_keep: int
    attachment_ttl_hours: int
    split_limit: int
    local_source_dirs: Tuple[str, ...]


def load_config() -> OneBotConfig:
    # ws_url 和 allowed_users 允许从 mykeys 兜底
    mykeys = _mykeys()
    ws_url = str(
        _get_env(
            "ONEBOT_WS_URL",
            mykeys.get("onebot_ws_url", "") or "ws://127.0.0.1:8080",
        )
    ).strip()

    admin_qq = str(_get_env("ONEBOT_ADMIN_QQ", "")).strip()
    access_token = str(_get_env("ONEBOT_ACCESS_TOKEN", "")).strip()
    plain_text_hint = str(
        _get_env("ONEBOT_PLAIN_TEXT_HINT", "请用纯文本回复，不要使用Markdown格式。")
    ).strip()
    if plain_text_hint.lower() in {"0", "false", "off", "disable", "disabled"}:
        plain_text_hint = ""

    allowed_raw = str(_get_env("ONEBOT_ALLOWED_USERS", "")).strip()
    if allowed_raw:
        allowed_users = _parse_set(allowed_raw)
    else:
        allowed_users = {
            str(item).strip()
            for item in mykeys.get("onebot_allowed_users", mykeys.get("qq_allowed_users", ["*"]))
            if str(item).strip()
        }

    admin_set = _parse_set(admin_qq) if admin_qq else set()
    allow_group = _parse_bool(_get_env("ONEBOT_ALLOW_GROUP", ""), True)
    allowed_groups_raw = str(_get_env("ONEBOT_ALLOWED_GROUPS", "*")).strip()
    allowed_groups = _parse_set(allowed_groups_raw) if allowed_groups_raw else {"*"}
    group_require_at = _parse_optional_bool(_get_env("ONEBOT_GROUP_REQUIRE_AT", ""))
    group_trigger_words = _parse_csv_tuple(_get_env("ONEBOT_GROUP_TRIGGER_WORDS", ""))
    context_messages = _parse_non_negative_int(_get_env("ONEBOT_CONTEXT_MESSAGES", "0"), 0)
    context_messages = min(context_messages, MAX_CONTEXT_MESSAGES)
    max_queue_size = _parse_int(
        _get_env("ONEBOT_MAX_QUEUE_SIZE", ""), DEFAULT_MAX_QUEUE_SIZE
    )
    max_msg_length = _parse_int(
        _get_env("ONEBOT_MAX_MSG_LENGTH", ""), DEFAULT_MAX_MSG_LENGTH
    )
    data_dir = _resolve_path(_get_env("ONEBOT_DATA_DIR", "data").strip())
    max_file_bytes = _parse_int(
        _get_env("ONEBOT_MAX_FILE_BYTES", ""), DEFAULT_MAX_FILE_BYTES
    )
    lock_port = _parse_int(_get_env("ONEBOT_LOCK_PORT", ""), DEFAULT_LOCK_PORT)
    log_file = str(_get_env("ONEBOT_LOG_FILE", DEFAULT_LOG_FILE)).strip() or DEFAULT_LOG_FILE
    log_keep = _parse_non_negative_int(_get_env("ONEBOT_LOG_KEEP", ""), DEFAULT_LOG_KEEP)
    attachment_ttl_hours = _parse_non_negative_int(
        _get_env("ONEBOT_ATTACHMENT_TTL_HOURS", ""), DEFAULT_ATTACHMENT_TTL_HOURS
    )
    split_limit = _parse_int(_get_env("ONEBOT_SPLIT_LIMIT", ""), DEFAULT_SPLIT_LIMIT)
    local_source_dirs = tuple(
        _resolve_path(item) for item in _parse_csv_tuple(_get_env("ONEBOT_LOCAL_SOURCE_DIRS", ""))
    )

    return OneBotConfig(
        ws_url=ws_url,
        admin_set=admin_set,
        allowed_users=allowed_users,
        allow_group=allow_group,
        allowed_groups=allowed_groups,
        group_require_at=group_require_at,
        group_trigger_words=group_trigger_words,
        context_messages=context_messages,
        access_token=access_token,
        plain_text_hint=plain_text_hint,
        max_queue_size=max_queue_size,
        max_msg_length=max_msg_length,
        data_dir=data_dir,
        max_file_bytes=max_file_bytes,
        lock_port=lock_port,
        log_file=log_file,
        log_keep=log_keep,
        attachment_ttl_hours=attachment_ttl_hours,
        split_limit=split_limit,
        local_source_dirs=local_source_dirs,
    )
