"""进程生命周期辅助函数的测试。

`_prune_old_logs` 会**删除文件**，而它匹配的前缀必须与 `_build_timestamped_log_name`
生成的文件名一致——两者一旦不同步，ONEBOT_LOG_KEEP 就会静默失效（曾经如此）。
这里的核心断言就是这个往返关系。

main.py 依赖父项目 GenericAgent，不在该环境下运行时整个模块跳过。
"""

import os
import shutil
import tempfile
import unittest
from datetime import datetime

try:
    import main as onebot_main

    IMPORT_ERROR = None
except Exception as exc:  # pragma: no cover - 取决于运行环境
    IMPORT_ERROR = exc


@unittest.skipIf(IMPORT_ERROR is not None, f"GenericAgent unavailable: {IMPORT_ERROR}")
class LogNameTest(unittest.TestCase):
    def test_default_name(self):
        name = onebot_main._build_timestamped_log_name("onebot.log", datetime(2026, 7, 27, 5, 6, 7))
        self.assertEqual(name, "onebot_20260727_050607.log")

    def test_custom_stem_and_extension_are_kept(self):
        name = onebot_main._build_timestamped_log_name("mybot.txt", datetime(2026, 1, 2, 3, 4, 5))
        self.assertEqual(name, "mybot_20260102_030405.txt")

    def test_directory_component_is_dropped(self):
        name = onebot_main._build_timestamped_log_name(
            os.path.join("a", "b", "deep.log"), datetime(2026, 1, 1, 0, 0, 0)
        )
        self.assertEqual(name, "deep_20260101_000000.log")

    def test_blank_and_none_fall_back_to_default(self):
        for value in ("", "   ", None):
            name = onebot_main._build_timestamped_log_name(value, datetime(2026, 1, 1, 0, 0, 0))
            self.assertEqual(name, "onebot_20260101_000000.log")

    def test_extensionless_name_gets_log_suffix(self):
        name = onebot_main._build_timestamped_log_name("bot", datetime(2026, 1, 1, 0, 0, 0))
        self.assertEqual(name, "bot_20260101_000000.log")


@unittest.skipIf(IMPORT_ERROR is not None, f"GenericAgent unavailable: {IMPORT_ERROR}")
class PruneOldLogsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="onebot_logs_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _seed(self, name, age_seconds=0):
        path = os.path.join(self.tmp, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("x")
        if age_seconds:
            stamp = os.stat(path).st_mtime - age_seconds
            os.utime(path, (stamp, stamp))
        return path

    def _names(self):
        return sorted(os.listdir(self.tmp))

    def test_disabled_when_keep_is_not_positive(self):
        self._seed("onebot_1.log")
        self.assertEqual(onebot_main._prune_old_logs("onebot.log", 0, self.tmp), 0)
        self.assertEqual(onebot_main._prune_old_logs("onebot.log", -3, self.tmp), 0)
        self.assertEqual(self._names(), ["onebot_1.log"])

    def test_keeps_the_newest_and_removes_the_rest(self):
        for index in range(5):
            self._seed(f"onebot_{index}.log", age_seconds=index * 100)
        self.assertEqual(onebot_main._prune_old_logs("onebot.log", 2, self.tmp), 3)
        self.assertEqual(self._names(), ["onebot_0.log", "onebot_1.log"])

    def test_nothing_to_do_when_under_the_limit(self):
        self._seed("onebot_1.log")
        self.assertEqual(onebot_main._prune_old_logs("onebot.log", 5, self.tmp), 0)

    def test_unrelated_files_are_never_touched(self):
        self._seed("onebot_1.log", age_seconds=500)
        self._seed("other_1.log", age_seconds=500)
        self._seed("notes.txt", age_seconds=500)
        self._seed("onebot_1.log.bak", age_seconds=500)
        onebot_main._prune_old_logs("onebot.log", 1, self.tmp)
        for name in ("other_1.log", "notes.txt", "onebot_1.log.bak"):
            self.assertIn(name, self._names())

    def test_missing_directory_is_tolerated(self):
        self.assertEqual(
            onebot_main._prune_old_logs("onebot.log", 1, os.path.join(self.tmp, "nope")), 0
        )

    def test_prefix_follows_the_configured_log_name(self):
        """回归：前缀曾写死为 onebot_，自定义 ONEBOT_LOG_FILE 时 ONEBOT_LOG_KEEP 完全失效。"""
        for index in range(4):
            self._seed(f"mybot_{index}.log", age_seconds=index * 100)
        removed = onebot_main._prune_old_logs("mybot.log", 1, self.tmp)
        self.assertEqual(removed, 3, "自定义基名的日志必须同样被清理")
        self.assertEqual(self._names(), ["mybot_0.log"])

    def test_generated_name_is_matched_by_the_pruner(self):
        """往返不变式：生成器造出来的文件名必须能被清理器认出来。"""
        for log_name in ("onebot.log", "mybot.log", "qq.txt", "bot", "  spaced.log  "):
            with self.subTest(log_name=log_name):
                shutil.rmtree(self.tmp, ignore_errors=True)
                os.makedirs(self.tmp, exist_ok=True)
                generated = onebot_main._build_timestamped_log_name(log_name)
                self._seed(generated, age_seconds=500)
                # keep=0 表示关闭，所以用 keep=1 再放一个更新的文件来触发删除
                self._seed(
                    onebot_main._build_timestamped_log_name(log_name, datetime(2030, 1, 1))
                )
                removed = onebot_main._prune_old_logs(log_name, 1, self.tmp)
                self.assertEqual(removed, 1, f"{generated} 没有被清理器匹配到")


@unittest.skipIf(IMPORT_ERROR is not None, f"GenericAgent unavailable: {IMPORT_ERROR}")
class LogDirTest(unittest.TestCase):
    def test_log_dir_is_the_project_temp_dir(self):
        # 必须与 chatapp_common.redirect_log 的推导保持一致
        expected = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(onebot_main.__file__))), "temp"
        )
        self.assertEqual(onebot_main.log_dir_for(onebot_main.__file__), expected)


if __name__ == "__main__":
    unittest.main()
