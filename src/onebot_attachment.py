import base64
import os
import re
import shutil
import time
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlparse

from onebot_async import run_in_thread

ATTACHMENT_TYPES = {"image", "record", "file"}
DEFAULT_EXTENSIONS = {
    "image": ".jpg",
    "record": ".silk",
    "file": ".bin",
}
# 只保留文件名里安全的字符：允许中日韩等 Unicode 文字，剔除路径分隔符与控制字符
UNSAFE_NAME_RE = re.compile(r"[^\w.\-]+", re.UNICODE)
MAX_NAME_BASE_LEN = 64
MAX_NAME_EXT_LEN = 16
ALLOWED_URL_SCHEMES = {"http", "https"}


class _SafeRedirectHandler(urllib.request.HTTPRedirectHandler):
    """只允许 http/https 之间跳转，阻断被重定向到 ftp:// 等其它协议。"""

    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if urlparse(newurl).scheme.lower() not in ALLOWED_URL_SCHEMES:
            return None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_URL_OPENER = urllib.request.build_opener(_SafeRedirectHandler)


class OneBotAttachmentManager:
    def __init__(
        self,
        data_dir: str,
        max_file_bytes: int,
        local_source_dirs: Sequence[str] = (),
    ):
        self.data_dir = os.path.abspath(data_dir)
        self.max_file_bytes = max_file_bytes
        # 非空时，只接受这些目录下的本地文件来源（纵深防御：OneBot 端被控时
        # data.path 可指向任意本地文件，内容会被复制并把路径交给 Agent）
        self.local_source_dirs = tuple(
            os.path.abspath(item) for item in local_source_dirs if str(item).strip()
        )
        self.data_dirs = {
            "image": os.path.join(self.data_dir, "image"),
            "record": os.path.join(self.data_dir, "record"),
            "file": os.path.join(self.data_dir, "file"),
        }
        for path in self.data_dirs.values():
            os.makedirs(path, exist_ok=True)

    @staticmethod
    def _sanitize_component(value: str, max_len: int) -> str:
        # 先取 basename 并把两种分隔符都切掉：Linux 上 os.path.basename 不认 "\"
        cleaned = str(value or "").replace("\\", "/").rsplit("/", 1)[-1]
        cleaned = UNSAFE_NAME_RE.sub("_", cleaned).strip("._ ")
        return cleaned[:max_len]

    def _build_filename(self, name_hint: str, source: str, default_ext: str) -> str:
        base = ""
        ext = ""
        if name_hint:
            base, ext = os.path.splitext(os.path.basename(str(name_hint).replace("\\", "/")))
        if not ext and source:
            ext = os.path.splitext(urlparse(source).path)[1]
        if not ext:
            ext = default_ext

        base = self._sanitize_component(base, MAX_NAME_BASE_LEN) or "file"
        # 扩展名同样必须清洗：它此前直接来自消息字段，从未被过滤
        ext_body = self._sanitize_component(ext, MAX_NAME_EXT_LEN)
        ext = f".{ext_body}" if ext_body else (default_ext or ".bin")

        suffix = uuid.uuid4().hex[:8]
        return f"{base}_{suffix}{ext}"

    def _default_extension(self, seg_type: str) -> str:
        return DEFAULT_EXTENSIONS.get(seg_type, ".bin")

    def _get_segment_url(self, data: Dict[str, Any]) -> str:
        url = str(data.get("url", "")).strip()
        if url.startswith("http://") or url.startswith("https://"):
            return url
        file_ref = str(data.get("file", "")).strip()
        if file_ref.startswith("http://") or file_ref.startswith("https://"):
            return file_ref
        return ""

    def _is_allowed_local_source(self, path: str) -> bool:
        if not self.local_source_dirs:
            return True
        try:
            resolved = os.path.realpath(path)
        except OSError:
            return False
        for root in self.local_source_dirs:
            try:
                if os.path.commonpath([resolved, os.path.realpath(root)]) == os.path.realpath(root):
                    return True
            except ValueError:
                # 不同盘符无公共前缀
                continue
        return False

    def _get_local_source(self, data: Dict[str, Any]) -> str:
        for key in ("path", "file"):
            candidate = self._normalize_local_path(str(data.get(key, "")).strip())
            if not candidate or not os.path.isfile(candidate):
                continue
            if not self._is_allowed_local_source(candidate):
                continue
            return candidate
        return ""

    def _normalize_local_path(self, value: str) -> str:
        raw = (value or "").strip().strip('"').strip("'")
        if not raw:
            return ""

        if raw.startswith("file://"):
            parsed = urlparse(raw)
            candidate = unquote(parsed.path or "")
            # file:///C:/path -> C:/path
            if re.match(r"^/[A-Za-z]:/", candidate):
                candidate = candidate[1:]
            # Unix-style absolute path for Windows drive path.
            candidate = candidate.replace("/", os.sep)
            return os.path.normpath(candidate)

        return os.path.normpath(raw)

    def _extract_base64_payload(self, data: Dict[str, Any]) -> str:
        file_ref = str(data.get("file", "")).strip()
        if file_ref.startswith("base64://"):
            return file_ref[len("base64://") :]
        if file_ref.startswith("data:") and ";base64," in file_ref:
            return file_ref.split(";base64,", 1)[1]
        return ""

    def _download_file_sync(self, url: str, dest_path: str) -> int:
        if urlparse(url).scheme.lower() not in ALLOWED_URL_SCHEMES:
            return 0
        req = urllib.request.Request(url, headers={"User-Agent": "OneBot"})
        try:
            with _URL_OPENER.open(req, timeout=20) as resp:
                length = resp.headers.get("Content-Length")
                if length:
                    try:
                        if int(length) > self.max_file_bytes:
                            return 0
                    except ValueError:
                        pass
                total = 0
                with open(dest_path, "wb") as handle:
                    while True:
                        chunk = resp.read(8192)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > self.max_file_bytes:
                            break
                        handle.write(chunk)
            if total > self.max_file_bytes:
                try:
                    os.remove(dest_path)
                except OSError:
                    pass
                return 0
            return total
        except Exception:
            try:
                if os.path.exists(dest_path):
                    os.remove(dest_path)
            except OSError:
                pass
            return 0

    def _copy_local_file_sync(self, source_path: str, dest_path: str) -> int:
        try:
            size = os.path.getsize(source_path)
            if size > self.max_file_bytes:
                return 0
            shutil.copy2(source_path, dest_path)
            return size
        except OSError:
            return 0

    def _save_base64_sync(self, payload: str, dest_path: str) -> int:
        # 先按编码长度粗筛，避免为了判大小把一个超大 payload 整块解码进内存
        if len(payload) > (self.max_file_bytes // 3 + 1) * 4 + 16:
            return 0
        try:
            binary = base64.b64decode(payload, validate=False)
        except Exception:
            return 0
        size = len(binary)
        if size <= 0 or size > self.max_file_bytes:
            return 0
        try:
            with open(dest_path, "wb") as handle:
                handle.write(binary)
            return size
        except OSError:
            return 0

    async def _save_from_url(
        self, seg_type: str, url: str, name_hint: str
    ) -> Optional[Dict[str, Any]]:
        save_dir = self.data_dirs.get(seg_type, self.data_dir)
        default_ext = self._default_extension(seg_type)
        filename = self._build_filename(name_hint, url, default_ext)
        dest_path = os.path.join(save_dir, filename)
        size = await run_in_thread(self._download_file_sync, url, dest_path)
        if not size:
            return None
        return {"type": seg_type, "path": os.path.abspath(dest_path), "size": size}

    async def _save_from_local_path(
        self, seg_type: str, source_path: str, name_hint: str
    ) -> Optional[Dict[str, Any]]:
        save_dir = self.data_dirs.get(seg_type, self.data_dir)
        default_ext = os.path.splitext(source_path)[1] or self._default_extension(seg_type)
        filename = self._build_filename(name_hint, source_path, default_ext)
        dest_path = os.path.join(save_dir, filename)
        size = await run_in_thread(self._copy_local_file_sync, source_path, dest_path)
        if not size:
            return None
        return {"type": seg_type, "path": os.path.abspath(dest_path), "size": size}

    async def _save_from_base64(
        self, seg_type: str, payload: str, name_hint: str
    ) -> Optional[Dict[str, Any]]:
        save_dir = self.data_dirs.get(seg_type, self.data_dir)
        default_ext = self._default_extension(seg_type)
        filename = self._build_filename(name_hint, "", default_ext)
        dest_path = os.path.join(save_dir, filename)
        size = await run_in_thread(self._save_base64_sync, payload, dest_path)
        if not size:
            return None
        return {"type": seg_type, "path": os.path.abspath(dest_path), "size": size}

    async def download_attachments(
        self, segments: Sequence[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], List[str]]:
        attachments: List[Dict[str, Any]] = []
        errors: List[str] = []
        for seg in segments:
            if not isinstance(seg, dict):
                continue
            seg_type = str(seg.get("type", "")).strip()
            if seg_type not in ATTACHMENT_TYPES:
                continue
            data = seg.get("data", {})
            if not isinstance(data, dict):
                data = {}
            name_hint = str(data.get("name") or data.get("file") or "").strip()

            url = self._get_segment_url(data)
            if url:
                saved = await self._save_from_url(seg_type, url, name_hint)
                if saved:
                    attachments.append(saved)
                    continue

            local_source = self._get_local_source(data)
            if local_source:
                saved = await self._save_from_local_path(seg_type, local_source, name_hint)
                if saved:
                    attachments.append(saved)
                    continue

            payload = self._extract_base64_payload(data)
            if payload:
                saved = await self._save_from_base64(seg_type, payload, name_hint)
                if saved:
                    attachments.append(saved)
                    continue

            errors.append(f"{seg_type} has no usable source (url/path/base64)")
        return attachments, errors

    def prune(self, ttl_seconds: int) -> int:
        """删除超过保留期的落盘附件，返回删除数量。ttl<=0 表示不清理。

        附件在正常处理完后没有任何删除时机，长期运行会把磁盘写满，
        因此按时间保留而不是处理完立刻删——Agent 可能还要追问同一张图。
        """
        if ttl_seconds <= 0:
            return 0
        cutoff = time.time() - ttl_seconds
        removed = 0
        for directory in self.data_dirs.values():
            try:
                entries = list(os.scandir(directory))
            except OSError:
                continue
            for entry in entries:
                try:
                    if not entry.is_file() or entry.stat().st_mtime >= cutoff:
                        continue
                    os.remove(entry.path)
                    removed += 1
                except OSError:
                    continue
        return removed


def cleanup_attachments(attachments: Sequence[Dict[str, Any]]) -> None:
    for item in attachments:
        path = item.get("path") if isinstance(item, dict) else None
        if not path:
            continue
        try:
            os.remove(path)
        except OSError:
            pass
