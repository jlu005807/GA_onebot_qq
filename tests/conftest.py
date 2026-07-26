"""pytest 入口的 sys.path 兜底（与 tests/__init__.py 等价，二者只需生效其一）。"""

import os
import sys

_SRC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src")

if _SRC_DIR not in sys.path:
    sys.path.insert(0, _SRC_DIR)
