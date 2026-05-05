import glob
import os
import sys


def _find_venv_site_packages(base_dir: str):
    venv_root = os.path.join(base_dir, ".venv")
    candidates = [
        os.path.join(venv_root, "Lib", "site-packages"),  # Windows
        os.path.join(
            venv_root,
            "lib",
            f"python{sys.version_info.major}.{sys.version_info.minor}",
            "site-packages",
        ),  # Linux/macOS (current interpreter version)
    ]
    candidates.extend(
        glob.glob(os.path.join(venv_root, "lib", "python*", "site-packages"))
    )

    seen = set()
    results = []
    for path in candidates:
        normalized = os.path.normpath(path)
        if normalized in seen:
            continue
        seen.add(normalized)
        if os.path.isdir(normalized):
            results.append(normalized)
    return results


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

    if base_dir not in sys.path:
        sys.path.insert(0, base_dir)
    if frontends_dir not in sys.path:
        sys.path.insert(0, frontends_dir)
    for site_packages in _find_venv_site_packages(base_dir):
        if site_packages not in sys.path:
            sys.path.insert(0, site_packages)
    return base_dir
