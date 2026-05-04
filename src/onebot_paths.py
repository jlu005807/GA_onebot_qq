import os
import sys


def setup_sys_path():
    # 向上遍历找到 GA 根目录（含 agentmain.py）
    current = os.path.dirname(os.path.abspath(__file__))
    base_dir = None
    for _ in range(10):  # 最多向上10层
        if os.path.isfile(os.path.join(current, "agentmain.py")):
            base_dir = current
            break
        parent = os.path.dirname(current)
        if parent == current:
            break
        current = parent

    if base_dir is None:
        raise FileNotFoundError("找不到 agentmain.py，请确认项目在 GenericAgent 目录下")

    frontends_dir = os.path.join(base_dir, "frontends")
    venv_site_packages = os.path.join(base_dir, ".venv", "Lib", "site-packages")

    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)
    if frontends_dir not in sys.path:
        sys.path.insert(0, frontends_dir)
    if os.path.isdir(venv_site_packages) and venv_site_packages not in sys.path:
        sys.path.insert(0, venv_site_packages)
    return base_dir