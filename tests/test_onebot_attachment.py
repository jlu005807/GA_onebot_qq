import asyncio
import base64
import os
import shutil
import tempfile
import time
import unittest

from onebot_attachment import OneBotAttachmentManager, cleanup_attachments


class AttachmentManagerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="onebot_test_")
        self.data_dir = os.path.join(self.tmp, "data")
        self.manager = OneBotAttachmentManager(self.data_dir, max_file_bytes=1024)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, name, payload=b"x"):
        path = os.path.join(self.tmp, name)
        with open(path, "wb") as handle:
            handle.write(payload)
        return path


class InitTest(AttachmentManagerTestCase):
    def test_subdirs_created(self):
        for kind in ("image", "record", "file"):
            self.assertTrue(os.path.isdir(os.path.join(self.data_dir, kind)))

    def test_data_dir_is_absolute(self):
        self.assertTrue(os.path.isabs(self.manager.data_dir))


class BuildFilenameTest(AttachmentManagerTestCase):
    def test_unsafe_characters_replaced(self):
        name = self.manager._build_filename("../../etc/passwd", "", ".bin")
        self.assertNotIn("..", name)
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)

    def test_extension_taken_from_hint(self):
        self.assertTrue(self.manager._build_filename("photo.png", "", ".jpg").endswith(".png"))

    def test_extension_falls_back_to_url(self):
        name = self.manager._build_filename("", "http://h/a/b.gif", ".jpg")
        self.assertTrue(name.endswith(".gif"))

    def test_extension_falls_back_to_default(self):
        self.assertTrue(self.manager._build_filename("noext", "", ".jpg").endswith(".jpg"))

    def test_blank_hint_uses_file_base(self):
        self.assertTrue(self.manager._build_filename("", "", ".jpg").startswith("file_"))

    def test_names_are_unique(self):
        first = self.manager._build_filename("a.jpg", "", ".jpg")
        second = self.manager._build_filename("a.jpg", "", ".jpg")
        self.assertNotEqual(first, second)

    def test_all_punctuation_base_falls_back(self):
        name = self.manager._build_filename("...", "", ".jpg")
        self.assertTrue(name.endswith(".jpg"))
        self.assertNotIn("..", name)

    def test_unicode_filename_is_kept_readable(self):
        # 中文名此前会被整体替换成 "_"，Agent 看不出附件是什么
        name = self.manager._build_filename("季度报告.pdf", "", ".bin")
        self.assertIn("季度报告", name)
        self.assertTrue(name.endswith(".pdf"))

    def test_extension_is_sanitized(self):
        # 扩展名此前直接来自消息字段，未经任何过滤
        name = self.manager._build_filename("a.j/p\\g", "", ".bin")
        self.assertNotIn("/", name)
        self.assertNotIn("\\", name)

    def test_backslash_separator_stripped_on_every_platform(self):
        name = self.manager._build_filename(r"..\..\windows\evil.exe", "", ".bin")
        self.assertNotIn("\\", name)
        self.assertNotIn("..", name)
        self.assertTrue(name.startswith("evil_"))

    def test_absurdly_long_name_is_capped(self):
        name = self.manager._build_filename("a" * 500 + "." + "b" * 100, "", ".bin")
        self.assertLess(len(name), 130)


class NormalizeLocalPathTest(AttachmentManagerTestCase):
    def test_blank(self):
        self.assertEqual(self.manager._normalize_local_path(""), "")
        self.assertEqual(self.manager._normalize_local_path("   "), "")

    def test_quotes_stripped(self):
        self.assertEqual(
            self.manager._normalize_local_path('"/tmp/a.txt"'), os.path.normpath("/tmp/a.txt")
        )

    def test_file_uri_with_windows_drive(self):
        self.assertEqual(
            self.manager._normalize_local_path("file:///C:/tmp/a.txt"),
            os.path.normpath("C:" + os.sep + "tmp" + os.sep + "a.txt"),
        )

    def test_file_uri_percent_decoded(self):
        result = self.manager._normalize_local_path("file:///tmp/a%20b.txt")
        self.assertIn("a b.txt", result)


class SegmentSourceTest(AttachmentManagerTestCase):
    def test_url_from_url_field(self):
        self.assertEqual(self.manager._get_segment_url({"url": "http://h/a.jpg"}), "http://h/a.jpg")

    def test_url_from_file_field(self):
        self.assertEqual(
            self.manager._get_segment_url({"file": "https://h/a.jpg"}), "https://h/a.jpg"
        )

    def test_non_http_ignored(self):
        self.assertEqual(self.manager._get_segment_url({"url": "ftp://h/a.jpg"}), "")
        self.assertEqual(self.manager._get_segment_url({}), "")

    def test_local_source_prefers_existing_path(self):
        path = self._write("a.txt")
        self.assertEqual(self.manager._get_local_source({"path": path}), os.path.normpath(path))

    def test_local_source_from_file_field(self):
        path = self._write("b.txt")
        self.assertEqual(self.manager._get_local_source({"file": path}), os.path.normpath(path))

    def test_local_source_missing_file(self):
        self.assertEqual(self.manager._get_local_source({"path": "/nope/x"}), "")


class Base64PayloadTest(AttachmentManagerTestCase):
    def test_base64_scheme(self):
        self.assertEqual(self.manager._extract_base64_payload({"file": "base64://QUJD"}), "QUJD")

    def test_data_uri(self):
        self.assertEqual(
            self.manager._extract_base64_payload({"file": "data:image/png;base64,QUJD"}), "QUJD"
        )

    def test_none(self):
        self.assertEqual(self.manager._extract_base64_payload({"file": "a.jpg"}), "")


class SyncSaveLimitsTest(AttachmentManagerTestCase):
    def test_base64_within_limit(self):
        dest = os.path.join(self.tmp, "out.bin")
        payload = base64.b64encode(b"hello").decode()
        self.assertEqual(self.manager._save_base64_sync(payload, dest), 5)
        with open(dest, "rb") as handle:
            self.assertEqual(handle.read(), b"hello")

    def test_base64_over_limit_rejected(self):
        dest = os.path.join(self.tmp, "big.bin")
        payload = base64.b64encode(b"x" * 2048).decode()
        self.assertEqual(self.manager._save_base64_sync(payload, dest), 0)

    def test_base64_invalid_rejected(self):
        dest = os.path.join(self.tmp, "bad.bin")
        self.assertEqual(self.manager._save_base64_sync("!!!not base64!!!", dest), 0)

    def test_copy_within_limit(self):
        src = self._write("small.bin", b"y" * 100)
        dest = os.path.join(self.tmp, "copy.bin")
        self.assertEqual(self.manager._copy_local_file_sync(src, dest), 100)
        self.assertTrue(os.path.isfile(dest))

    def test_copy_over_limit_rejected(self):
        src = self._write("large.bin", b"y" * 2048)
        dest = os.path.join(self.tmp, "copy2.bin")
        self.assertEqual(self.manager._copy_local_file_sync(src, dest), 0)
        self.assertFalse(os.path.exists(dest))

    def test_copy_missing_source(self):
        dest = os.path.join(self.tmp, "copy3.bin")
        self.assertEqual(self.manager._copy_local_file_sync(os.path.join(self.tmp, "no"), dest), 0)


class DownloadAttachmentsTest(AttachmentManagerTestCase):
    def _run(self, segments):
        return asyncio.run(self.manager.download_attachments(segments))

    def test_non_attachment_segments_ignored(self):
        attachments, errors = self._run([{"type": "text", "data": {"text": "hi"}}])
        self.assertEqual(attachments, [])
        self.assertEqual(errors, [])

    def test_malformed_segments_ignored(self):
        attachments, errors = self._run(["junk", 5, None])
        self.assertEqual(attachments, [])
        self.assertEqual(errors, [])

    def test_local_file_copied_into_data_dir(self):
        src = self._write("note.txt", b"hello")
        attachments, errors = self._run([{"type": "file", "data": {"path": src}}])
        self.assertEqual(errors, [])
        self.assertEqual(len(attachments), 1)
        item = attachments[0]
        self.assertEqual(item["type"], "file")
        self.assertEqual(item["size"], 5)
        self.assertTrue(os.path.isfile(item["path"]))
        self.assertTrue(item["path"].startswith(os.path.join(self.data_dir, "file")))

    def test_base64_saved_into_image_dir(self):
        payload = base64.b64encode(b"img").decode()
        attachments, errors = self._run(
            [{"type": "image", "data": {"file": "base64://" + payload}}]
        )
        self.assertEqual(errors, [])
        self.assertTrue(attachments[0]["path"].startswith(os.path.join(self.data_dir, "image")))

    def test_segment_without_source_reports_error(self):
        attachments, errors = self._run([{"type": "image", "data": {"file": "abc.jpg"}}])
        self.assertEqual(attachments, [])
        self.assertEqual(len(errors), 1)
        self.assertIn("image", errors[0])

    def test_data_not_a_dict_is_tolerated(self):
        attachments, errors = self._run([{"type": "image", "data": "junk"}])
        self.assertEqual(attachments, [])
        self.assertEqual(len(errors), 1)

    def test_oversize_local_file_reports_error(self):
        src = self._write("big.bin", b"z" * 4096)
        attachments, errors = self._run([{"type": "file", "data": {"path": src}}])
        self.assertEqual(attachments, [])
        self.assertEqual(len(errors), 1)


class LocalSourceAllowlistTest(AttachmentManagerTestCase):
    def test_any_path_allowed_when_allowlist_empty(self):
        path = self._write("free.txt")
        self.assertEqual(self.manager._get_local_source({"path": path}), os.path.normpath(path))

    def test_outside_allowlist_is_rejected(self):
        allowed_dir = os.path.join(self.tmp, "allowed")
        os.makedirs(allowed_dir, exist_ok=True)
        manager = OneBotAttachmentManager(
            os.path.join(self.tmp, "data2"), 1024, local_source_dirs=[allowed_dir]
        )
        outside = self._write("outside.txt")
        self.assertEqual(manager._get_local_source({"path": outside}), "")

    def test_inside_allowlist_is_accepted(self):
        allowed_dir = os.path.join(self.tmp, "allowed")
        os.makedirs(allowed_dir, exist_ok=True)
        inside = os.path.join(allowed_dir, "ok.txt")
        with open(inside, "wb") as handle:
            handle.write(b"x")
        manager = OneBotAttachmentManager(
            os.path.join(self.tmp, "data3"), 1024, local_source_dirs=[allowed_dir]
        )
        self.assertEqual(manager._get_local_source({"path": inside}), os.path.normpath(inside))

    def test_rejected_source_surfaces_as_error(self):
        allowed_dir = os.path.join(self.tmp, "allowed")
        os.makedirs(allowed_dir, exist_ok=True)
        manager = OneBotAttachmentManager(
            os.path.join(self.tmp, "data4"), 1024, local_source_dirs=[allowed_dir]
        )
        outside = self._write("secret.txt")
        attachments, errors = asyncio.run(
            manager.download_attachments([{"type": "file", "data": {"path": outside}}])
        )
        self.assertEqual(attachments, [])
        self.assertEqual(len(errors), 1)


class UrlSchemeGuardTest(AttachmentManagerTestCase):
    def test_non_http_scheme_is_refused(self):
        dest = os.path.join(self.tmp, "out.bin")
        self.assertEqual(self.manager._download_file_sync("file:///etc/passwd", dest), 0)
        self.assertEqual(self.manager._download_file_sync("ftp://h/a", dest), 0)
        self.assertFalse(os.path.exists(dest))


class Base64SizeGuardTest(AttachmentManagerTestCase):
    def test_oversize_payload_rejected_without_decoding(self):
        # 编码长度就已超限时直接拒绝，避免把巨大 payload 解进内存
        payload = "A" * (self.manager.max_file_bytes * 4)
        self.assertEqual(
            self.manager._save_base64_sync(payload, os.path.join(self.tmp, "x.bin")), 0
        )


class PruneTest(AttachmentManagerTestCase):
    def _seed(self, kind, name, age_seconds):
        path = os.path.join(self.manager.data_dirs[kind], name)
        with open(path, "wb") as handle:
            handle.write(b"x")
        stamp = time.time() - age_seconds
        os.utime(path, (stamp, stamp))
        return path

    def test_disabled_when_ttl_not_positive(self):
        old = self._seed("image", "old.jpg", 10_000)
        self.assertEqual(self.manager.prune(0), 0)
        self.assertEqual(self.manager.prune(-5), 0)
        self.assertTrue(os.path.exists(old))

    def test_only_expired_files_removed(self):
        old = self._seed("image", "old.jpg", 10_000)
        fresh = self._seed("image", "fresh.jpg", 10)
        self.assertEqual(self.manager.prune(3600), 1)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(fresh))

    def test_all_kinds_are_swept(self):
        self._seed("image", "a.jpg", 10_000)
        self._seed("record", "b.amr", 10_000)
        self._seed("file", "c.bin", 10_000)
        self.assertEqual(self.manager.prune(3600), 3)

    def test_missing_directory_is_tolerated(self):
        shutil.rmtree(self.manager.data_dirs["image"], ignore_errors=True)
        self.assertEqual(self.manager.prune(3600), 0)


class CleanupAttachmentsTest(AttachmentManagerTestCase):
    def test_files_removed(self):
        path = self._write("gone.txt")
        cleanup_attachments([{"path": path}])
        self.assertFalse(os.path.exists(path))

    def test_missing_path_tolerated(self):
        cleanup_attachments([{"path": os.path.join(self.tmp, "nope")}, {}, "junk"])


if __name__ == "__main__":
    unittest.main()
