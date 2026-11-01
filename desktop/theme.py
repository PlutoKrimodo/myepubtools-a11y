# -*- coding: utf-8 -*-
"""桌面主题：书卷主题 + 内置高对比度主题。

三种模式
--------
* :data:`MODE_NORMAL`：书卷活字风（米纸、墨色、朱砂），与 Web 端一致；
* :data:`MODE_BUILTIN_HC`：**内置高对比度**（纯黑底、纯白字、明黄强调），
  由应用内的「高对比度模式」开关启用，不依赖系统设置；
* :data:`MODE_SYSTEM_HC`：系统已开启高对比度时，**清空样式表**，
  颜色与焦点框完全交给 Windows 调色板，尊重用户在系统里选的配色方案。

无障碍原则
----------
内置高对比度只用黑 / 白 / 明黄三种颜色，避免任何中间灰；禁用态用虚线边框
而不是降低对比度；焦点框加粗到 2–3px，保证键盘用户能看见。
"""
from __future__ import annotations

from string import Template

from PyQt6.QtGui import QColor, QPalette

# 纸与墨
PAPER = "#f7f4ee"
PAPER_DEEP = "#efeade"
SHEET = "#fffdf8"
RULE = "#ddd6c8"
RULE_STRONG = "#c3baa9"
INK = "#1f1c18"
INK_SOFT = "#575047"
INK_NOTE = "#6e6659"
INK_FAINT = "#8d8578"

# 朱砂与竹青
SEAL = "#a8402c"
SEAL_HOVER = "#8f3524"
SEAL_PRESSED = "#7a2c1e"
BAMBOO = "#3f6b52"

# 夜色（侧边栏）
NIGHT = "#141210"
NIGHT_SOFT = "#1d1a16"
NIGHT_LINE = "#332e27"

# 内置高对比度
HC_BG = "#000000"
HC_TEXT = "#ffffff"
HC_ACCENT = "#ffd700"

SERIF = '"SimSun"'

MODE_NORMAL = "normal"
MODE_BUILTIN_HC = "builtin_hc"
MODE_SYSTEM_HC = "system_hc"




# --------------------------------------------------------------------------- #
# 书卷主题
# --------------------------------------------------------------------------- #
_NORMAL_QSS = Template(
    """
/* ------------------------------------------------------------------ 基础 */
QWidget {
    color: $INK;
    font-family: "Microsoft YaHei UI";
    font-size: 13px;
}
QWidget#centralRoot, QWidget#contentRoot, QStackedWidget#contentRoot {
    background: $PAPER;
}
QMenuBar {
    background: $PAPER;
    color: $INK;
}
QMenuBar::item {
    background: transparent;
    padding: 6px 10px;
}
QMenuBar::item:selected {
    background: $SEAL;
    color: #fff8f0;
}
QMenu {
    background: $SHEET;
    color: $INK;
    border: 1px solid $RULE;
}
QMenu::item {
    padding: 6px 26px 6px 22px;
}
QMenu::item:selected {
    background: $SEAL;
    color: #fff8f0;
}
QMenu::item:disabled {
    color: $INK_FAINT;
}
QMenu::separator {
    height: 1px;
    background: $RULE;
    margin: 4px 8px;
}
QMenu::indicator {
    width: 14px;
    height: 14px;
}

/* ---------------------------------------------------------------- 侧边栏 */
QWidget#sidebar {
    background: $NIGHT;
}
QLabel#sidebarTitle {
    color: #efe9df;
    font-family: $SERIF;
    font-size: 15pt;
    font-weight: 600;
}
QLabel#sidebarFoot {
    color: #8d8478;
    font-size: 9pt;
}
QCheckBox#hcToggle {
    color: #efe9df;
    spacing: 8px;
}
QCheckBox#hcToggle::indicator {
    width: 14px;
    height: 14px;
    border: 1px solid #b3aa9c;
    border-radius: 2px;
    background: transparent;
}
QCheckBox#hcToggle::indicator:checked {
    background: $SEAL;
    border-color: $SEAL;
}
QPushButton#helpLink {
    background: transparent;
    border: 1px solid transparent;
    color: #b3aa9c;
    text-align: left;
    padding: 10px 14px;
}
QPushButton#helpLink:hover {
    background: $NIGHT_SOFT;
    color: #f5efe6;
}
QPushButton#helpLink:focus {
    border: 1px solid $SEAL;
    color: #ffffff;
}
QListWidget#toolList {
    background: transparent;
    border: 1px solid transparent;
    outline: none;
    padding: 4px 0;
}
QListWidget#toolList:focus {
    border: 1px solid $SEAL;
}
QListWidget#toolList::item {
    color: #b3aa9c;
    padding: 11px 14px;
    border-left: 3px solid transparent;
}
QListWidget#toolList::item:hover {
    background: $NIGHT_SOFT;
    color: #f5efe6;
}
QListWidget#toolList::item:selected {
    background: $NIGHT_SOFT;
    color: #ffffff;
    border-left: 3px solid $SEAL;
}

/* ---------------------------------------------------------------- 页头 */
QLabel#pageTitle {
    color: $INK;
    font-family: $SERIF;
    font-size: 20pt;
    font-weight: 600;
}
QLabel#pageSubtitle, QLabel[role="hint"] {
    color: $INK_NOTE;
}
QLabel#seal {
    background: $SEAL;
    color: #fff8f0;
    border-radius: 3px;
    font-family: $SERIF;
    font-size: 17pt;
    font-weight: 600;
}

/* ---------------------------------------------------------------- 章节 */
QGroupBox {
    border: none;
    border-top: 1px solid $RULE;
    margin-top: 22px;
    padding-top: 14px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 0;
    padding: 0 8px 0 0;
    color: $INK;
    font-family: $SERIF;
    font-size: 12pt;
    font-weight: 600;
}

/* ---------------------------------------------------------------- 输入 */
QLineEdit, QPlainTextEdit, QTextBrowser, QComboBox, QSpinBox {
    background: $PAPER_DEEP;
    border: 1px solid $RULE;
    border-radius: 3px;
    padding: 6px 8px;
    selection-background-color: $SEAL;
    selection-color: #fff8f0;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextBrowser:focus,
QComboBox:focus, QSpinBox:focus {
    border: 1px solid $SEAL;
    background: $SHEET;
}
QLineEdit[readOnly="true"] {
    background: #f2eee4;
    color: $INK_SOFT;
}
QComboBox QAbstractItemView {
    background: $SHEET;
    border: 1px solid $RULE;
    selection-background-color: $SEAL;
    selection-color: #fff8f0;
    outline: none;
}

/* ---------------------------------------------------------------- 按钮 */
QPushButton {
    background: $SHEET;
    border: 1px solid $RULE_STRONG;
    border-radius: 3px;
    padding: 7px 16px;
    color: $INK;
}
QPushButton:hover {
    background: #f2eee4;
    border-color: $SEAL;
}
QPushButton:pressed {
    background: #e9e2d4;
}
QPushButton:focus {
    border: 2px solid $SEAL;
    padding: 6px 15px;
}
QPushButton:disabled {
    background: #f2eee4;
    border-color: $RULE;
    color: $INK_FAINT;
}
QPushButton#primary {
    background: $SEAL;
    border: 1px solid $SEAL;
    color: #fff8f0;
    font-weight: 600;
}
QPushButton#primary:hover {
    background: $SEAL_HOVER;
    border-color: $SEAL_HOVER;
}
QPushButton#primary:pressed {
    background: $SEAL_PRESSED;
}
QPushButton#primary:focus {
    border: 2px solid $INK;
}
QPushButton#primary:disabled {
    background: #c9a69d;
    border-color: #c9a69d;
    color: $PAPER;
}

/* ---------------------------------------------------------------- 列表 */
QListWidget#fileList, QListWidget#checkList {
    background: $PAPER_DEEP;
    border: 1px solid $RULE;
    border-radius: 3px;
    padding: 4px;
    outline: none;
}
QListWidget#fileList::item, QListWidget#checkList::item {
    padding: 6px 8px;
    border-radius: 2px;
}
QListWidget#fileList::item:selected, QListWidget#checkList::item:selected {
    background: $SEAL;
    color: #fff8f0;
}
QListWidget#fileList::item:disabled, QListWidget#checkList::item:disabled {
    color: $INK_FAINT;
}
QListWidget#fileList:focus, QListWidget#checkList:focus {
    border: 1px solid $SEAL;
}
QTextBrowser#helpView {
    background: $SHEET;
    border: 1px solid $RULE;
    border-radius: 3px;
    padding: 16px;
}

/* ---------------------------------------------------------------- 滚动 */
QScrollArea#toolScroll {
    background: transparent;
    border: none;
}
QScrollBar:vertical {
    background: transparent;
    width: 10px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: $RULE_STRONG;
    border-radius: 5px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: $SEAL;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: transparent;
}
QScrollBar:horizontal {
    background: transparent;
    height: 10px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: $RULE_STRONG;
    border-radius: 5px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: $SEAL;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: transparent;
}

/* ---------------------------------------------------------------- 其它 */
QLabel#statusLabel {
    color: $INK_NOTE;
}
QCheckBox {
    color: $INK;
    spacing: 8px;
}
QToolTip {
    background: $NIGHT;
    color: #f5efe6;
    border: 1px solid $NIGHT_LINE;
    padding: 6px 8px;
}
"""
)


def build_normal_stylesheet() -> str:
    """书卷主题。"""
    return _NORMAL_QSS.substitute(
        PAPER=PAPER,
        PAPER_DEEP=PAPER_DEEP,
        SHEET=SHEET,
        RULE=RULE,
        RULE_STRONG=RULE_STRONG,
        INK=INK,
        INK_SOFT=INK_SOFT,
        INK_NOTE=INK_NOTE,
        INK_FAINT=INK_FAINT,
        SEAL=SEAL,
        SEAL_HOVER=SEAL_HOVER,
        SEAL_PRESSED=SEAL_PRESSED,
        NIGHT=NIGHT,
        NIGHT_SOFT=NIGHT_SOFT,
        NIGHT_LINE=NIGHT_LINE,
        SERIF=SERIF,
    )


# --------------------------------------------------------------------------- #
# 内置高对比度主题（黑底 / 白字 / 明黄）
# --------------------------------------------------------------------------- #
_HC_QSS = """
/* ------------------------------------------------------------------ 基础 */
QWidget {
    color: #ffffff;
    font-family: "Microsoft YaHei UI";
    font-size: 14px;
}
QWidget#centralRoot, QWidget#contentRoot, QStackedWidget#contentRoot,
QWidget#sidebar {
    background: #000000;
}
QMenuBar {
    background: #000000;
    color: #ffffff;
}
QMenuBar::item {
    background: transparent;
    padding: 6px 10px;
}
QMenuBar::item:selected {
    background: #ffd700;
    color: #000000;
}
QMenu {
    background: #000000;
    color: #ffffff;
    border: 2px solid #ffffff;
}
QMenu::item {
    padding: 6px 26px 6px 22px;
}
QMenu::item:selected {
    background: #ffd700;
    color: #000000;
}
QMenu::item:disabled {
    color: #ffffff;
}
QMenu::separator {
    height: 2px;
    background: #ffffff;
    margin: 4px 8px;
}
QMenu::indicator {
    width: 15px;
    height: 15px;
}

/* ---------------------------------------------------------------- 侧边栏 */
QLabel#sidebarTitle {
    color: #ffffff;
    font-family: "SimSun";
    font-size: 15pt;
    font-weight: 600;
}
QLabel#sidebarFoot {
    color: #ffd700;
    font-size: 9pt;
}
QCheckBox#hcToggle {
    color: #ffffff;
    spacing: 8px;
}
QCheckBox#hcToggle::indicator {
    width: 15px;
    height: 15px;
    border: 2px solid #ffffff;
    border-radius: 2px;
    background: #000000;
}
QCheckBox#hcToggle::indicator:checked {
    background: #ffd700;
    border-color: #ffd700;
}
QPushButton#helpLink {
    background: #000000;
    border: 2px solid transparent;
    color: #ffffff;
    text-align: left;
    padding: 10px 14px;
}
QPushButton#helpLink:hover {
    background: #ffffff;
    color: #000000;
}
QPushButton#helpLink:focus {
    border: 2px solid #ffd700;
}
QListWidget#toolList {
    background: #000000;
    border: 2px solid transparent;
    outline: none;
    padding: 4px 0;
}
QListWidget#toolList:focus {
    border: 2px solid #ffd700;
}
QListWidget#toolList::item {
    color: #ffffff;
    padding: 11px 14px;
    border-left: 3px solid transparent;
}
QListWidget#toolList::item:hover {
    background: #ffffff;
    color: #000000;
}
QListWidget#toolList::item:selected {
    background: #ffd700;
    color: #000000;
    border-left: 3px solid #ffffff;
}

/* ---------------------------------------------------------------- 页头 */
QLabel#pageTitle {
    color: #ffffff;
    font-family: "SimSun";
    font-size: 20pt;
    font-weight: 600;
}
QLabel#pageSubtitle, QLabel[role="hint"] {
    color: #ffd700;
}
QLabel#seal {
    background: #ffd700;
    color: #000000;
    border-radius: 3px;
    font-family: "SimSun";
    font-size: 17pt;
    font-weight: 600;
}

/* ---------------------------------------------------------------- 章节 */
QGroupBox {
    border: none;
    border-top: 2px solid #ffffff;
    margin-top: 22px;
    padding-top: 14px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 0;
    padding: 0 8px 0 0;
    color: #ffffff;
    font-family: "SimSun";
    font-size: 12pt;
    font-weight: 600;
}

/* ---------------------------------------------------------------- 输入 */
QLineEdit, QPlainTextEdit, QTextBrowser, QComboBox, QSpinBox {
    background: #000000;
    color: #ffffff;
    border: 2px solid #ffffff;
    border-radius: 3px;
    padding: 6px 8px;
    selection-background-color: #ffd700;
    selection-color: #000000;
}
QLineEdit:focus, QPlainTextEdit:focus, QTextBrowser:focus,
QComboBox:focus, QSpinBox:focus {
    border: 2px solid #ffd700;
}
QLineEdit[readOnly="true"] {
    background: #000000;
    color: #ffffff;
}
QComboBox QAbstractItemView {
    background: #000000;
    color: #ffffff;
    border: 2px solid #ffffff;
    selection-background-color: #ffd700;
    selection-color: #000000;
    outline: none;
}

/* ---------------------------------------------------------------- 按钮 */
QPushButton {
    background: #000000;
    color: #ffffff;
    border: 2px solid #ffffff;
    border-radius: 3px;
    padding: 7px 16px;
}
QPushButton:hover {
    background: #ffffff;
    color: #000000;
}
QPushButton:pressed {
    background: #ffd700;
    color: #000000;
}
QPushButton:focus {
    border: 3px solid #ffd700;
    padding: 6px 15px;
}
QPushButton:disabled {
    background: #000000;
    color: #ffffff;
    border: 2px dashed #ffffff;
}
QPushButton#primary {
    background: #ffd700;
    color: #000000;
    border: 2px solid #ffd700;
    font-weight: 600;
}
QPushButton#primary:hover {
    background: #ffffff;
    color: #000000;
    border-color: #ffffff;
}
QPushButton#primary:focus {
    border: 3px solid #ffffff;
}
QPushButton#primary:disabled {
    background: #000000;
    color: #ffd700;
    border: 2px dashed #ffd700;
}

/* ---------------------------------------------------------------- 列表 */
QListWidget#fileList, QListWidget#checkList {
    background: #000000;
    color: #ffffff;
    border: 2px solid #ffffff;
    border-radius: 3px;
    padding: 4px;
    outline: none;
}
QListWidget#fileList::item, QListWidget#checkList::item {
    color: #ffffff;
    padding: 6px 8px;
}
QListWidget#fileList::item:selected, QListWidget#checkList::item:selected {
    background: #ffd700;
    color: #000000;
}
QListWidget#fileList::item:disabled, QListWidget#checkList::item:disabled {
    color: #ffffff;
}
QListWidget#fileList:focus, QListWidget#checkList:focus {
    border: 2px solid #ffd700;
}
QTextBrowser#helpView {
    background: #000000;
    border: 2px solid #ffffff;
    padding: 16px;
}

/* ---------------------------------------------------------------- 滚动 */
QScrollArea#toolScroll {
    background: transparent;
    border: none;
}
QScrollBar:vertical {
    background: #000000;
    width: 12px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: #ffffff;
    border-radius: 6px;
    min-height: 30px;
}
QScrollBar::handle:vertical:hover {
    background: #ffd700;
}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0;
}
QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {
    background: #000000;
}
QScrollBar:horizontal {
    background: #000000;
    height: 12px;
    margin: 0;
}
QScrollBar::handle:horizontal {
    background: #ffffff;
    border-radius: 6px;
    min-width: 30px;
}
QScrollBar::handle:horizontal:hover {
    background: #ffd700;
}
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    width: 0;
}
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal {
    background: #000000;
}

/* ---------------------------------------------------------------- 其它 */
QLabel#statusLabel {
    color: #ffffff;
}
QCheckBox {
    color: #ffffff;
    spacing: 8px;
}
QToolTip {
    background: #000000;
    color: #ffffff;
    border: 2px solid #ffffff;
    padding: 6px 8px;
}
"""


def build_high_contrast_stylesheet() -> str:
    """内置高对比度主题（黑底、白字、明黄强调）。"""
    return _HC_QSS


# --------------------------------------------------------------------------- #
# 应用
# --------------------------------------------------------------------------- #
def _hc_palette() -> QPalette:
    """给原生绘制元素（下拉箭头、滚动条等）用的高对比度调色板。"""
    palette = QPalette()
    black = QColor(HC_BG)
    white = QColor(HC_TEXT)
    yellow = QColor(HC_ACCENT)

    palette.setColor(QPalette.ColorRole.Window, black)
    palette.setColor(QPalette.ColorRole.WindowText, white)
    palette.setColor(QPalette.ColorRole.Base, black)
    palette.setColor(QPalette.ColorRole.AlternateBase, black)
    palette.setColor(QPalette.ColorRole.Text, white)
    palette.setColor(QPalette.ColorRole.Button, black)
    palette.setColor(QPalette.ColorRole.ButtonText, white)
    palette.setColor(QPalette.ColorRole.BrightText, yellow)
    palette.setColor(QPalette.ColorRole.Highlight, yellow)
    palette.setColor(QPalette.ColorRole.HighlightedText, black)
    palette.setColor(QPalette.ColorRole.ToolTipBase, black)
    palette.setColor(QPalette.ColorRole.ToolTipText, white)
    palette.setColor(QPalette.ColorRole.PlaceholderText, yellow)
    palette.setColor(QPalette.ColorRole.Link, yellow)
    palette.setColor(QPalette.ColorRole.LinkVisited, yellow)

    for role in (
        QPalette.ColorRole.WindowText,
        QPalette.ColorRole.Text,
        QPalette.ColorRole.ButtonText,
    ):
        palette.setColor(QPalette.ColorGroup.Disabled, role, white)
    return palette


def apply_theme(app, mode: str) -> None:
    """按模式应用主题。

    * ``MODE_NORMAL``：书卷主题；
    * ``MODE_BUILTIN_HC``：内置高对比度主题；
    * ``MODE_SYSTEM_HC``：清空样式表，跟随系统调色板。
    """
    if mode == MODE_BUILTIN_HC:
        app.setPalette(_hc_palette())
        app.setStyleSheet(build_high_contrast_stylesheet())
        return

    # 非内置高对比度：恢复与系统一致的调色板，避免残留上一套自定义配色。
    app.setPalette(app.style().standardPalette())
    app.setStyleSheet(
        build_normal_stylesheet() if mode == MODE_NORMAL else ""
    )
