# -*- coding: utf-8 -*-
"""无障碍辅助：可访问名称/描述、系统高对比度检测与主题变化监听。

设计约定
--------
* 每个可交互控件都通过 :func:`describe` 设置可访问名称（AccessibleName）
  与可访问描述（AccessibleDescription），屏幕阅读器据此朗读；
  控件角色（按钮 / 复选框 / 组合框 / 编辑框）与状态（勾选 / 选中 / 聚焦）
  由 Qt 的 QAccessible 自动暴露，无需手工声明。
* 界面**不硬编码前景/背景色**，颜色一律取自系统调色板。这样 Windows
  高对比度模式切换后，Qt 会自动套用系统高反差配色，不会出现
  「深色底 + 深色字」这类低视力不可读的组合。
* 主题 / 调色板变化时，:class:`ThemeWatcher` 会发出信号，界面据此刷新提示。
"""
from __future__ import annotations

import ctypes
import sys

from PyQt6.QtCore import QObject, pyqtSignal
from PyQt6.QtWidgets import QWidget


def describe(widget: QWidget, name: str, description: str = "") -> QWidget:
    """为控件设置屏幕阅读器可读的名称与描述，返回控件本身便于链式调用。"""
    if name:
        widget.setAccessibleName(name)
    if description:
        widget.setAccessibleDescription(description)
    return widget


def _windows_high_contrast() -> bool | None:
    """读取 Windows 的高对比度开关；非 Windows 或调用失败时返回 ``None``。"""
    if sys.platform != "win32":
        return None
    try:
        class _HIGHCONTRASTW(ctypes.Structure):
            _fields_ = [
                ("cbSize", ctypes.c_uint),
                ("dwFlags", ctypes.c_uint),
                ("lpszDefaultScheme", ctypes.c_wchar_p),
            ]

        info = _HIGHCONTRASTW()
        info.cbSize = ctypes.sizeof(_HIGHCONTRASTW)
        spi_get_high_contrast = 0x0042
        hcf_high_contrast_on = 0x00000001
        ok = ctypes.windll.user32.SystemParametersInfoW(
            spi_get_high_contrast, info.cbSize, ctypes.byref(info), 0
        )
        if not ok:
            return None
        return bool(info.dwFlags & hcf_high_contrast_on)
    except Exception:  # noqa: BLE001 - 检测失败不应影响程序运行
        return None


def is_high_contrast() -> bool:
    """当前是否处于系统高对比度模式（无法检测时按「否」处理）。"""
    return bool(_windows_high_contrast())


class ThemeWatcher(QObject):
    """监听系统调色板 / 配色方案变化（含高对比度切换）。

    通过 ``QApplication.paletteChanged`` 与
    ``QStyleHints.colorSchemeChanged`` 两个信号监听。刻意不使用事件过滤器：
    后者挂在 QApplication 上时，应用退出阶段容易触发 Qt 的销毁顺序崩溃。
    """

    changed = pyqtSignal(bool)

    def __init__(self, app, parent=None):
        super().__init__(parent)
        self._app = app
        self._last = is_high_contrast()
        app.paletteChanged.connect(self._on_changed)
        try:
            app.styleHints().colorSchemeChanged.connect(self._on_changed)
        except Exception:  # noqa: BLE001 - 旧版 Qt 没有该信号时退化为仅监听调色板
            pass

    def _on_changed(self, *_args) -> None:
        current = is_high_contrast()
        if current != self._last:
            self._last = current
            self.changed.emit(current)
