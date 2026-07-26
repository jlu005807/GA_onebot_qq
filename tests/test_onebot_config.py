import os
import shutil
import tempfile
import unittest

import onebot_config
from onebot_config import (
    DEFAULT_ATTACHMENT_TTL_HOURS,
    DEFAULT_LOCK_PORT,
    DEFAULT_LOG_KEEP,
    DEFAULT_SPLIT_LIMIT,
    MAX_CONTEXT_MESSAGES,
    OneBotConfig,
    _parse_bool,
    _parse_csv_tuple,
    _parse_int,
    _parse_non_negative_int,
    _parse_optional_bool,
    _parse_set,
    _read_dotenv,
    _resolve_path,
    load_config,
)


class ParseHelpersTest(unittest.TestCase):
    def test_parse_set(self):
        self.assertEqual(_parse_set("a, b ,,c"), {"a", "b", "c"})
        self.assertEqual(_parse_set(""), set())

    def test_parse_int_rejects_non_positive_and_garbage(self):
        self.assertEqual(_parse_int("7", 3), 7)
        self.assertEqual(_parse_int("0", 3), 3)
        self.assertEqual(_parse_int("-1", 3), 3)
        self.assertEqual(_parse_int("abc", 3), 3)
        self.assertEqual(_parse_int(None, 3), 3)

    def test_parse_non_negative_int_allows_zero(self):
        self.assertEqual(_parse_non_negative_int("0", 5), 0)
        self.assertEqual(_parse_non_negative_int("-2", 5), 5)
        self.assertEqual(_parse_non_negative_int("x", 5), 5)

    def test_parse_bool(self):
        for truthy in ("1", "true", "TRUE", "yes", "on"):
            self.assertTrue(_parse_bool(truthy, False), truthy)
        for falsy in ("0", "false", "no", "off"):
            self.assertFalse(_parse_bool(falsy, True), falsy)
        self.assertTrue(_parse_bool("", True))
        self.assertTrue(_parse_bool("nonsense", True))
        self.assertTrue(_parse_bool(None, True))

    def test_parse_optional_bool_distinguishes_unset(self):
        self.assertIsNone(_parse_optional_bool(""))
        self.assertIsNone(_parse_optional_bool(None))
        self.assertIsNone(_parse_optional_bool("maybe"))
        self.assertTrue(_parse_optional_bool("1"))
        self.assertFalse(_parse_optional_bool("off"))

    def test_parse_csv_tuple_keeps_order(self):
        self.assertEqual(_parse_csv_tuple(" a , b ,, c "), ("a", "b", "c"))
        self.assertEqual(_parse_csv_tuple(""), ())
        self.assertEqual(_parse_csv_tuple(None), ())


class ResolvePathTest(unittest.TestCase):
    def test_relative_is_based_on_project_root_not_cwd(self):
        resolved = _resolve_path("data")
        self.assertEqual(resolved, os.path.join(onebot_config.PROJECT_ROOT, "data"))

    def test_absolute_is_kept(self):
        absolute = os.path.abspath(os.sep + "tmp")
        self.assertEqual(_resolve_path(absolute), os.path.normpath(absolute))

    def test_blank_falls_back_to_base(self):
        self.assertEqual(_resolve_path(""), onebot_config.PROJECT_ROOT)

    def test_custom_base_dir(self):
        base = os.path.abspath(os.sep + "base")
        self.assertEqual(_resolve_path("x", base), os.path.join(base, "x"))


class ReadDotenvTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="onebot_env_")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(self, text, encoding="utf-8"):
        path = os.path.join(self.tmp, ".env")
        with open(path, "w", encoding=encoding) as handle:
            handle.write(text)
        return path

    def test_missing_file_is_empty(self):
        self.assertEqual(_read_dotenv(os.path.join(self.tmp, "nope")), {})

    def test_basic_pairs(self):
        data = _read_dotenv(self._write("A=1\nB = two \n"))
        self.assertEqual(data["A"], "1")
        self.assertEqual(data["B"], "two")

    def test_comments_and_blank_lines_skipped(self):
        data = _read_dotenv(self._write("# note\n\nA=1\n"))
        self.assertEqual(data, {"A": "1"})

    def test_export_prefix_stripped(self):
        self.assertEqual(_read_dotenv(self._write("export A=1\n"))["A"], "1")

    def test_quotes_stripped(self):
        data = _read_dotenv(self._write("A=\"a b\"\nB='c'\n"))
        self.assertEqual(data["A"], "a b")
        self.assertEqual(data["B"], "c")

    def test_bom_does_not_corrupt_first_key(self):
        # 记事本保存的 .env 带 BOM，按 utf-8 读会得到 "﻿ONEBOT_WS_URL"
        data = _read_dotenv(self._write("ONEBOT_WS_URL=ws://h:1\n", encoding="utf-8-sig"))
        self.assertIn("ONEBOT_WS_URL", data)

    def test_inline_comment_stripped_when_unquoted(self):
        data = _read_dotenv(self._write("A=1  # 说明\n"))
        self.assertEqual(data["A"], "1")

    def test_hash_inside_quotes_is_kept(self):
        data = _read_dotenv(self._write("A=\"a # b\"\n"))
        self.assertEqual(data["A"], "a # b")

    def test_hash_without_leading_space_is_kept(self):
        # 令牌等值里可能本身带 #，只有空白后的 # 才当注释
        data = _read_dotenv(self._write("A=abc#def\n"))
        self.assertEqual(data["A"], "abc#def")

    def test_value_starting_with_hash_is_kept_literally(self):
        # 回归：曾经把整个值清空，而清空的白名单会退化成「放行全部」
        data = _read_dotenv(self._write("A=#tag\nB=#123,#456\n"))
        self.assertEqual(data["A"], "#tag")
        self.assertEqual(data["B"], "#123,#456")

    def test_space_before_hash_is_a_comment_even_with_empty_value(self):
        data = _read_dotenv(self._write("A= # 整个值被注释掉了\n"))
        self.assertEqual(data["A"], "")

    def test_tab_before_hash_is_a_comment(self):
        data = _read_dotenv(self._write("A=1\t# note\n"))
        self.assertEqual(data["A"], "1")

    def test_quoting_protects_prose_containing_hash(self):
        # 含 '#' 的自然语言值必须加引号，否则会被行尾注释规则截断
        data = _read_dotenv(self._write('A="请用纯文本回复 #不要markdown"\n'))
        self.assertEqual(data["A"], "请用纯文本回复 #不要markdown")

    def test_unquoted_prose_is_cut_at_the_comment_marker(self):
        # 记录既有约定（与 python-dotenv 一致），文档里已说明需要加引号
        data = _read_dotenv(self._write("A=请用纯文本回复 #不要markdown\n"))
        self.assertEqual(data["A"], "请用纯文本回复")

    def test_single_quote_character_is_not_treated_as_quoting(self):
        data = _read_dotenv(self._write("A='\n"))
        self.assertEqual(data["A"], "'")

    def test_internal_spaces_are_preserved(self):
        data = _read_dotenv(self._write("A=a b c\n"))
        self.assertEqual(data["A"], "a b c")

    def test_line_without_equals_skipped(self):
        self.assertEqual(_read_dotenv(self._write("garbage\nA=1\n")), {"A": "1"})


class LoadConfigTest(unittest.TestCase):
    """load_config 必须完全由环境变量决定，因此屏蔽项目里真实的 .env。"""

    ENV_KEYS = (
        "ONEBOT_WS_URL",
        "ONEBOT_ADMIN_QQ",
        "ONEBOT_ALLOWED_USERS",
        "ONEBOT_ALLOW_GROUP",
        "ONEBOT_ALLOWED_GROUPS",
        "ONEBOT_GROUP_REQUIRE_AT",
        "ONEBOT_GROUP_TRIGGER_WORDS",
        "ONEBOT_CONTEXT_MESSAGES",
        "ONEBOT_ACCESS_TOKEN",
        "ONEBOT_PLAIN_TEXT_HINT",
        "ONEBOT_MAX_MSG_LENGTH",
        "ONEBOT_MAX_QUEUE_SIZE",
        "ONEBOT_DATA_DIR",
        "ONEBOT_MAX_FILE_BYTES",
        "ONEBOT_LOCK_PORT",
        "ONEBOT_LOG_FILE",
        "ONEBOT_LOG_KEEP",
        "ONEBOT_ATTACHMENT_TTL_HOURS",
        "ONEBOT_SPLIT_LIMIT",
        "ONEBOT_LOCAL_SOURCE_DIRS",
    )

    def setUp(self):
        self._saved_env = {key: os.environ.get(key) for key in self.ENV_KEYS}
        for key in self.ENV_KEYS:
            os.environ.pop(key, None)
        self._saved_dotenv = onebot_config._DOTENV
        onebot_config._DOTENV = {}
        self._saved_mykeys = onebot_config._mykeys
        onebot_config._mykeys = lambda: {}

    def tearDown(self):
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        onebot_config._DOTENV = self._saved_dotenv
        onebot_config._mykeys = self._saved_mykeys

    def test_defaults(self):
        config = load_config()
        self.assertIsInstance(config, OneBotConfig)
        self.assertEqual(config.lock_port, DEFAULT_LOCK_PORT)
        self.assertEqual(config.log_keep, DEFAULT_LOG_KEEP)
        self.assertEqual(config.attachment_ttl_hours, DEFAULT_ATTACHMENT_TTL_HOURS)
        self.assertEqual(config.split_limit, DEFAULT_SPLIT_LIMIT)
        self.assertEqual(config.log_file, "onebot.log")
        self.assertEqual(config.admin_set, set())
        self.assertTrue(config.allow_group)
        self.assertEqual(config.allowed_groups, {"*"})
        self.assertIsNone(config.group_require_at)
        self.assertEqual(config.group_trigger_words, ())
        self.assertEqual(config.context_messages, 0)
        self.assertEqual(config.local_source_dirs, ())
        self.assertEqual(config.data_dir, os.path.join(onebot_config.PROJECT_ROOT, "data"))

    def test_config_is_frozen(self):
        config = load_config()
        with self.assertRaises(Exception):
            config.ws_url = "ws://other"

    def test_env_overrides_are_applied(self):
        os.environ.update(
            {
                "ONEBOT_WS_URL": "ws://1.2.3.4:9",
                "ONEBOT_ADMIN_QQ": "1, 2",
                "ONEBOT_ALLOWED_USERS": "10,20",
                "ONEBOT_ALLOW_GROUP": "0",
                "ONEBOT_ALLOWED_GROUPS": "900",
                "ONEBOT_GROUP_REQUIRE_AT": "1",
                "ONEBOT_GROUP_TRIGGER_WORDS": "小宇, bot",
                "ONEBOT_LOCK_PORT": "20000",
                "ONEBOT_LOG_FILE": "custom.log",
                "ONEBOT_LOG_KEEP": "0",
                "ONEBOT_ATTACHMENT_TTL_HOURS": "0",
                "ONEBOT_SPLIT_LIMIT": "800",
            }
        )
        config = load_config()
        self.assertEqual(config.ws_url, "ws://1.2.3.4:9")
        self.assertEqual(config.admin_set, {"1", "2"})
        self.assertEqual(config.allowed_users, {"10", "20"})
        self.assertFalse(config.allow_group)
        self.assertEqual(config.allowed_groups, {"900"})
        self.assertTrue(config.group_require_at)
        self.assertEqual(config.group_trigger_words, ("小宇", "bot"))
        self.assertEqual(config.lock_port, 20000)
        self.assertEqual(config.log_file, "custom.log")
        self.assertEqual(config.log_keep, 0)
        self.assertEqual(config.attachment_ttl_hours, 0)
        self.assertEqual(config.split_limit, 800)

    def test_context_messages_is_capped(self):
        os.environ["ONEBOT_CONTEXT_MESSAGES"] = "999"
        self.assertEqual(load_config().context_messages, MAX_CONTEXT_MESSAGES)

    def test_plain_text_hint_can_be_disabled(self):
        os.environ["ONEBOT_PLAIN_TEXT_HINT"] = "off"
        self.assertEqual(load_config().plain_text_hint, "")

    def test_allowed_users_defaults_to_public_without_mykeys(self):
        self.assertEqual(load_config().allowed_users, {"*"})

    def test_local_source_dirs_are_resolved(self):
        os.environ["ONEBOT_LOCAL_SOURCE_DIRS"] = "cache, other"
        dirs = load_config().local_source_dirs
        self.assertEqual(
            dirs,
            (
                os.path.join(onebot_config.PROJECT_ROOT, "cache"),
                os.path.join(onebot_config.PROJECT_ROOT, "other"),
            ),
        )

    def test_environment_beats_dotenv(self):
        onebot_config._DOTENV = {"ONEBOT_LOG_FILE": "from_dotenv.log"}
        os.environ["ONEBOT_LOG_FILE"] = "from_env.log"
        self.assertEqual(load_config().log_file, "from_env.log")

    def test_dotenv_used_when_environment_absent(self):
        onebot_config._DOTENV = {"ONEBOT_LOG_FILE": "from_dotenv.log"}
        self.assertEqual(load_config().log_file, "from_dotenv.log")


if __name__ == "__main__":
    unittest.main()
