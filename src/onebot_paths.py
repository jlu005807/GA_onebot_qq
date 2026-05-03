import os
import sys


def setup_sys_path():
    # 确保 GA 根目录与 frontends 可被导入
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    frontends_dir = os.path.join(base_dir, "frontends")
    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)
    if frontends_dir not in sys.path:
        sys.path.insert(0, frontends_dir)
    return base_dir
