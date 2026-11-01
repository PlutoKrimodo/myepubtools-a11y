# -*- coding: utf-8 -*-
"""样式排版的桌面适配器。"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from modules.epub_css import (
    TypographyOptions,
    check_options,
    process_book,
    render_preview,
)
from modules.epub_io import extract_epub, write_epub
from modules.layout import (
    PAGE,
    TOOL,
    _clear_empty_paragraphs,
    _combined_summary,
)
from modules.toolkit import safe_filename

from desktop.adapters.base import BaseAdapter, RunResult


class LayoutAdapter(BaseAdapter):
    key = TOOL["key"]
    title = TOOL["title"]
    order = TOOL["order"]
    description = TOOL["description"]
    page = PAGE

    def validate_file(self, path: Path) -> str | None:
        return self._validate_epub(path)

    # ------------------------------------------------------------------ #
    def _options(self, values: dict) -> tuple[bool, TypographyOptions, bool]:
        typography = TypographyOptions(
            font_size=(values.get("font_size") or "").strip(),
            line_height=(values.get("line_height") or "").strip(),
            text_indent=(values.get("text_indent") or "").strip(),
            paragraph_spacing=(values.get("paragraph_spacing") or "").strip(),
        )
        if not values.get("apply_typography"):
            typography = TypographyOptions()
        # 先校验取值，避免处理到一半才因为非法字符失败。
        return (
            bool(values.get("clean_css")),
            check_options(typography),
            bool(values.get("clear_empty_paragraphs")),
        )

    def preview(self, files: list[Path], values: dict) -> dict:
        clean, typography, clear_empty = self._options(values)
        with TemporaryDirectory() as temp_dir:
            book = extract_epub(files[0], Path(temp_dir) / "book")
            stats, samples, override_block = process_book(
                book, clean=clean, typography=typography, write=False
            )
            text_stats = _clear_empty_paragraphs(book, clear_empty, write=False)
        return {
            "summary": _combined_summary(stats, text_stats),
            "detail": render_preview(
                stats, samples, override_block, typography, text_stats
            ),
        }

    def run(self, files: list[Path], values: dict) -> list[RunResult]:
        clean, typography, clear_empty = self._options(values)
        path = files[0]
        with TemporaryDirectory() as temp_dir:
            book = extract_epub(path, Path(temp_dir) / "book")
            stats, _samples, _block = process_book(
                book, clean=clean, typography=typography, write=True
            )
            text_stats = _clear_empty_paragraphs(book, clear_empty, write=True)
            payload = write_epub(book)

        name = safe_filename(f"{path.stem}_styled.epub", f"{path.stem}.epub")
        summary = f"处理完成，{_combined_summary(stats, text_stats)}"
        return [RunResult(name, payload.getvalue(), summary)]
