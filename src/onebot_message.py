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
    r"|(?:web_(?:scan|search|open|click|fetch|find|query|screenshot))"
    r"|(?:(?:functions|web|multi_tool_use)\.(?:shell_command|apply_patch|run|parallel)))"
    r"\s*\(",
)
CODE_FENCE_RE = re.compile(r"^```[\w.+-]*\s*$")
TOOL_BLOCK_MAX_LINES = 40
CQ_AT_RE = re.compile(r"\[CQ:at,qq=(\d+)\]")
CQ_SEGMENT_RE = re.compile(r"\[CQ:[^\]]+\]")


def unescape_cq(text: str, *, in_segment: bool = False) -> str:
    """还原 OneBot v11 的 CQ 转义。

    纯文本中 ``&`` ``[`` ``]`` 会被转义；CQ 段的参数值里 ``,`` 也会被转义。
    必须最后再还原 ``&amp;``，否则 ``&amp;#91;`` 会被二次解码成 ``[``。
    """
    if not text or "&" not in text:
        return text or ""
    result = text.replace("&#91;", "[").replace("&#93;", "]")
    if in_segment:
        result = result.replace("&#44;", ",")
    return result.replace("&amp;", "&")


def escape_cq(text: str, *, in_segment: bool = False) -> str:
    """按 OneBot v11 规则转义，供构造 CQ 字符串时使用（``&`` 必须最先处理）。"""
    if not text:
        return ""
    result = text.replace("&", "&amp;").replace("[", "&#91;").replace("]", "&#93;")
    if in_segment:
        result = result.replace(",", "&#44;")
    return result


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
    # 未闭合的工具调用块最多吞掉这么多行，超出则认为判断有误并把内容还回去，
    # 避免一句形似工具调用的正文把整条回复全部吃掉。
    pending: List[str] = []
    for line in lines:
        stripped = line.strip()

        # Drop assistant status/progress templates wherever they appear.
        if _is_status_line(stripped):
            continue

        # Drop tool-call templates such as:
        # "code_run({...})", "file_read({...})", or lines prefixed with symbols.
        if not in_tool_block and TOOL_CALL_RE.match(stripped):
            if _is_tool_block_end(stripped):
                continue
            in_tool_block = True
            pending = [line]
            continue
        if in_tool_block:
            pending.append(line)
            if _is_tool_block_end(stripped):
                in_tool_block = False
                pending = []
            elif len(pending) > TOOL_BLOCK_MAX_LINES:
                in_tool_block = False
                filtered.extend(pending)
                pending = []
            continue

        # Remove markdown code-fence markers.
        if CODE_FENCE_RE.match(stripped):
            continue

        filtered.append(line)

    # 到结尾仍未闭合：同样按误判处理，保留原文
    if pending:
        filtered.extend(pending)

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


def _at_targets(raw_msg: Any) -> List[str]:
    """统一取出消息里所有 @ 目标，字符串/数组两种上报格式走同一套解析。"""
    targets: List[str] = []
    for seg in extract_segments(raw_msg):
        if seg.get("type") != "at":
            continue
        data = seg.get("data")
        if not isinstance(data, dict):
            continue
        qq = str(data.get("qq", "")).strip()
        if qq:
            targets.append(qq)
    return targets


def is_at_bot(raw_msg: Any, bot_qq: str) -> bool:
    # 不能用 f"[CQ:at,qq={bot_qq}]" 做子串匹配：部分实现会带上 name 等附加参数
    bot = str(bot_qq or "").strip()
    if not bot:
        return False
    return bot in _at_targets(raw_msg)


def extract_text(raw_msg: Any) -> str:
    if isinstance(raw_msg, str):
        # 字符串上报格式里 CQ 段与转义实体共存：先去段，再还原 &amp; / &#91; / &#93;
        content = raw_msg
        if "[CQ:" in content:
            content = CQ_SEGMENT_RE.sub("", content)
        return unescape_cq(content).strip()

    if isinstance(raw_msg, list):
        # 数组上报格式的 text 段本身未转义，不能再按 CQ 规则剥离，
        # 否则用户原样输入的 "[CQ:at,qq=1]" 这类文本会被误删。
        parts: List[str] = []
        for seg in raw_msg:
            if isinstance(seg, dict) and seg.get("type") == "text":
                parts.append(str(seg.get("data", {}).get("text", "")))
            elif isinstance(seg, str):
                parts.append(seg)
        return "".join(parts).strip()

    return str(raw_msg).strip()


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
    bot = str(bot_qq or "").strip()
    mentions = [qq for qq in _at_targets(raw_msg) if qq != bot and qq != "all"]
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
            # 参数值里的 , & [ ] 都是转义过的，不还原会拿到坏 URL（?a=1&amp;b=2）
            data[key.strip()] = unescape_cq(value.strip(), in_segment=True)
    return {"type": seg_type, "data": data}


def split_for_send(text: str, limit: int) -> List[str]:
    """按长度切分待发送文本，但绝不在 ``[CQ:...]`` 段内部切开。

    在 CQ 段中间截断会把 @ 变成一串乱码文本，因此宁可让某一块略微超长。
    """
    body = (text or "").strip() or "..."
    if limit <= 0 or len(body) <= limit:
        return [body]

    spans = [(m.start(), m.end()) for m in CQ_SEGMENT_RE.finditer(body)]
    parts: List[str] = []
    start = 0
    while len(body) - start > limit:
        hard = start + limit
        cut = body.rfind("\n", start, hard)
        if cut - start < limit * 0.6:
            cut = hard
        for begin, end in spans:
            if begin < cut < end:
                # 段落起点还在本块之后就整段顺延，否则该段本身超长，整段留在本块
                cut = begin if begin > start else end
                break
        chunk = body[start:cut].rstrip()
        if chunk:
            parts.append(chunk)
        start = cut
        while start < len(body) and body[start] in " \t\r\n":
            start += 1

    tail = body[start:]
    if tail:
        parts.append(tail)
    return parts or ["..."]


def format_attachments(attachments: Sequence[Dict[str, Any]]) -> str:
    lines: List[str] = []
    for idx, item in enumerate(attachments, start=1):
        try:
            size_bytes = int(item.get("size", 0))
        except (TypeError, ValueError):
            size_bytes = 0
        size_kb = max(1, size_bytes // 1024)
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
        "syntax, code fences, or tool-call templates (for example code_run(...), web_scan(...), file_read(...), file_patch(...), "
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
