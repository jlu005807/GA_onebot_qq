"""测试包：把 src/ 注入 sys.path，使测试无需安装即可导入被测模块。

同时兼容两种运行方式（均无需额外依赖）：
    python -m unittest discover -s tests -t .
    python -m pytest tests
"""

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SRC_DIR = os.path.join(os.path.dirname(_TESTS_DIR), "src")

if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
