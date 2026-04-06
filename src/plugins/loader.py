# -*- coding: utf-8 -*-
"""
Plugin module scanner and loader.
"""
import importlib
import logging
from pathlib import Path
from typing import Callable, List, Tuple

logger = logging.getLogger(__name__)


def scan_and_register(
    plugin_dirs: List[str],
    register_func_name: str = "register",
) -> List[Tuple[str, Callable]]:
    """
    Scan multiple directories, import .py files (skipping __init__.py),
    and call their module-level register() function.

    Returns: [(name, factory_func), ...]
    """
    results = []

    for dir_path in plugin_dirs:
        path = Path(dir_path)
        if not path.is_dir():
            continue

        for py_file in sorted(path.glob("*.py")):
            if py_file.name.startswith("_"):
                continue

            parent_name = path.name
            module_name = f"plugins.{parent_name}.{py_file.stem}"
            try:
                module = importlib.import_module(module_name)
                factory = getattr(module, register_func_name, None)
                if factory is None or not callable(factory):
                    logger.warning(
                        f"[Loader] {module_name} 缺少可执行的 register() 函数，跳过"
                    )
                    continue
                results.append((py_file.stem, factory))
                logger.info("[Loader] 成功加载插件: %s", module_name)
            except Exception as exc:
                logger.warning("[Loader] 加载插件 %s 失败: %s", module_name, exc)
                continue

    return results
