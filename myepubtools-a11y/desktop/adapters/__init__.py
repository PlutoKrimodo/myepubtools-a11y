# -*- coding: utf-8 -*-
"""桌面适配器包：自动发现 ``BaseAdapter`` 子类。

与 ``modules/registry.py`` 的自动发现思路一致：新增一个工具只需在
本目录放一个模块并定义适配器类，无需改动界面代码。
"""
from __future__ import annotations

import importlib
import pkgutil

from desktop.adapters.base import BaseAdapter


def discover_adapters() -> tuple[list[BaseAdapter], list[str]]:
    """扫描本包，返回 ``(adapters, warnings)``，按 ``order`` 排序。

    单个适配器导入失败（例如缺少 opencc 依赖）不会影响其它工具，
    只在 ``warnings`` 里记录一条说明。
    """
    adapters: list[BaseAdapter] = []
    warnings: list[str] = []
    seen: set[str] = set()

    for info in pkgutil.iter_modules(__path__):
        name = info.name
        if name.startswith("_") or name == "base":
            continue

        module_name = f"{__name__}.{name}"
        try:
            module = importlib.import_module(module_name)
        except Exception as exc:  # noqa: BLE001 - 单个工具失败不拖垮整个应用
            warnings.append(f"{module_name} 加载失败：{type(exc).__name__}: {exc}")
            continue

        for value in vars(module).values():
            if (
                isinstance(value, type)
                and issubclass(value, BaseAdapter)
                and value is not BaseAdapter
                and getattr(value, "key", "")
            ):
                instance = value()
                if instance.key in seen:
                    warnings.append(f"适配器 key 重复：{instance.key}，已跳过")
                    continue
                seen.add(instance.key)
                adapters.append(instance)

    adapters.sort(key=lambda item: (item.order, item.key))
    return adapters, warnings
