import re
from typing import Any, Dict, List, Sequence


STATUS_ONLY_PATTERNS = (
    re.compile(r"^思考中[.。…]*$"),
    re.compile(r"^⏳\s*还在处理中，请稍等[.。…]*$"),
    re.compile(r"^还在处理中，请稍等[.。…]*$"),
)
LLM_RUNNING_RE = re.compile(
    r"^(?:[^\w\s]*\s*)?LLM\s+Running\s*\(Turn\s+\d+\)\s*(?:\.{3}|…)$",
    re.IGNORECASE,
)
TOOL_CALL_RE = re.compile(
    r"^(?:[^\w\s]*\s*)?"
    r"(?:(?:code_run|file_read|file_write|file_patch|shell_command|apply_patch)"
    r"|(?:(?:functions|web|multi_tool_use)\.(?:shell_command|apply_patch|run|parallel)))"
    r"\s*\(",
)
CODE_FENCE_RE = re.compile(r"^```[\w.+-]*\s*$")
CQ_AT_RE = re.compile(r"\[CQ:at,qq=(\d+)\]")
CQ_SEGMENT_RE = re.compile(r"\[CQ:[^\]]+\]")


def _is_status_line(text: str) -> bool:
    stripped = (text or "").strip()
    if not stripped:
        return False
    if LLM_RUNNING_RE.match(stripped):
        return True
    return any(pattern.match(stripped) for pattern in STATUS_ONLY_PATTERNS)


def _is_tool_block_end(text: str) -> bool:
    stripped = (text or "").strip().rstrip(";")
    return stripped.endswith((")", "})", "])"))


def normalize_outgoing_content(content: str) -> str:
    if not content:
        return ""
    text = content.replace("\r\n", "\n")
    lines = text.split("\n")

    filtered: List[str] = []
    in_tool_block = False
    for line in lines:
        stripped = line.strip()

        # Drop assistant status/progress templates wherever they appear.
        if _is_status_line(stripped):
            continue

        # Drop tool-call templates such as:
        # "code_run({...})", "file_read({...})", or lines prefixed with symbols.
        if not in_tool_block and TOOL_CALL_RE.match(stripped):
            in_tool_block = True
            if _is_tool_block_end(stripped):
                in_tool_block = False
            continue
        if in_tool_block:
            if _is_tool_block_end(stripped):
                in_tool_block = False
            continue

        # Remove markdown code-fence markers.
        if CODE_FENCE_RE.match(stripped):
            continue

        filtered.append(line)

    # Collapse excessive blank lines and trim.
    compact: List[str] = []
    previous_blank = False
    for line in filtered:
        is_blank = line.strip() == ""
        if is_blank and previous_blank:
            continue
        compact.append(line)
        previous_blank = is_blank

    return "\n".join(compact).strip()


def is_transient_status_message(content: str) -> bool:
    text = (content or "").strip()
    if not text:
        return True
    return _is_status_line(text)


def parse_send_content(text: str) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    last_end = 0
    for match in CQ_AT_RE.finditer(text or ""):
        if match.start() > last_end:
            pre_text = text[last_end : match.start()]
            if pre_text:
                segments.append({"type": "text", "data": {"text": pre_text}})
        segments.append({"type": "at", "data": {"qq": match.group(1)}})
        last_end = match.end()
    if last_end < len(text or ""):
        remaining = text[last_end:]
        if remaining:
            segments.append({"type": "text", "data": {"text": remaining}})
    return segments


def is_at_bot(raw_msg: Any, bot_qq: str) -> bool:
    if isinstance(raw_msg, str):
        return f"[CQ:at,qq={bot_qq}]" in raw_msg
    if isinstance(raw_msg, list):
        for seg in raw_msg:
            if isinstance(seg, dict) and seg.get("type") == "at":
                if str(seg.get("data", {}).get("qq", "")) == bot_qq:
                    return True
    return False


def extract_text(raw_msg: Any) -> str:
    if isinstance(raw_msg, str):
        content = raw_msg
    elif isinstance(raw_msg, list):
        parts: List[str] = []
        for seg in raw_msg:
            if isinstance(seg, dict) and seg.get("type") == "text":
                parts.append(str(seg.get("data", {}).get("text", "")))
            elif isinstance(seg, str):
                parts.append(seg)
        content = "".join(parts)
    else:
        content = str(raw_msg)

    if "[CQ:" in content:
        content = CQ_SEGMENT_RE.sub("", content)
    return content.strip()


def match_trigger_word(content: str, trigger_words: Sequence[str]) -> str:
    text = (content or "").strip()
    if not text or not trigger_words:
        return ""
    lowered = text.casefold()
    for word in trigger_words:
        normalized = str(word or "").strip()
        if not normalized:
            continue
        if normalized.casefold() in lowered:
            return normalized
    return ""


def extract_at_mentions(raw_msg: Any, bot_qq: str) -> List[str]:
    mentions: List[str] = []
    if isinstance(raw_msg, list):
        for seg in raw_msg:
            if isinstance(seg, dict) and seg.get("type") == "at":
                qq = str(seg.get("data", {}).get("qq", ""))
                if qq and qq != bot_qq and qq != "all":
                    mentions.append(qq)
    elif isinstance(raw_msg, str):
        for match in CQ_AT_RE.finditer(raw_msg):
            qq = match.group(1)
            if qq != bot_qq:
                mentions.append(qq)
    # de-dup while keeping order
    return list(dict.fromkeys(mentions))


def extract_segments(raw_msg: Any) -> List[Dict[str, Any]]:
    if isinstance(raw_msg, list):
        return [seg for seg in raw_msg if isinstance(seg, dict)]
    if isinstance(raw_msg, str) and "[CQ:" in raw_msg:
        return _parse_cq_segments(raw_msg)
    return []


def _parse_cq_segments(raw_msg: str) -> List[Dict[str, Any]]:
    segments: List[Dict[str, Any]] = []
    for code in CQ_SEGMENT_RE.findall(raw_msg):
        seg = _parse_cq_segment(code)
        if seg:
            segments.append(seg)
    return segments


def _parse_cq_segment(code: str) -> Dict[str, Any]:
    if not code.startswith("[CQ:") or not code.endswith("]"):
        return {}
    inner = code[4:-1]
    if not inner:
        return {}
    parts = inner.split(",")
    seg_type = parts[0].strip()
    data: Dict[str, str] = {}
    for part in parts[1:]:
        if "=" in part:
            key, value = part.split("=", 1)
            data[key.strip()] = value.strip()
    return {"type": seg_type, "data": data}


def format_attachments(attachments: Sequence[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for idx, item in enumerate(attachments, start=1):
        size_kb = max(1, int(item.get("size", 0)) // 1024)
        lines.append(
            f"attachment{idx}: type={item.get('type')} path={item.get('path')} size={size_kb}KB"
        )
    return "\n".join(lines)


def build_agent_prompt(
    content: str,
    attachments: Sequence[Dict[str, Any]],
    *,
    is_group: bool,
    is_admin: bool,
    include_admin_policy: bool = True,
    sender_nickname: str = "",
    sender_qq: str = "",
    at_mentions: Sequence[str] = (),
    history_messages: Sequence[str] = (),
    plain_text_hint: str = "",
) -> str:
    context_line = f"context: group={1 if is_group else 0}"
    if include_admin_policy:
        context_line += f" admin={1 if is_admin else 0}"
    parts: List[str] = [context_line]
    parts.append(
        "output_rules: You are replying in QQ chat. Use plain text only; do NOT use markdown and please use Chinese. "
        "syntax, code fences, or tool-call templates (for example code_run(...), file_read(...), file_patch(...), "
        "or lines like LLM Running (Turn N) ...)."
    )
    if sender_qq:
        sender_line = f"sender_qq: {sender_qq}"
        if sender_nickname:
            sender_line += f" nickname: {sender_nickname}"
        parts.append(sender_line)
    if at_mentions:
        parts.append(f"mentioned_qq: {', '.join(at_mentions)}")
        parts.append("tip: if you need to @ someone, use [CQ:at,qq=<qq>].")
    if history_messages:
        parts.append("recent_messages:\n" + "\n".join(history_messages))
    if content:
        parts.append(content)
    if attachments:
        parts.append("attachments:\n" + format_attachments(attachments))
    if plain_text_hint:
        parts.append(plain_text_hint)

    if include_admin_policy:
        if is_admin:
            parts.append(
                "policy: user is admin. File/process/hardware operations are allowed with risk notice."
            )
        else:
            parts.append(
                "policy: user is NOT admin. File/process/hardware operations are forbidden. "
                "Reject requests like directory operations, process control, and screen/CPU changes."
            )
    return "\n\n".join(parts)
