"""文档与实现的一致性测试。

配置项散落在四处：`onebot_config.py`、`README.md` 的配置表、两个 `.env` 模板。
以往靠人工同步，加了新配置很容易漏掉某一处。这些用例把一致性变成可执行的约束：
少写一处、多写一处、或改了默认值没同步文档，都会直接测试失败。

只读文件文本、不导入被测模块，因此不依赖父项目 GenericAgent。
"""

import os
import re
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC_DIR = os.path.join(PROJECT_ROOT, "src")


def _read(*parts):
    with open(os.path.join(PROJECT_ROOT, *parts), "r", encoding="utf-8-sig") as handle:
        return handle.read()


CONFIG_SOURCE = _read("src", "onebot_config.py")
APP_SOURCE = _read("src", "onebot_app.py")
README = _read("README.md")
ENV_ZH = _read(".env.example")
ENV_EN = _read(".env.example-en")

# 代码里真正被读取的配置键
CODE_KEYS = re.findall(r'_get_env\(\s*"(ONEBOT_[A-Z_]+)"', CONFIG_SOURCE)

# 各文档里声明的配置键（保留出现顺序）
README_ROW_RE = re.compile(r"^\| `(ONEBOT_[A-Z_]+)` \| (.+?) \| (.+?) \| (.+?) \|$", re.MULTILINE)
README_ROWS = README_ROW_RE.findall(README)
README_KEYS = [row[0] for row in README_ROWS]
ENV_ZH_KEYS = re.findall(r"^(ONEBOT_[A-Z_]+)=", ENV_ZH, re.MULTILINE)
ENV_EN_KEYS = re.findall(r"^(ONEBOT_[A-Z_]+)=", ENV_EN, re.MULTILINE)


class ConfigKeyCoverageTest(unittest.TestCase):
    def test_code_actually_reads_some_keys(self):
        # 正则失效时其余用例会全部空跑通过，这里先兜住
        self.assertGreaterEqual(len(CODE_KEYS), 20, CODE_KEYS)

    def test_no_duplicate_keys_in_code(self):
        self.assertEqual(len(CODE_KEYS), len(set(CODE_KEYS)))

    def test_every_code_key_is_in_readme_table(self):
        missing = [key for key in CODE_KEYS if key not in README_KEYS]
        self.assertEqual(missing, [], f"README 配置表缺少: {missing}")

    def test_every_code_key_is_in_chinese_template(self):
        missing = [key for key in CODE_KEYS if key not in ENV_ZH_KEYS]
        self.assertEqual(missing, [], f".env.example 缺少: {missing}")

    def test_every_code_key_is_in_english_template(self):
        missing = [key for key in CODE_KEYS if key not in ENV_EN_KEYS]
        self.assertEqual(missing, [], f".env.example-en 缺少: {missing}")

    def test_readme_documents_no_nonexistent_key(self):
        extra = [key for key in README_KEYS if key not in CODE_KEYS]
        self.assertEqual(extra, [], f"README 记录了代码里不存在的键: {extra}")

    def test_templates_declare_no_nonexistent_key(self):
        for name, keys in ((".env.example", ENV_ZH_KEYS), (".env.example-en", ENV_EN_KEYS)):
            extra = [key for key in keys if key not in CODE_KEYS]
            self.assertEqual(extra, [], f"{name} 声明了代码里不存在的键: {extra}")

    def test_both_templates_agree_key_for_key(self):
        self.assertEqual(ENV_ZH_KEYS, ENV_EN_KEYS, "中英模板的键集合或顺序不一致")

    def test_no_duplicate_keys_in_docs(self):
        for name, keys in (
            ("README", README_KEYS),
            (".env.example", ENV_ZH_KEYS),
            (".env.example-en", ENV_EN_KEYS),
        ):
            self.assertEqual(len(keys), len(set(keys)), f"{name} 有重复键")


class DocumentedDefaultsTest(unittest.TestCase):
    """README 写的默认值必须与代码常量一致。"""

    # 键 -> 代码里定义该默认值的常量名
    DEFAULT_CONSTANTS = {
        "ONEBOT_LOCK_PORT": "DEFAULT_LOCK_PORT",
        "ONEBOT_LOG_KEEP": "DEFAULT_LOG_KEEP",
        "ONEBOT_ATTACHMENT_TTL_HOURS": "DEFAULT_ATTACHMENT_TTL_HOURS",
        "ONEBOT_SPLIT_LIMIT": "DEFAULT_SPLIT_LIMIT",
    }

    def _readme_default(self, key):
        for row in README_ROWS:
            if row[0] == key:
                return row[1]
        self.fail(f"README 配置表里没有 {key}")

    def _code_default(self, constant):
        match = re.search(rf"^{constant}\s*=\s*(\d+)", CONFIG_SOURCE, re.MULTILINE)
        self.assertIsNotNone(match, f"onebot_config.py 里找不到常量 {constant}")
        return match.group(1)

    def test_numeric_defaults_match_code(self):
        for key, constant in self.DEFAULT_CONSTANTS.items():
            expected = self._code_default(constant)
            documented = self._readme_default(key)
            self.assertIn(expected, documented, f"{key} 默认值文档写的是 {documented}，代码是 {expected}")

    def test_log_file_default_matches_code(self):
        match = re.search(r'^DEFAULT_LOG_FILE\s*=\s*"([^"]+)"', CONFIG_SOURCE, re.MULTILINE)
        self.assertIsNotNone(match)
        self.assertIn(match.group(1), self._readme_default("ONEBOT_LOG_FILE"))

    def test_context_messages_cap_matches_code(self):
        match = re.search(r"^MAX_CONTEXT_MESSAGES\s*=\s*(\d+)", CONFIG_SOURCE, re.MULTILINE)
        self.assertIsNotNone(match)
        row = self._readme_default("ONEBOT_CONTEXT_MESSAGES")
        description = next(r[3] for r in README_ROWS if r[0] == "ONEBOT_CONTEXT_MESSAGES")
        self.assertIn(match.group(1), row + description)


class CommandTableTest(unittest.TestCase):
    """README 的命令表必须与 ADMIN_COMMANDS 完全对应。"""

    COMMAND_ROW_RE = re.compile(r"^\| `(/[a-z]+)[^`]*` \| (.+?) \| (.+?) \|$", re.MULTILINE)

    def setUp(self):
        self.rows = self.COMMAND_ROW_RE.findall(README)
        self.documented = {row[0]: row[1] for row in self.rows}
        match = re.search(r"^ADMIN_COMMANDS\s*=\s*\{([^}]*)\}", APP_SOURCE, re.MULTILINE)
        self.assertIsNotNone(match, "onebot_app.py 里找不到 ADMIN_COMMANDS")
        self.admin_commands = set(re.findall(r'"(/[a-z]+)"', match.group(1)))

    def test_command_table_is_present(self):
        self.assertGreaterEqual(len(self.rows), 9, self.rows)

    def test_every_admin_command_is_documented(self):
        missing = sorted(self.admin_commands - set(self.documented))
        self.assertEqual(missing, [], f"README 命令表缺少: {missing}")

    def test_admin_commands_are_marked_admin_only(self):
        for command in sorted(self.admin_commands):
            self.assertIn(
                "仅管理员",
                self.documented[command],
                f"{command} 受管理员限制，但 README 权限列写的是「{self.documented[command]}」",
            )

    def test_commands_marked_admin_only_really_are(self):
        for command, permission in self.documented.items():
            if "仅管理员" in permission:
                self.assertIn(
                    command,
                    self.admin_commands,
                    f"README 说 {command} 仅管理员，但它不在 ADMIN_COMMANDS 里",
                )


class ProjectStructureTest(unittest.TestCase):
    def test_every_source_module_is_listed_in_readme(self):
        modules = sorted(
            name for name in os.listdir(SRC_DIR) if name.endswith(".py") and name != "__init__.py"
        )
        self.assertTrue(modules)
        missing = [name for name in modules if name not in README]
        self.assertEqual(missing, [], f"README 项目结构缺少: {missing}")

    def test_every_test_module_is_listed_in_readme(self):
        tests_dir = os.path.dirname(os.path.abspath(__file__))
        modules = sorted(
            name
            for name in os.listdir(tests_dir)
            if name.startswith("test_") and name.endswith(".py")
        )
        self.assertTrue(modules)
        missing = [name for name in modules if name not in README]
        self.assertEqual(missing, [], f"README 测试表缺少: {missing}")

    def test_readme_lists_no_nonexistent_source_module(self):
        for name in re.findall(r"\b(onebot_[a-z_]+\.py)\b", README):
            self.assertTrue(
                os.path.isfile(os.path.join(SRC_DIR, name)),
                f"README 提到了不存在的模块 {name}",
            )


class RequirementsTest(unittest.TestCase):
    def test_websockets_is_pinned_with_a_floor(self):
        requirements = _read("requirements.txt")
        self.assertRegex(requirements, r"(?m)^websockets>=\d+")

    def test_no_test_framework_is_required_at_runtime(self):
        # 测试用标准库 unittest，requirements 不应把 pytest 变成硬依赖
        for line in _read("requirements.txt").splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#"):
                self.assertNotIn("pytest", stripped)


if __name__ == "__main__":
    unittest.main()
