# -*- coding: utf-8 -*-
"""文本清洗的桌面适配器。"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from modules.epub_io import extract_epub, write_epub
from modules.epub_text import (
    CleanOptions,
    clean_book,
    parse_replacements,
    render_preview,
    stats_text,
)
from modules.textclean import PAGE, TOOL
from modules.toolkit import safe_filename

from desktop.adapters.base import BaseAdapter, RunResult


class TextCleanAdapter(BaseAdapter):
    key = TOOL["key"]
    title = TOOL["title"]
    order = TOOL["order"]
    description = TOOL["description"]
    page = PAGE
    supports_multiple = True

    def validate_file(self, path: Path) -> str | None:
        return self._validate_epub(path)

    # ------------------------------------------------------------------ #
    def _options(self, values: dict) -> CleanOptions:
        # 与原 _options_from_form 一致：规范空白与删空段共用同一个开关。
        normalize = bool(values.get("normalize_whitespace"))
        return CleanOptions(
            normalize_whitespace=normalize,
            remove_empty_paragraphs=normalize,
            merge_broken_paragraphs=bool(values.get("merge_broken_paragraphs")),
            strip_inline=bool(values.get("strip_inline")),
            replacements=parse_replacements(values.get("replace_text") or ""),
            regex_replacements=bool(values.get("regex_replacements")),
        )

    def preview(self, files: list[Path], values: dict) -> dict:
        options = self._options(values)
        with TemporaryDirectory() as temp_dir:
            book = extract_epub(files[0], Path(temp_dir) / "book")
            stats, samples = clean_book(book, options, write=False)
        return {
            "summary": stats_text(stats),
            "detail": render_preview(samples, stats),
        }

    def run(self, files: list[Path], values: dict) -> list[RunResult]:
        options = self._options(values)
        results: list[RunResult] = []
        for path in files:
            with TemporaryDirectory() as temp_dir:
                book = extract_epub(path, Path(temp_dir) / "book")
                stats, _samples = clean_book(book, options, write=True)
                payload = write_epub(book)
            name = safe_filename(f"{path.stem}_cleaned.epub", f"{path.stem}.epub")
            results.append(
                RunResult(name, payload.getvalue(), f"处理完成，{stats_text(stats)}。")
            )
        return results
