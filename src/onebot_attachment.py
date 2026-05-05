import base64
import os
import re
import shutil
import urllib.request
import uuid
from typing import Any, Dict, List, Optional, Sequence, Tuple
from urllib.parse import unquote, urlparse

from onebot_async import run_in_thread

ATTACHMENT_TYPES = {"image", "record", "file"}


class OneBotAttachmentManager:
    def __init__(self, data_dir: str, max_file_bytes: int):
        self.data_dir = os.path.abspath(data_dir)
        self.max_file_bytes = max_file_bytes
        self.data_dirs = {
            "image": os.path.join(self.data_dir, "image"),
            "record": os.path.join(self.data_dir, "record"),
            "file": os.path.join(self.data_dir, "file"),
        }
        for path in self.data_dirs.values():
            os.makedirs(path, exist_ok=True)

    def _build_filename(self, name_hint: str, source: str, default_ext: str) -> str:
        base = ""
        ext = ""
        if name_hint:
            base, ext = os.path.splitext(os.path.basename(name_hint))
        if not ext and source:
            ext = os.path.splitext(urlparse(source).path)[1]
        if not ext:
            ext = default_ext
        if not base:
            base = "file"
        base = re.sub(r"[^A-Za-z0-9._-]+", "_", base).strip("._")
        if not base:
            base = "file"
        suffix = uuid.uuid4().hex[:8]
        return f"{base}_{suffix}{ext}"

    def _get_segment_url(self, data: Dict[str, Any]) -> str:
        url = str(data.get("url", "")).strip()
        if url.startswith("http://") or url.startswith("https://"):
            return url
        file_ref = str(data.get("file", "")).strip()
        if file_ref.startswith("http://") or file_ref.startswith("https://"):
            return file_ref
        return ""

    def _get_local_source(self, data: Dict[str, Any]) -> str:
        path = self._normalize_local_path(str(data.get("path", "")).strip())
        if path and os.path.isfile(path):
            return path
        file_ref = self._normalize_local_path(str(data.get("file", "")).strip())
        if file_ref and os.path.isfile(file_ref):
            return file_ref
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
        req = urllib.request.Request(url, headers={"User-Agent": "OneBot"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
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
        default_ext = ".jpg" if seg_type == "image" else ".silk" if seg_type == "record" else ".bin"
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
        default_ext = os.path.splitext(source_path)[1] or (
            ".silk" if seg_type == "record" else ".bin"
        )
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
        default_ext = ".jpg" if seg_type == "image" else ".silk" if seg_type == "record" else ".bin"
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
            seg_type = str(seg.get("type", "")).strip()
            if seg_type not in ATTACHMENT_TYPES:
                continue
            data = seg.get("data", {}) if isinstance(seg, dict) else {}
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


def cleanup_attachments(attachments: Sequence[Dict[str, Any]]) -> None:
    for item in attachments:
        path = item.get("path") if isinstance(item, dict) else None
        if not path:
            continue
        try:
            os.remove(path)
        except OSError:
            pass
