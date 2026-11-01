# -*- coding: utf-8 -*-
"""MyEpubTools 桌面版入口。

用法（在项目根目录）::

    python -m desktop.main

也可以直接运行本文件::

    python desktop/main.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许直接 `python desktop/main.py` 运行时仍能 import modules/
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PyQt6.QtCore import QEvent, QSettings, QTimer, Qt
from PyQt6.QtGui import QAction, QFont, QKeySequence
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from desktop.accessibility import ThemeWatcher, describe, is_high_contrast
from desktop.adapters import discover_adapters
from desktop.framework import ToolPage
from desktop.help import HelpPage
from desktop.icons import make_app_icon
from desktop.theme import (
    MODE_BUILTIN_HC,
    MODE_NORMAL,
    MODE_SYSTEM_HC,
    apply_theme,
)


def _app_font() -> QFont:
    font = QFont()
    if sys.platform == "win32":
        font.setFamilies(["Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI"])
    else:
        font.setFamilies(["PingFang SC", "Noto Sans CJK SC", "Source Han Sans SC"])
    font.setPointSize(10)
    return font


class MainWindow(QMainWindow):
    """书卷式布局：左侧深色目录栏，右侧纸色工具页。

    高对比度有两层来源：
    * **系统**开启高对比度时优先跟随系统（清空样式表，交给系统调色板）；
    * 系统未开启时，可用侧边栏的「高对比度模式」开关启用**内置**高对比度主题，
      该选择会记入 ``QSettings``，下次启动仍然生效。
    """

    def __init__(self, adapters, warnings):
        super().__init__()
        self.setWindowTitle("MyEpubTools")
        self.resize(1180, 780)
        self.setMinimumSize(960, 640)

        self._settings = QSettings("MyEpubTools", "Desktop")
        self._manual_high_contrast = bool(
            self._settings.value("high_contrast/manual", False, type=bool)
        )
        self._suppress_hc_signal = False
        self._suppress_tool_signal = False
        self._last_tool_row = 0

        central = QWidget()
        central.setObjectName("centralRoot")
        central.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        central.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)

        # ---- 左侧目录栏（深色，朱砂选中条）----
        sidebar = QWidget()
        sidebar.setObjectName("sidebar")
        sidebar.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        sidebar.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        sidebar.setFixedWidth(236)
        side = QVBoxLayout(sidebar)
        side.setContentsMargins(20, 26, 14, 18)
        side.setSpacing(6)

        sidebar_title = QLabel("MyEpubTools")
        sidebar_title.setObjectName("sidebarTitle")
        describe(sidebar_title, "MyEpubTools", "视障友好无障碍桌面版")
        side.addWidget(sidebar_title)

        side.addSpacing(18)

        self.tool_list = QListWidget()
        self.tool_list.setObjectName("toolList")
        self.tool_list.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        describe(
            self.tool_list,
            "工具列表",
            "用上下方向键选择工具；按右方向键或 Enter 进入工具页，F6 在两侧切换",
        )
        self.tool_list.installEventFilter(self)
        side.addWidget(self.tool_list, 1)

        # ---- 快捷键与说明入口 ----
        self.help_button = QPushButton("快捷键与说明 (F1)")
        self.help_button.setObjectName("helpLink")
        self.help_button.setMinimumHeight(34)
        self.help_button.setToolTip("快捷键与说明（快捷键 F1）")
        describe(
            self.help_button,
            "快捷键与说明",
            "打开键盘快捷键与读屏说明页 快捷键 F1",
        )
        self.help_button.clicked.connect(self._show_help_page)
        side.addWidget(self.help_button)

        # ---- 内置高对比度开关 ----
        self.hc_toggle = QCheckBox("高对比度模式")
        self.hc_toggle.setObjectName("hcToggle")
        self.hc_toggle.setChecked(self._manual_high_contrast)
        self.hc_toggle.setToolTip("高对比度模式（快捷键 Ctrl+Shift+H）")
        describe(
            self.hc_toggle,
            "高对比度模式",
            "在应用内启用黑底白字的高对比度配色 快捷键 Ctrl+Shift+H",
        )
        self.hc_toggle.toggled.connect(self._on_hc_toggled)
        side.addWidget(self.hc_toggle)

        self.high_contrast_label = QLabel()
        self.high_contrast_label.setObjectName("sidebarFoot")
        self.high_contrast_label.setWordWrap(True)
        side.addWidget(self.high_contrast_label)

        root.addWidget(sidebar)

        # ---- 右侧工具页（纸色）----
        self.stack = QStackedWidget()
        self.stack.setObjectName("contentRoot")
        self.stack.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.stack.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        for adapter in adapters:
            item = QListWidgetItem(adapter.title)
            item.setData(Qt.ItemDataRole.UserRole, adapter)
            item.setData(
                Qt.ItemDataRole.AccessibleDescriptionRole, adapter.description
            )
            item.setToolTip(adapter.description)
            self.tool_list.addItem(item)
            page = ToolPage(adapter)
            page.busy_changed.connect(self._on_page_busy)
            page.chain_dirty.connect(self._rebuild_focus_chain)
            self.stack.addWidget(page)

        self.help_page = HelpPage(on_back=self._return_to_tool)
        self.stack.addWidget(self.help_page)
        self._help_index = self.stack.count() - 1
        root.addWidget(self.stack, 1)

        self.setCentralWidget(central)
        self._build_menus()

        self.tool_list.currentRowChanged.connect(self._on_tool_changed)
        self.tool_list.setCurrentRow(0)
        self._update_window_title()
        self._rebuild_focus_chain()

        if warnings:
            self.statusBar().showMessage("部分工具加载失败：" + "；".join(warnings))

        self._refresh_theme()

    # ------------------------------------------------------------------ #
    # 高对比度
    # ------------------------------------------------------------------ #
    def _on_hc_toggled(self, checked: bool) -> None:
        if self._suppress_hc_signal:
            return
        self._manual_high_contrast = bool(checked)
        self._settings.setValue("high_contrast/manual", self._manual_high_contrast)
        self._refresh_theme()

    def _refresh_theme(self) -> None:
        """按「系统优先、其次内置开关」的规则应用主题。"""
        app = QApplication.instance()
        system_hc = is_high_contrast()

        self._suppress_hc_signal = True
        try:
            if system_hc:
                apply_theme(app, MODE_SYSTEM_HC)
                self.hc_toggle.setEnabled(False)
                self.hc_toggle.setChecked(True)
                status = "系统高对比度已开启（配色由系统接管）"
                toggle_hint = "系统高对比度已开启，配色由系统接管，无法在此关闭"
            else:
                self.hc_toggle.setEnabled(True)
                self.hc_toggle.setChecked(self._manual_high_contrast)
                mode = (
                    MODE_BUILTIN_HC
                    if self._manual_high_contrast
                    else MODE_NORMAL
                )
                apply_theme(app, mode)
                status = (
                    "内置高对比度：已开启"
                    if self._manual_high_contrast
                    else "高对比度：已关闭"
                )
                toggle_hint = "在应用内启用黑底白字的高对比度配色 快捷键 Ctrl+Shift+H"

            action = getattr(self, "_hc_action", None)
            if action is not None:
                action.setChecked(
                    True if system_hc else self._manual_high_contrast
                )
                action.setEnabled(not system_hc)
        finally:
            self._suppress_hc_signal = False

        self.high_contrast_label.setText(status)
        self.high_contrast_label.setAccessibleName(status)
        self.high_contrast_label.setAccessibleDescription(status)
        self.hc_toggle.setAccessibleDescription(toggle_hint)
        self._rebuild_focus_chain()

    def on_system_theme_changed(self) -> None:
        """系统主题 / 调色板变化时重新计算有效主题。"""
        self._refresh_theme()

    # ------------------------------------------------------------------ #
    # 菜单栏
    # ------------------------------------------------------------------ #
    def _build_menus(self) -> None:
        menubar = self.menuBar()
        describe(menubar, "菜单栏", "包含文件、视图与帮助菜单")

        file_menu = menubar.addMenu("文件(&F)")
        describe(file_menu, "文件菜单")
        quit_action = QAction("退出(&X)", self)
        quit_action.setShortcut(QKeySequence("Ctrl+Q"))
        quit_action.setStatusTip("退出 MyEpubTools")
        quit_action.setWhatsThis("退出应用程序")
        quit_action.triggered.connect(self.close)
        file_menu.addAction(quit_action)

        view_menu = menubar.addMenu("视图(&V)")
        describe(view_menu, "视图菜单")
        focus_action = QAction("切换侧边栏与工具页焦点(&L)", self)
        focus_action.setShortcut(QKeySequence("F6"))
        focus_action.setStatusTip("在工具列表与工具页之间切换焦点")
        focus_action.setWhatsThis("F6：在侧边栏与当前工具页之间切换键盘焦点")
        focus_action.triggered.connect(self._toggle_pane_focus)
        view_menu.addAction(focus_action)

        self._hc_action = QAction("高对比度模式(&H)", self)
        self._hc_action.setCheckable(True)
        self._hc_action.setShortcut(QKeySequence("Ctrl+Shift+H"))
        self._hc_action.setStatusTip("切换内置高对比度配色")
        self._hc_action.setWhatsThis("在应用内启用黑底白字的高对比度配色")
        self._hc_action.toggled.connect(self._on_hc_action_toggled)
        view_menu.addAction(self._hc_action)

        help_menu = menubar.addMenu("帮助(&H)")
        describe(help_menu, "帮助菜单")
        help_action = QAction("键盘与读屏说明(&K)", self)
        help_action.setShortcut(QKeySequence("F1"))
        help_action.setStatusTip("查看键盘操作与屏幕阅读器说明")
        help_action.setWhatsThis("列出本应用的键盘快捷键与读屏使用说明")
        help_action.triggered.connect(self._show_help_page)
        help_menu.addAction(help_action)

    def _on_hc_action_toggled(self, checked: bool) -> None:
        if self._suppress_hc_signal:
            return
        self._manual_high_contrast = bool(checked)
        self._settings.setValue("high_contrast/manual", self._manual_high_contrast)
        self._refresh_theme()

    def _show_help_page(self) -> None:
        """切换到应用内帮助页（侧边栏按钮、帮助菜单与 F1 共用）。"""
        self._suppress_tool_signal = True
        try:
            self.tool_list.clearSelection()
            self.tool_list.setCurrentRow(-1)
        finally:
            self._suppress_tool_signal = False
        self.stack.setCurrentIndex(self._help_index)
        self._update_window_title()
        self._rebuild_focus_chain()
        first = self.help_page.first_focus_widget()
        if first is not None:
            first.setFocus(Qt.FocusReason.OtherFocusReason)

    def _return_to_tool(self) -> None:
        """从帮助页返回上一次使用的工具页。"""
        row = self._last_tool_row
        if row < 0 or row >= self.tool_list.count():
            row = 0
        self.tool_list.setCurrentRow(row)
        # 即使行号没有变化（帮助页可能没有清掉当前行），也强制切回工具页。
        self._on_tool_changed(row)
        self.tool_list.setFocus(Qt.FocusReason.OtherFocusReason)

    # ------------------------------------------------------------------ #
    # 焦点导航
    # ------------------------------------------------------------------ #
    def _on_tool_changed(self, row: int) -> None:
        if self._suppress_tool_signal or row < 0:
            return
        self._last_tool_row = row
        self.stack.setCurrentIndex(row)
        self._update_window_title()
        self._rebuild_focus_chain()

    def _on_page_busy(self, busy: bool) -> None:
        self._update_window_title(busy=busy)

    def _update_window_title(self, busy: bool = False) -> None:
        page = self.stack.currentWidget()
        if page is self.help_page:
            name = "MyEpubTools — 快捷键与使用说明"
        else:
            title = getattr(getattr(page, "adapter", None), "title", "")
            name = "MyEpubTools" if not title else f"MyEpubTools — {title}"
        if busy:
            name += "（处理中）"
        self.setWindowTitle(name)

    def _rebuild_focus_chain(self) -> None:
        """把侧边栏、帮助入口与当前页面接进同一条 Tab 链。"""
        page = self.stack.currentWidget()
        if page is None:
            return
        page.rebuild_tab_order()
        first = page.first_focus_widget()
        last = page.last_focus_widget()

        QWidget.setTabOrder(self.tool_list, self.help_button)
        QWidget.setTabOrder(self.help_button, self.hc_toggle)
        if first is not None:
            QWidget.setTabOrder(self.hc_toggle, first)
        if last is not None:
            QWidget.setTabOrder(last, self.tool_list)

    def _focus_tool_list(self) -> None:
        self.tool_list.setFocus(Qt.FocusReason.OtherFocusReason)

    def _focus_content(self) -> None:
        page = self.stack.currentWidget()
        first = page.first_focus_widget() if page is not None else None
        if first is not None:
            first.setFocus(Qt.FocusReason.OtherFocusReason)
        else:
            self._focus_tool_list()

    def _toggle_pane_focus(self) -> None:
        """F6：在侧边栏与当前工具页之间切换焦点。"""
        focus = QApplication.focusWidget()
        in_sidebar = focus is not None and (
            focus is self.tool_list
            or focus is self.help_button
            or focus is self.hc_toggle
            or self.tool_list.isAncestorOf(focus)
        )
        if in_sidebar:
            self._focus_content()
        else:
            self._focus_tool_list()

    def eventFilter(self, obj, event):  # noqa: N802 - Qt 命名
        if obj is self.tool_list and event.type() == QEvent.Type.KeyPress:
            if event.key() in (
                Qt.Key.Key_Right,
                Qt.Key.Key_Return,
                Qt.Key.Key_Enter,
            ):
                self._focus_content()
                return True
        return super().eventFilter(obj, event)

    def showEvent(self, event):  # noqa: N802 - Qt 命名
        super().showEvent(event)
        QTimer.singleShot(0, self._rebuild_focus_chain)
        QTimer.singleShot(0, self._focus_tool_list)


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("MyEpubTools")
    app.setApplicationDisplayName("MyEpubTools")
    app.setFont(_app_font())
    app.setWindowIcon(make_app_icon())

    adapters, warnings = discover_adapters()
    if not adapters:
        QMessageBox.critical(
            None,
            "无法启动",
            "没有发现可用的工具。请确认已安装依赖"
            "（Flask、beautifulsoup4、opencc、PyQt6）。\n\n" + "\n".join(warnings),
        )
        return 1

    window = MainWindow(adapters, warnings)

    watcher = ThemeWatcher(app)
    watcher.changed.connect(window.on_system_theme_changed)
    # 保持引用，避免 Python 包装对象被回收后信号连接失效。
    window._theme_watcher = watcher

    window.show()

    if "--smoke" in sys.argv:
        # 自检开关：无界面环境下启动 1.2 秒后自动退出，供回归脚本调用。
        QTimer.singleShot(1200, app.quit)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
