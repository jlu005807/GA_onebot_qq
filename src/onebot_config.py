import os
from dataclasses import dataclass
from typing import Dict, Set

from onebot_paths import setup_sys_path

GA_ROOT = setup_sys_path()

from llmcore import mykeys
from onebot_state import (
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_MSG_LENGTH,
    DEFAULT_MAX_QUEUE_SIZE,
)


def _read_dotenv(path: str) -> Dict[str, str]:
    # 仅解析本地 .env，不写入环境变量
    data: Dict[str, str] = {}
    if not os.path.exists(path):
        return data
    with open(path, "r", encoding="utf-8") as handle:
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
            data[key] = value
    return data


_DOTENV = _read_dotenv(os.path.join(GA_ROOT, ".env"))


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


def _resolve_path(path: str) -> str:
    if not path:
        return GA_ROOT
    if os.path.isabs(path):
        return path
    return os.path.join(GA_ROOT, path)


@dataclass(frozen=True)
class OneBotConfig:
    ws_url: str
    admin_set: Set[str]
    allowed_users: Set[str]
    access_token: str
    plain_text_hint: str
    max_queue_size: int
    max_msg_length: int
    data_dir: str
    max_file_bytes: int
    lock_port: int
    log_file: str


def load_config() -> OneBotConfig:
    # ws_url 和 allowed_users 允许从 mykeys 兜底
    ws_url = str(
        _get_env(
            "ONEBOT_WS_URL",
            mykeys.get("onebot_ws_url", "") or "ws://127.0.0.1:8080/onebot/v11/ws",
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

    return OneBotConfig(
        ws_url=ws_url,
        admin_set=admin_set,
        allowed_users=allowed_users,
        access_token=access_token,
        plain_text_hint=plain_text_hint,
        max_queue_size=max_queue_size,
        max_msg_length=max_msg_length,
        data_dir=data_dir,
        max_file_bytes=max_file_bytes,
        lock_port=19529,
        log_file="onebot.log",
    )
