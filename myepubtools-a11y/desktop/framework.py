# -*- coding: utf-8 -*-
"""通用工具页渲染器：把 ``PageConfig`` 声明渲染成原生 Qt 控件。

一个 :class:`ToolPage` 对应一个工具。页面形态完全由工具模块里的
``PageConfig``（标题、模式、字段、上传、结果）决定，因此 7 个配置驱动的
工具共用同一套界面代码，不重复实现。

无障碍：
    * 每个控件都调用 :func:`desktop.accessibility.describe` 设置可访问名称/描述；
    * 控件角色与状态由 Qt 的 QAccessible 自动暴露；
    * 控件按「视觉顺序」创建并显式串联 Tab 顺序；
    * 不硬编码任何前景/背景色，高对比度模式由系统调色板接管。
"""
from __future__ import annotations

import io
import zipfile
from pathlib import Path

from PyQt6.QtCore import QEvent, QThread, Qt, pyqtSignal
from PyQt6.QtGui import QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from desktop.accessibility import describe
from desktop.adapters.base import BaseAdapter, RunResult
from modules.toolkit import seal_mark, unique_name


# --------------------------------------------------------------------------- #
# 小工具
# --------------------------------------------------------------------------- #
def _format_error(exc: BaseException) -> str:
    message = str(exc).strip()
    return message or type(exc).__name__


def _qt_filter(accept: str) -> str:
    """把 ``.epub,application/epub+zip`` 转成 QFileDialog 的过滤器文本。"""
    extensions = [
        part.strip()
        for part in (accept or "").split(",")
        if part.strip().startswith(".")
    ]
    if not extensions:
        return "所有文件 (*)"
    patterns = " ".join(f"*{extension}" for extension in extensions)
    return f"支持的文件 ({patterns});;所有文件 (*)"


def _make_button(text: str, mnemonic: str, description: str = "") -> QPushButton:
    """创建按钮：文案直接标出快捷键，并注册真实的 Alt 快捷键。"""
    shortcut = f"Alt+{mnemonic}"
    button = QPushButton(f"{text} ({shortcut})")
    button.setMinimumHeight(34)
    button.setMinimumWidth(120)
    hint = (
        f"{description} 快捷键 {shortcut}".strip()
        if description
        else f"快捷键 {shortcut}"
    )
    button.setToolTip(f"{text}（快捷键 {shortcut}）")
    describe(button, text, hint)
    # 窗口级快捷键：焦点在侧边栏时也能触发；页面隐藏时由 show/hideEvent 禁用。
    hotkey = QShortcut(QKeySequence(shortcut), button)
    hotkey.setContext(Qt.ShortcutContext.WindowShortcut)
    hotkey.activated.connect(button.click)
    button._shortcut = hotkey
    return button


def _zip_results(results: list[RunResult]) -> bytes:
    """多本结果打包为 ZIP（仅传输层打包，不是业务逻辑）。"""
    buffer = io.BytesIO()
    used: set[str] = set()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for result in results:
            archive.writestr(unique_name(result.filename, used), result.data)
    return buffer.getvalue()


def _set_combo(combo: QComboBox, value) -> None:
    index = combo.findData(value)
    if index < 0 and value:
        index = combo.findText(str(value))
    if index >= 0:
        combo.setCurrentIndex(index)


def _checked_values(widget: QListWidget) -> list:
    values = []
    for index in range(widget.count()):
        item = widget.item(index)
        value = item.data(Qt.ItemDataRole.UserRole)
        if value is None:
            continue
        if item.checkState() == Qt.CheckState.Checked:
            values.append(value)
    return values


def _populate_checklist(widget: QListWidget, items) -> None:
    widget.clear()
    for entry in items or []:
        if isinstance(entry, dict):
            value = entry.get("value")
            label = entry.get("label") or value
        else:
            value = str(entry)
            label = value
        item = QListWidgetItem(str(label))
        item.setData(Qt.ItemDataRole.UserRole, value)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
        item.setCheckState(Qt.CheckState.Unchecked)
        widget.addItem(item)

    if widget.count() == 0:
        # 空态占位：避免出现一大块没有任何说明的空白列表
        placeholder = QListWidgetItem("（选择文件后在此列出可选项）")
        placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
        widget.addItem(placeholder)


def _set_all_checked(widget: QListWidget, checked: bool) -> None:
    state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
    for index in range(widget.count()):
        item = widget.item(index)
        if item.data(Qt.ItemDataRole.UserRole) is None:
            continue
        item.setCheckState(state)


class _Worker(QThread):
    """在后台线程执行耗时操作，避免界面卡死（保证「功能不中断」）。"""

    succeeded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, func, parent=None):
        super().__init__(parent)
        self._func = func

    def run(self):  # noqa: D401 - QThread 约定
        try:
            self.succeeded.emit(self._func())
        except Exception as exc:  # noqa: BLE001 - 统一转成可读消息
            self.failed.emit(_format_error(exc))


# --------------------------------------------------------------------------- #
# 工具页
# --------------------------------------------------------------------------- #
class ToolPage(QWidget):
    """按 ``PageConfig`` 渲染的单工具页面。"""

    busy_changed = pyqtSignal(bool)
    chain_dirty = pyqtSignal()

    def __init__(self, adapter: BaseAdapter, parent=None):
        super().__init__(parent)
        self.adapter = adapter
        self.page = adapter.page
        self._files: list[Path] = []
        self._results: list[RunResult] = []
        self._field_getters: dict = {}
        self._field_setters: dict = {}
        self._field_widgets: dict = {}
        self._field_base_desc: dict = {}
        self._filelist_widgets: dict = {}
        self._tab_widgets: list[QWidget] = []
        self._shortcuts: list = []
        self._scroll = None
        self._worker = None
        self._busy = False
        self._section_no = 0

        self.mode_combo: QComboBox | None = None
        self._mode_by_value: dict = {}
        self._load_button: QPushButton | None = None
        self._preview_button: QPushButton | None = None

        self._build_ui()
        # 非当前页面的快捷键先禁用，等 showEvent 再启用。
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)

    # ------------------------------------------------------------------ #
    # 构建界面
    # ------------------------------------------------------------------ #
    def _build_ui(self) -> None:
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        scroll = QScrollArea(self)
        scroll.setObjectName("toolScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        scroll.viewport().setAutoFillBackground(False)
        # 容器不参与 Tab 链，焦点直接落到内部控件上。
        scroll.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        describe(scroll, "工具页面区域")
        outer.addWidget(scroll)
        self._scroll = scroll

        content = QWidget()
        content.setObjectName("contentRoot")
        content.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        content.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(40, 34, 48, 40)
        layout.setSpacing(16)
        scroll.setWidget(content)

        # 页头：衬线标题 + 右上角朱砂印章（与 Web 端一致）
        header = QHBoxLayout()
        header.setSpacing(12)
        title = QLabel(self.page.title)
        title.setObjectName("pageTitle")
        describe(title, self.page.title)
        header.addWidget(title)
        header.addStretch(1)
        seal = QLabel(seal_mark(self.page.title))
        seal.setObjectName("seal")
        seal.setFixedSize(40, 40)
        seal.setAlignment(Qt.AlignmentFlag.AlignCenter)
        seal.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        describe(seal, "装饰性印章", "仅装饰，无操作")
        header.addWidget(seal)
        layout.addLayout(header)

        if self.page.subtitle:
            subtitle = QLabel(self.page.subtitle)
            subtitle.setObjectName("pageSubtitle")
            subtitle.setWordWrap(True)
            layout.addWidget(subtitle)

        self._build_mode(layout)
        self._build_upload(layout)
        if self.page.fields:
            self._build_fields(layout)
        self._build_actions(layout)
        self._build_result(layout)
        layout.addStretch(1)

        if self.mode_combo is not None:
            self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)

        self.rebuild_tab_order()

    def _section_title(self, text: str) -> str:
        """章节标题：与 Web 端一致的 01 / 02 编号。"""
        self._section_no += 1
        return f"{self._section_no:02d}   {text}"

    def _build_mode(self, layout: QVBoxLayout) -> None:
        if not self.page.modes:
            return
        row = QHBoxLayout()
        label = QLabel("处理模式")
        describe(label, "处理模式")
        combo = QComboBox()
        for mode in self.page.modes:
            combo.addItem(mode.label, mode.value)
            self._mode_by_value[mode.value] = mode
        describe(
            combo,
            "处理模式",
            "选择工具的工作模式；切换后文件类型、上传提示与按钮文字会相应变化",
        )
        label.setBuddy(combo)
        row.addWidget(label)
        row.addWidget(combo, 1)
        layout.addLayout(row)

        self.mode_combo = combo
        self._register_focus(combo)

    def _build_upload(self, layout: QVBoxLayout) -> None:
        section = self._section_title("选择文件")
        group = QGroupBox(section)
        describe(group, section)
        box = QVBoxLayout(group)
        box.setSpacing(8)

        self._upload_label = QLabel(self._current_upload_label())
        self._upload_label.setWordWrap(True)
        box.addWidget(self._upload_label)

        self._upload_hint = QLabel(self._current_upload_hint())
        self._upload_hint.setWordWrap(True)
        self._upload_hint.setProperty("role", "hint")
        box.addWidget(self._upload_hint)

        row = QHBoxLayout()
        self._choose_button = _make_button("选择文件", "O", "打开文件选择对话框")
        self._choose_button.clicked.connect(self._choose_files)
        row.addWidget(self._choose_button)
        self._file_count_label = QLabel("尚未选择文件")
        self._file_count_label.setProperty("role", "hint")
        row.addWidget(self._file_count_label, 1)
        box.addLayout(row)

        self._file_list = QListWidget()
        self._file_list.setObjectName("fileList")
        self._file_list.setMaximumHeight(120)
        self._file_list.setVisible(False)
        describe(
            self._file_list, "已选文件列表", "显示已选择的文件；多选时顺序即处理顺序"
        )
        box.addWidget(self._file_list)

        order_row = QHBoxLayout()
        self._up_button = _make_button("上移", "U", "把选中文件上移")
        self._down_button = _make_button("下移", "D", "把选中文件下移")
        self._remove_button = _make_button("移除", "X", "从列表移除选中文件")
        self._up_button.clicked.connect(lambda: self._move_file(-1))
        self._down_button.clicked.connect(lambda: self._move_file(1))
        self._remove_button.clicked.connect(self._remove_selected_file)
        for button in (self._up_button, self._down_button, self._remove_button):
            order_row.addWidget(button)
        order_row.addStretch(1)
        box.addLayout(order_row)

        self._order_hint_label = QLabel(self.page.upload_order_hint or "")
        self._order_hint_label.setWordWrap(True)
        self._order_hint_label.setProperty("role", "hint")
        self._order_hint_label.setVisible(
            bool(self.page.upload_order_hint) and self._allows_multiple()
        )
        box.addWidget(self._order_hint_label)

        layout.addWidget(group)

        self._register_focus(self._choose_button)
        self._register_focus(self._file_list)
        for button in (self._up_button, self._down_button, self._remove_button):
            self._register_focus(button)

        for button in (self._up_button, self._down_button, self._remove_button):
            button.setEnabled(False)

    def _build_fields(self, layout: QVBoxLayout) -> None:
        section = self._section_title(self.page.fields_title)
        group = QGroupBox(section)
        describe(group, section)
        box = QVBoxLayout(group)
        box.setSpacing(12)

        if self.page.fields_hint:
            hint = QLabel(self.page.fields_hint)
            hint.setWordWrap(True)
            hint.setProperty("role", "hint")
            box.addWidget(hint)

        for field in self.page.fields:
            self._add_field(box, field)

        layout.addWidget(group)

    def _add_field(self, box: QVBoxLayout, field) -> None:
        required = bool(getattr(field, "required", False))
        label_text = f"{field.label} *" if required else field.label
        accessible_name = f"{field.label}（必填）" if required else field.label
        description = field.hint or ""
        if required:
            description = f"必填。{description}".strip() if description else "必填"

        if field.type == "checkbox":
            widget = QCheckBox(label_text)
            widget.setChecked(bool(field.default))
            describe(widget, accessible_name, description)
            box.addWidget(widget)
            self._field_getters[field.name] = widget.isChecked
            self._field_setters[field.name] = widget.setChecked
            self._remember_field(field, widget, description)
            self._register_focus(widget)
            if field.hint:
                box.addWidget(self._hint_label(field.hint))
            return

        label = QLabel(label_text)
        describe(label, label_text)
        box.addWidget(label)

        if field.type == "select":
            widget = QComboBox()
            for option in field.options:
                widget.addItem(option.label, option.value)
            _set_combo(widget, field.default)
            describe(widget, accessible_name, description)
            label.setBuddy(widget)
            box.addWidget(widget)
            self._field_getters[field.name] = widget.currentData
            self._field_setters[field.name] = lambda value, w=widget: _set_combo(w, value)
            self._remember_field(field, widget, description)
            self._register_focus(widget)

        elif field.type == "textarea":
            widget = QPlainTextEdit()
            widget.setPlainText(str(field.default or ""))
            widget.setMinimumHeight(max(90, int(field.rows or 6) * 22))
            # 多行输入里 Tab 用来切换焦点，而不是插入制表符。
            widget.setTabChangesFocus(True)
            describe(widget, accessible_name, description)
            label.setBuddy(widget)
            box.addWidget(widget)
            self._field_getters[field.name] = widget.toPlainText
            self._field_setters[field.name] = widget.setPlainText
            self._remember_field(field, widget, description)
            self._register_focus(widget)

        elif field.type == "number":
            widget = QSpinBox()
            widget.setRange(-1_000_000, 1_000_000)
            try:
                widget.setValue(int(field.default or 0))
            except (TypeError, ValueError):
                widget.setValue(0)
            describe(widget, accessible_name, description)
            label.setBuddy(widget)
            box.addWidget(widget)
            self._field_getters[field.name] = widget.value
            self._field_setters[field.name] = widget.setValue
            self._remember_field(field, widget, description)
            self._register_focus(widget)

        elif field.type == "file":
            row = QHBoxLayout()
            edit = QLineEdit()
            edit.setReadOnly(True)
            describe(edit, accessible_name, description)
            browse = _make_button("浏览", "B", f"选择{field.label}")
            browse.clicked.connect(
                lambda _checked=False, target=edit: self._choose_single_file(target)
            )
            row.addWidget(edit, 1)
            row.addWidget(browse)
            box.addLayout(row)
            self._field_getters[field.name] = edit.text
            self._field_setters[field.name] = edit.setText
            label.setBuddy(edit)
            self._remember_field(field, edit, description)
            self._register_focus(edit)
            self._register_focus(browse)

        elif field.type == "filelist":
            widget = QListWidget()
            widget.setObjectName("checkList")
            widget.setMinimumHeight(160)
            describe(widget, accessible_name, description)
            box.addWidget(widget)
            label.setBuddy(widget)
            self._filelist_widgets[field.name] = widget
            _populate_checklist(widget, [])
            self._field_getters[field.name] = lambda w=widget: _checked_values(w)
            self._field_setters[field.name] = lambda items, w=widget: _populate_checklist(
                w, items
            )
            self._remember_field(field, widget, description)
            self._register_focus(widget)

            actions = QHBoxLayout()
            select_all = _make_button("全选", "A", f"全选{field.label}")
            clear = _make_button("清空", "C", f"清空{field.label}")
            select_all.clicked.connect(
                lambda _checked=False, w=widget: _set_all_checked(w, True)
            )
            clear.clicked.connect(
                lambda _checked=False, w=widget: _set_all_checked(w, False)
            )
            actions.addWidget(select_all)
            actions.addWidget(clear)
            actions.addStretch(1)
            box.addLayout(actions)
            self._register_focus(select_all)
            self._register_focus(clear)

        else:  # text
            widget = QLineEdit()
            widget.setText(str(field.default or ""))
            if field.placeholder:
                widget.setPlaceholderText(field.placeholder)
            describe(widget, accessible_name, description)
            label.setBuddy(widget)
            box.addWidget(widget)
            self._field_getters[field.name] = widget.text
            self._field_setters[field.name] = widget.setText
            self._remember_field(field, widget, description)
            self._register_focus(widget)

        if field.hint and field.type != "checkbox":
            box.addWidget(self._hint_label(field.hint))

    def _remember_field(self, field, widget, description: str) -> None:
        """记住字段控件与基础描述，供校验失败时聚焦并临时改写描述。"""
        self._field_widgets[field.name] = widget
        self._field_base_desc[field.name] = description
        for signal_name in (
            "textChanged",
            "currentIndexChanged",
            "toggled",
            "valueChanged",
        ):
            signal = getattr(widget, signal_name, None)
            if signal is not None:
                signal.connect(
                    lambda *_, name=field.name: self._restore_field_description(name)
                )
                break

    def _restore_field_description(self, name: str) -> None:
        widget = self._field_widgets.get(name)
        base = self._field_base_desc.get(name)
        if widget is not None and base is not None:
            widget.setAccessibleDescription(base)

    def _build_actions(self, layout: QVBoxLayout) -> None:
        row = QHBoxLayout()

        if self.page.load_endpoint:
            self._load_button = _make_button(
                self.page.load_label, "L", "重新读取文件信息并回填"
            )
            self._load_button.clicked.connect(lambda: self._start_load())
            row.addWidget(self._load_button)
            self._register_focus(self._load_button)

        if self.page.preview_endpoint:
            self._preview_button = _make_button(
                self.page.preview_label, "P", "试跑并查看预览，不写出文件"
            )
            self._preview_button.clicked.connect(self._start_preview)
            row.addWidget(self._preview_button)
            self._register_focus(self._preview_button)

        self._run_button = _make_button(
            self._current_submit_label(), "R", "开始处理并保存结果"
        )
        self._run_button.setObjectName("primary")
        self._run_button.clicked.connect(self._start_run)
        row.addWidget(self._run_button)
        self._register_focus(self._run_button)

        row.addStretch(1)
        layout.addLayout(row)

    def _build_result(self, layout: QVBoxLayout) -> None:
        section = self._section_title(self.page.result_title)
        group = QGroupBox(section)
        describe(group, section)
        box = QVBoxLayout(group)
        box.setSpacing(8)

        self._summary_edit = QLineEdit()
        self._summary_edit.setReadOnly(True)
        self._summary_edit.setPlaceholderText("处理完成后这里显示结果摘要")
        describe(
            self._summary_edit,
            "处理结果摘要",
            "处理完成后的文字说明，可被屏幕阅读器直接朗读",
        )
        box.addWidget(self._summary_edit)
        self._register_focus(self._summary_edit)

        self._detail_view = QTextBrowser()
        self._detail_view.setOpenExternalLinks(False)
        self._detail_view.setTabChangesFocus(True)
        self._detail_view.setMinimumHeight(220)
        self._detail_view.setVisible(False)
        describe(self._detail_view, "预览详情", "预览效果的文字与对照内容")
        box.addWidget(self._detail_view)
        self._register_focus(self._detail_view)

        if self.page.result_hint:
            result_hint = QLabel(self.page.result_hint)
            result_hint.setWordWrap(True)
            result_hint.setProperty("role", "hint")
            box.addWidget(result_hint)

        row = QHBoxLayout()
        self._save_button = _make_button("保存结果", "S", "把处理结果保存到本地")
        self._save_button.setEnabled(False)
        self._save_button.clicked.connect(self._save_results)
        row.addWidget(self._save_button)
        self._register_focus(self._save_button)

        self._status_label = QLabel("就绪")
        self._status_label.setObjectName("statusLabel")
        self._status_label.setWordWrap(True)
        self._status_label.setAccessibleName("就绪")
        self._status_label.setAccessibleDescription("当前操作状态：就绪")
        row.addWidget(self._status_label, 1)
        box.addLayout(row)

        layout.addWidget(group)

    @staticmethod
    def _hint_label(text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setProperty("role", "hint")
        return label

    def _register_focus(self, widget: QWidget) -> None:
        if widget is not None and widget.focusPolicy() != Qt.FocusPolicy.NoFocus:
            self._tab_widgets.append(widget)
            widget.installEventFilter(self)
            shortcut = getattr(widget, "_shortcut", None)
            if shortcut is not None:
                self._shortcuts.append(shortcut)

    def eventFilter(self, obj, event):  # noqa: N802 - Qt 命名
        # 键盘焦点落到折叠区域时，自动把该控件滚进可视范围。
        if (
            event.type() == QEvent.Type.FocusIn
            and self._scroll is not None
            and isinstance(obj, QWidget)
            and self._scroll.isAncestorOf(obj)
        ):
            self._scroll.ensureWidgetVisible(obj)
        return super().eventFilter(obj, event)

    def showEvent(self, event):  # noqa: N802 - Qt 命名
        super().showEvent(event)
        for shortcut in self._shortcuts:
            shortcut.setEnabled(True)

    def hideEvent(self, event):  # noqa: N802 - Qt 命名
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
        super().hideEvent(event)

    def _focus_candidates(self) -> list[QWidget]:
        return [
            widget
            for widget in self._tab_widgets
            if widget.isEnabled() and not widget.isHidden()
        ]

    def rebuild_tab_order(self) -> None:
        """按视觉顺序重建页面内部的 Tab 链。"""
        widgets = self._focus_candidates()
        for first, second in zip(widgets, widgets[1:]):
            QWidget.setTabOrder(first, second)

    def first_focus_widget(self) -> QWidget | None:
        widgets = self._focus_candidates()
        return widgets[0] if widgets else None

    def last_focus_widget(self) -> QWidget | None:
        widgets = self._focus_candidates()
        return widgets[-1] if widgets else None

    # ------------------------------------------------------------------ #
    # 模式相关
    # ------------------------------------------------------------------ #
    def _current_mode(self):
        if self.mode_combo is None:
            return None
        return self._mode_by_value.get(self.mode_combo.currentData())

    def _current_upload_label(self) -> str:
        mode = self._current_mode()
        if mode is not None and mode.upload_label:
            return mode.upload_label
        return self.page.upload_label

    def _current_upload_hint(self) -> str:
        mode = self._current_mode()
        if mode is not None and mode.hint:
            return mode.hint
        return self.page.upload_hint

    def _current_accept(self) -> str:
        mode = self._current_mode()
        if mode is not None and mode.accept:
            return mode.accept
        return self.page.upload_accept

    def _current_submit_label(self) -> str:
        mode = self._current_mode()
        if mode is not None and mode.submit_label:
            return mode.submit_label
        return self.page.submit_label

    def _allows_multiple(self) -> bool:
        mode = self._current_mode()
        if mode is not None and mode.multiple is not None:
            return bool(mode.multiple)
        return bool(self.page.upload_multiple)

    def _on_mode_changed(self, *_args) -> None:
        self._upload_label.setText(self._current_upload_label())
        hint = self._current_upload_hint()
        self._upload_hint.setText(hint)
        self._upload_hint.setAccessibleDescription(hint)
        submit_label = self._current_submit_label()
        self._run_button.setText(f"{submit_label} (Alt+R)")
        self._run_button.setToolTip(f"{submit_label}（快捷键 Alt+R）")
        self._run_button.setAccessibleName(submit_label)
        self._run_button.setAccessibleDescription("开始处理并保存结果 快捷键 Alt+R")

        if hasattr(self, "_order_hint_label"):
            self._order_hint_label.setVisible(
                bool(self.page.upload_order_hint) and self._allows_multiple()
            )

        if not self._allows_multiple() and len(self._files) > 1:
            self._files = self._files[:1]
        for widget in self._filelist_widgets.values():
            _populate_checklist(widget, [])
        self._refresh_file_list()
        self._refresh_order_buttons()

    # ------------------------------------------------------------------ #
    # 文件选择
    # ------------------------------------------------------------------ #
    def _choose_files(self) -> None:
        filter_text = _qt_filter(self._current_accept())
        if self._allows_multiple():
            paths, _ = QFileDialog.getOpenFileNames(self, "选择文件", "", filter_text)
        else:
            path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", filter_text)
            paths = [path] if path else []
        if not paths:
            return
        self._files = [Path(item) for item in paths]
        self._refresh_file_list()
        self._refresh_order_buttons()
        if not self._file_list.isHidden():
            self._file_list.setCurrentRow(0)
            self._file_list.setFocus(Qt.FocusReason.OtherFocusReason)
        self._autoload_if_needed()

    def _choose_single_file(self, target: QLineEdit) -> None:
        filter_text = _qt_filter(self.page.upload_accept or ".epub")
        path, _ = QFileDialog.getOpenFileName(self, "选择文件", "", filter_text)
        if path:
            target.setText(path)

    def _refresh_file_list(self) -> None:
        self._file_list.clear()
        multiple = self._allows_multiple()
        for index, path in enumerate(self._files, start=1):
            text = f"{index}. {path.name}" if multiple else path.name
            item = QListWidgetItem(text)
            item.setToolTip(str(path))
            item.setData(Qt.ItemDataRole.UserRole, str(path))
            accessible = (
                f"第 {index} 个文件：{path.name}" if multiple else f"文件：{path.name}"
            )
            item.setData(Qt.ItemDataRole.AccessibleTextRole, accessible)
            self._file_list.addItem(item)

        if not self._files:
            self._file_count_label.setText("尚未选择文件")
        else:
            self._file_count_label.setText(f"已选 {len(self._files)} 个文件")
        self._file_list.setVisible(bool(self._files))
        self.rebuild_tab_order()
        self.chain_dirty.emit()

    def _refresh_order_buttons(self) -> None:
        multiple = self._allows_multiple()
        for button in (self._up_button, self._down_button, self._remove_button):
            button.setEnabled(bool(self._files) and multiple)
        self.rebuild_tab_order()
        self.chain_dirty.emit()

    def _move_file(self, delta: int) -> None:
        row = self._file_list.currentRow()
        if row < 0:
            self._notify("请先在文件列表中选择一个文件。")
            return
        target = row + delta
        if target < 0:
            self._set_status("已经在列表顶部。")
            return
        if target >= len(self._files):
            self._set_status("已经在列表底部。")
            return
        self._files[row], self._files[target] = self._files[target], self._files[row]
        self._refresh_file_list()
        self._file_list.setCurrentRow(target)
        self._set_status(f"已移动到第 {target + 1} 位。")

    def _remove_selected_file(self) -> None:
        row = self._file_list.currentRow()
        if row < 0 or row >= len(self._files):
            self._notify("请先在文件列表中选择一个文件。")
            return
        del self._files[row]
        self._refresh_file_list()
        self._refresh_order_buttons()
        self._set_status("已移除选中文件。")

    def _autoload_if_needed(self) -> None:
        if self.page.load_endpoint and self.page.autoload and self._files:
            self._start_load()

    # ------------------------------------------------------------------ #
    # 取值 / 回填
    # ------------------------------------------------------------------ #
    def _collect_values(self) -> dict:
        values = {}
        if self.mode_combo is not None:
            values["mode"] = self.mode_combo.currentData()
        for name, getter in self._field_getters.items():
            try:
                values[name] = getter()
            except Exception:  # noqa: BLE001 - 单个控件取值失败不应中断
                values[name] = None
        return values

    def _missing_required(self) -> list:
        missing = []
        for field in self.page.fields:
            if not field.required:
                continue
            getter = self._field_getters.get(field.name)
            if getter is None:
                continue
            value = getter()
            if isinstance(value, bool):
                empty = not value
            elif value is None:
                empty = True
            else:
                empty = not str(value).strip()
            if empty:
                missing.append(field)
        return missing

    def _focus_first_missing(self, missing) -> None:
        """把焦点移到第一个缺失的必填字段，并临时改写其可访问描述。"""
        for field in missing:
            widget = self._field_widgets.get(field.name)
            if widget is None:
                continue
            base = self._field_base_desc.get(field.name, "")
            widget.setAccessibleDescription((base + " 必填项，当前为空").strip())
            widget.setFocus(Qt.FocusReason.OtherFocusReason)
            if hasattr(widget, "selectAll"):
                widget.selectAll()
            break

    def _apply_values(self, values: dict) -> None:
        for name, value in (values or {}).items():
            setter = self._field_setters.get(name)
            if setter is None:
                continue
            try:
                setter(value)
            except Exception:  # noqa: BLE001 - 回填失败忽略该字段
                continue

    # ------------------------------------------------------------------ #
    # 后台任务
    # ------------------------------------------------------------------ #
    def _run_worker(self, func, on_done) -> None:
        worker = _Worker(func, self)
        worker.succeeded.connect(lambda result: self._finish(on_done, result))
        worker.failed.connect(self._fail)
        worker.finished.connect(worker.deleteLater)
        self._worker = worker
        worker.start()

    def _finish(self, on_done, result) -> None:
        self._set_busy(False)
        on_done(result)

    def _fail(self, message: str) -> None:
        self._set_busy(False)
        self._show_error(message)

    # ------------------------------------------------------------------ #
    # 动作
    # ------------------------------------------------------------------ #
    def _start_load(self) -> None:
        if not self._files:
            self._notify("请先选择文件。")
            return
        self._set_busy(True, "正在读取文件信息…")
        # Qt 控件只能在主线程访问：先快照文件与取值，再交给工作线程。
        files = list(self._files)
        values = self._collect_values()
        self._run_worker(lambda: self.adapter.load(files, values), self._on_load_done)

    def _on_load_done(self, result) -> None:
        result = result or {}
        self._apply_values(result.get("values") or {})
        self._set_summary(result.get("summary") or "读取完成。")
        self._set_status("读取完成，可继续调整设置。")

    def _start_preview(self) -> None:
        if not self._files:
            self._notify("请先选择文件。")
            return
        self._set_busy(True, "正在生成预览…")
        files = list(self._files)
        values = self._collect_values()
        self._run_worker(
            lambda: self.adapter.preview(files, values), self._on_preview_done
        )

    def _on_preview_done(self, result) -> None:
        result = result or {}
        self._set_summary(result.get("summary") or "预览完成。")
        detail = result.get("detail") or ""
        self._detail_view.setHtml(detail)
        self._detail_view.setVisible(True)
        self.rebuild_tab_order()
        self.chain_dirty.emit()
        self._set_status("预览完成；查看详情后可直接点「开始处理」导出。")

    def _start_run(self) -> None:
        if not self._files:
            self._notify("请先选择文件。")
            return
        missing = self._missing_required()
        if missing:
            self._show_error(
                "请先填写必填项：" + "、".join(field.label for field in missing)
            )
            self._focus_first_missing(missing)
            return
        problems = []
        for path in self._files:
            reason = self.adapter.validate_file(path)
            if reason:
                problems.append(reason)
        if problems:
            self._show_error("；".join(problems[:5]))
            return

        self._set_busy(True, "正在处理…")
        files = list(self._files)
        values = self._collect_values()
        self._run_worker(lambda: self.adapter.run(files, values), self._on_run_done)

    def _on_run_done(self, results) -> None:
        self._results = list(results or [])
        if not self._results:
            self._show_error("处理没有产出结果。")
            return

        summaries = [result.summary for result in self._results if result.summary]
        summary = "；".join(summaries) if summaries else "处理完成。"
        if len(self._results) > 1:
            summary = f"共处理 {len(self._results)} 本。{summary}"
        self._set_summary(summary)
        self._save_button.setEnabled(True)
        self.rebuild_tab_order()
        self.chain_dirty.emit()
        self._set_status("处理完成，请点「保存结果」选择保存位置。")

    def _save_results(self) -> None:
        if not self._results:
            self._notify("还没有可保存的结果。")
            return

        if len(self._results) == 1:
            result = self._results[0]
            path, _ = QFileDialog.getSaveFileName(
                self, "保存结果", result.filename, "所有文件 (*)"
            )
            if not path:
                return
            Path(path).write_bytes(result.data)
        else:
            default = f"{self.adapter.key}_结果_{len(self._results)}本.zip"
            path, _ = QFileDialog.getSaveFileName(
                self, "保存结果", default, "ZIP 压缩包 (*.zip)"
            )
            if not path:
                return
            Path(path).write_bytes(_zip_results(self._results))

        self._set_status(f"已保存到：{path}")
        self._set_summary("已保存到：" + path)

    # ------------------------------------------------------------------ #
    # 状态
    # ------------------------------------------------------------------ #
    def _set_busy(self, busy: bool, message: str = "") -> None:
        self._busy = busy
        self.busy_changed.emit(busy)
        buttons = [self._run_button, self._choose_button]
        if self._load_button is not None:
            buttons.append(self._load_button)
        if self._preview_button is not None:
            buttons.append(self._preview_button)
        for button in buttons:
            button.setEnabled(not busy)
        if busy:
            for button in (self._up_button, self._down_button, self._remove_button):
                button.setEnabled(False)
            self._set_status(message)
        else:
            self._refresh_order_buttons()
        self.rebuild_tab_order()
        self.chain_dirty.emit()

    def _set_summary(self, text: str) -> None:
        self._summary_edit.setText(text)
        self._summary_edit.setAccessibleDescription(text)
        # 焦点移到摘要，屏幕阅读器会立即朗读结果。
        self._summary_edit.setFocus()
        self._summary_edit.selectAll()

    def _set_status(self, text: str) -> None:
        self._status_label.setText(text)
        # 状态文字本身就是可访问名称，避免读屏只读到泛化的「状态」。
        self._status_label.setAccessibleName(text)
        self._status_label.setAccessibleDescription(text)

    def _notify(self, message: str) -> None:
        """没有可承载焦点的控件时，用模态对话框确保读屏能听到。"""
        self._set_status(message)
        QMessageBox.information(self, "提示", message)

    def _show_error(self, message: str) -> None:
        self._set_status("处理失败：" + message)
        QMessageBox.warning(self, "处理失败", message)
