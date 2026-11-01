# -*- coding: utf-8 -*-
"""解包 / 重打包的桌面适配器。

复用 ``modules/repack.py`` 里的归档结构校验与扁平化辅助函数，
只把上传文件换成 Qt 选中的本地路径。
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from modules.epub_io import extract_epub, write_epub
from modules.repack import (
    MODE_UNPACK,
    PAGE,
    TOOL,
    _flatten_single_root,
    _normalize_mimetype,
    _require_epub_structure,
)
from modules.toolkit import safe_filename, zip_directory

from desktop.adapters.base import BaseAdapter, RunResult


class RepackAdapter(BaseAdapter):
    key = TOOL["key"]
    title = TOOL["title"]
    order = TOOL["order"]
    description = TOOL["description"]
    page = PAGE

    def validate_file(self, path: Path) -> str | None:
        return self._validate_epub(path)

    def run(self, files: list[Path], values: dict) -> list[RunResult]:
        # 解包 / 重打包都只处理第一个文件
        path = files[0]
        reason = self._validate_epub(path)
        if reason:
            from modules.toolkit import ToolkitError

            raise ToolkitError(reason)

        mode = values.get("mode") or MODE_UNPACK
        if mode == MODE_UNPACK:
            with TemporaryDirectory() as temp_dir:
                book = extract_epub(path, Path(temp_dir) / "book")
                payload = zip_directory(book)
            name = safe_filename(f"{path.stem}.zip", "book.zip")
            return [RunResult(name, payload.getvalue(), "解包完成，已生成 ZIP 压缩包。")]

        with TemporaryDirectory() as temp_dir:
            root = extract_epub(path, Path(temp_dir) / "book")
            book = _flatten_single_root(root)
            _require_epub_structure(book)
            _normalize_mimetype(book)
            payload = write_epub(book)
        name = safe_filename(f"{path.stem}.epub", "book.epub")
        return [RunResult(name, payload.getvalue(), "重打包完成，已生成规范 EPUB。")]
