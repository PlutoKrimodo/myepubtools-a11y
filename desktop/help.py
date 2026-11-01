# -*- coding: utf-8 -*-
"""应用内「快捷键与使用说明」页。

快捷键不再是弹一次就消失的对话框：侧边栏的「快捷键与说明」按钮、
帮助菜单与 F1 都会切换到本页。页面本身可滚动、可读屏，
并纳入主窗口的 Tab 链。
"""
from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from desktop.accessibility import describe

_HELP_HTML = """
<h2>键盘导航</h2>
<ul>
  <li>Tab / Shift+Tab：在控件之间前后移动，可进入和离开右侧功能区。</li>
  <li>↑ / ↓：在左侧工具列表中切换工具。</li>
  <li>→ / Enter：从工具列表进入当前工具页。</li>
  <li>F6：在侧边栏与工具页之间切换焦点。</li>
  <li>Alt+F / V / H：打开文件 / 视图 / 帮助菜单。</li>
  <li>F1：打开本说明页；Ctrl+Q：退出。</li>
</ul>

<h2>处理流程</h2>
<ol>
  <li>在「选择文件」区按 Alt+O 选择 EPUB，可一次选择多个文件。</li>
  <li>按 Tab 进入设置区；空格勾选复选框，方向键选择下拉项。</li>
  <li>若该工具支持预览，按 Alt+P 先看效果；按 Alt+R 开始处理。</li>
  <li>处理完成后焦点落在结果摘要；按 Alt+S 保存结果。</li>
</ol>

<h2>读屏说明</h2>
<ul>
  <li>按钮、输入框、下拉框、复选框、列表和菜单都设置了可访问名称与描述。</li>
  <li>标有 * 的字段为必填项；校验失败时会聚焦第一个缺失字段，并朗读「必填项，当前为空」。</li>
  <li>每个工具页分为「01 选择文件」「02 设置」「03 处理结果」三组。</li>
  <li>处理失败使用模态对话框播报；处理完成聚焦结果摘要。</li>
</ul>

<h2>高对比度</h2>
<ul>
  <li>侧边栏底部「高对比度模式」启用内置黑底白字主题，选择会被记住。</li>
  <li>Windows 系统高对比度开启时优先跟随系统配色。</li>
  <li>Ctrl+Shift+H 也可切换内置高对比度。</li>
</ul>

<h2>按钮快捷键</h2>
<p>每个按钮的文字后面都直接标有对应快捷键，按 Alt+字母即可触发。</p>
<ul>
  <li>Alt+O 选择文件、Alt+B 浏览、Alt+L 读取、Alt+P 预览、Alt+R 开始处理、Alt+S 保存结果。</li>
  <li>Alt+U / D / X 上移 / 下移 / 移除，Alt+A / C 全选 / 清空。</li>
  <li>帮助页的「返回工具」为 Alt+B。</li>
</ul>
"""


class HelpPage(QWidget):
    """常驻的帮助页，与工具页共享页头与主题。"""

    def __init__(self, on_back=None, parent=None):
        super().__init__(parent)
        self.setObjectName("contentRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(40, 34, 48, 40)
        layout.setSpacing(16)

        header = QHBoxLayout()
        header.setSpacing(12)
        title = QLabel("快捷键与使用说明")
        title.setObjectName("pageTitle")
        describe(title, "快捷键与使用说明")
        header.addWidget(title)
        header.addStretch(1)
        seal = QLabel("助")
        seal.setObjectName("seal")
        seal.setFixedSize(40, 40)
        seal.setAlignment(Qt.AlignmentFlag.AlignCenter)
        seal.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        describe(seal, "装饰性印章", "仅装饰，无操作")
        header.addWidget(seal)
        layout.addLayout(header)

        subtitle = QLabel("所有功能都可以只靠键盘与屏幕阅读器完成。")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        self._browser = QTextBrowser()
        self._browser.setObjectName("helpView")
        self._browser.setOpenExternalLinks(False)
        self._browser.setTabChangesFocus(True)
        self._browser.setHtml(_HELP_HTML)
        describe(
            self._browser,
            "快捷键与使用说明",
            "包含键盘导航、处理流程、读屏说明、高对比度与按钮助记符",
        )
        layout.addWidget(self._browser, 1)

        row = QHBoxLayout()
        self._back_button = QPushButton("返回工具 (Alt+B)")
        self._back_button.setMinimumHeight(34)
        self._back_button.setMinimumWidth(120)
        self._back_button.setToolTip("返回工具（快捷键 Alt+B）")
        describe(self._back_button, "返回工具", "回到工具页 快捷键 Alt+B")
        self._shortcut = QShortcut(QKeySequence("Alt+B"), self._back_button)
        self._shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._shortcut.activated.connect(self._back_button.click)
        self._shortcut.setEnabled(False)
        if on_back is not None:
            self._back_button.clicked.connect(on_back)
        row.addWidget(self._back_button)
        row.addStretch(1)
        layout.addLayout(row)

        QWidget.setTabOrder(self._browser, self._back_button)

    def showEvent(self, event):  # noqa: N802 - Qt 命名
        super().showEvent(event)
        self._shortcut.setEnabled(True)

    def hideEvent(self, event):  # noqa: N802 - Qt 命名
        self._shortcut.setEnabled(False)
        super().hideEvent(event)

    # ------------------------------------------------------------------ #
    # 供主窗口建立焦点链
    # ------------------------------------------------------------------ #
    def first_focus_widget(self) -> QWidget:
        return self._browser

    def last_focus_widget(self) -> QWidget:
        return self._back_button

    def rebuild_tab_order(self) -> None:
        QWidget.setTabOrder(self._browser, self._back_button)
