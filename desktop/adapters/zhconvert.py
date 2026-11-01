# -*- coding: utf-8 -*-
"""简繁转换的桌面适配器。

对应原 ``modules/zhconvert.py`` 的 ``api_run`` / ``api_load`` / ``api_preview``：
把 Flask 的 ``read_uploads`` / ``_options_from_form`` / ``send_file`` 换成
Qt 文件路径与本地字节，业务函数 ``convert_book`` 等原样调用。
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from modules.epub_convert import (
    DEFAULT_DIRECTION,
    ConvertOptions,
    convert_book,
    detect_direction,
    render_preview,
    stats_text,
)
from modules.epub_io import extract_epub, write_epub
from modules.toolkit import safe_filename
from modules.zhconvert import PAGE, TOOL

from desktop.adapters.base import BaseAdapter, RunResult

_SCRIPT_SUFFIX = {"traditional": "繁体", "simplified": "简体"}


class ZhConvertAdapter(BaseAdapter):
    key = TOOL["key"]
    title = TOOL["title"]
    order = TOOL["order"]
    description = TOOL["description"]
    page = PAGE
    supports_multiple = True

    def validate_file(self, path: Path) -> str | None:
        return self._validate_epub(path)

    # ------------------------------------------------------------------ #
    def _options(self, values: dict) -> ConvertOptions:
        return ConvertOptions(
            direction=values.get("direction") or DEFAULT_DIRECTION,
            scope_body=bool(values.get("scope_body")),
            scope_navigation=bool(values.get("scope_navigation")),
            scope_metadata=bool(values.get("scope_metadata")),
            update_language=bool(values.get("update_language")),
        )

    def load(self, files: list[Path], values: dict) -> dict:
        with TemporaryDirectory() as temp_dir:
            book = extract_epub(files[0], Path(temp_dir) / "book")
            direction, summary = detect_direction(book)
        # 判断不出来时不返回 direction，避免把用户的选项覆盖成空值。
        return {
            "values": {"direction": direction} if direction else {},
            "summary": summary,
        }

    def preview(self, files: list[Path], values: dict) -> dict:
        options = self._options(values)
        with TemporaryDirectory() as temp_dir:
            book = extract_epub(files[0], Path(temp_dir) / "book")
            stats, preview = convert_book(book, options, write=False)
        return {
            "summary": stats_text(stats),
            "detail": render_preview(stats, preview),
        }

    def run(self, files: list[Path], values: dict) -> list[RunResult]:
        options = self._options(values)
        results: list[RunResult] = []
        for path in files:
            with TemporaryDirectory() as temp_dir:
                book = extract_epub(path, Path(temp_dir) / "book")
                stats, preview = convert_book(book, options, write=True)
                payload = write_epub(book)
            suffix = _SCRIPT_SUFFIX.get(preview.direction.target, "转换")
            name = safe_filename(f"{path.stem}_{suffix}.epub", f"{path.stem}.epub")
            results.append(RunResult(name, payload.getvalue(), stats_text(stats)))
        return results
