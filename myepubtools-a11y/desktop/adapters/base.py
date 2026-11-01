# -*- coding: utf-8 -*-
"""桌面适配器基类。

适配器只承担「原来 Flask 路由里读 ``request.form`` / ``request.files`` /
``send_file`` 的那层胶水」：把 Qt 控件值（``values``）与本地文件路径
（``files``）翻译成对业务模块纯函数的调用，再把产物交回界面保存。

业务模块（``modules/`` 与 ``build_epub.py``）逐字节不变。
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class RunResult:
    """单本处理的产物：文件名、字节内容与一句可朗读摘要。"""

    filename: str
    data: bytes
    summary: str = ""


class BaseAdapter:
    """一个工具的桌面适配器。"""

    key: str = ""
    title: str = ""
    order: int = 100
    description: str = ""
    #: 对应工具模块里的 :class:`modules.toolkit.PageConfig`
    page = None

    #: 页面是否允许一次选择多个文件（批量工具为 True）
    supports_multiple = False

    # ------------------------------------------------------------------ #
    # 供界面调用
    # ------------------------------------------------------------------ #
    def validate_file(self, path: Path) -> str | None:
        """单个文件的预校验；返回原因文本即视为不通过。"""
        return None

    def load(self, files: list[Path], values: dict) -> dict:
        """读取现有值（对应原 ``load_endpoint``）。

        返回 ``{"values": {...}, "summary": "..."}``；``values`` 里的键
        与页面字段同名，界面会据此回填控件。
        """
        return {"values": {}, "summary": ""}

    def preview(self, files: list[Path], values: dict) -> dict | None:
        """试跑预览（对应原 ``preview_endpoint``）。

        返回 ``{"summary": "...", "detail": "HTML 文本"}``；``detail``
        会显示在只读的预览区里，屏幕阅读器可直接朗读。
        """
        return None

    def run(self, files: list[Path], values: dict) -> list[RunResult]:
        """真正处理并返回产物列表；失败时抛业务层异常（中文消息）。"""
        raise NotImplementedError

    # ------------------------------------------------------------------ #
    # 公共校验
    # ------------------------------------------------------------------ #
    def _validate_epub(self, path: Path) -> str | None:
        """校验本地文件是有效 ZIP/EPUB 且未超出解压体积上限。"""
        from modules.epub_io import INVALID_EPUB_MESSAGE, is_epub_bytes
        from modules.toolkit import ToolkitError, check_zip_bomb

        try:
            data = path.read_bytes()
        except OSError as exc:
            return f"无法读取文件：{path.name}（{exc}）"

        if not data:
            return f"文件为空：{path.name}"
        if not is_epub_bytes(data):
            return f"{INVALID_EPUB_MESSAGE}：{path.name}"
        try:
            check_zip_bomb(data)
        except ToolkitError as exc:
            return str(exc)
        return None
