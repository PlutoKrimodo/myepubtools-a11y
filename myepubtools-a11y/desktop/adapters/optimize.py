# -*- coding: utf-8 -*-
"""EPUB 格式优化的桌面适配器。"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from modules.epub_io import extract_epub, is_epub_bytes, write_epub
from modules.epub_optimize import (
    MODE_APPEND,
    MODE_REPLACE,
    extract_template_css_map,
    optimize_book,
    render_preview_html,
)
from modules.optimize import DEFAULT_TEMPLATE, PAGE, TOOL
from modules.toolkit import ToolkitError, check_zip_bomb, safe_filename

from desktop.adapters.base import BaseAdapter, RunResult


class OptimizeAdapter(BaseAdapter):
    key = TOOL["key"]
    title = TOOL["title"]
    order = TOOL["order"]
    description = TOOL["description"]
    page = PAGE
    supports_multiple = True

    def validate_file(self, path: Path) -> str | None:
        return self._validate_epub(path)

    # ------------------------------------------------------------------ #
    def _options(self, values: dict) -> dict:
        return {
            "remove_empty_paragraphs": bool(values.get("remove_empty_paragraphs")),
            "remove_br": bool(values.get("remove_br")),
            "same_filename": bool(values.get("same_filename")),
            "remove_paragraph_spacing": bool(values.get("remove_paragraph_spacing")),
            "paragraph_indent": (values.get("paragraph_indent") or "").strip(),
            "paragraph_include_div": bool(values.get("paragraph_include_div")),
        }

    def _template_css_map(self, mode: str, values: dict):
        """替换模式：读取上传模板或回落到默认 sample_test.epub。"""
        if mode != MODE_REPLACE:
            return None

        template = (values.get("template_file") or "").strip()
        if template:
            try:
                data = Path(template).read_bytes()
            except OSError as exc:
                raise ToolkitError(f"无法读取模板文件：{template}（{exc}）") from exc
            if not data:
                raise ToolkitError("上传的模板文件为空")
            if not is_epub_bytes(data):
                raise ToolkitError("模板不是有效的 EPUB/ZIP")
            check_zip_bomb(data)
            return extract_template_css_map(data)

        if not DEFAULT_TEMPLATE.exists():
            raise ToolkitError(
                f"替换模式需要模板 EPUB：请上传模板，或确认默认模板存在"
                f"（{DEFAULT_TEMPLATE.name}）"
            )
        return extract_template_css_map(DEFAULT_TEMPLATE.read_bytes())

    @staticmethod
    def _run_kwargs(options: dict) -> dict:
        return {
            "remove_empty_paragraphs": options["remove_empty_paragraphs"],
            "remove_br": options["remove_br"],
            "remove_paragraph_spacing": options["remove_paragraph_spacing"],
            "paragraph_indent": options["paragraph_indent"],
            "paragraph_include_div": options["paragraph_include_div"],
        }

    def preview(self, files: list[Path], values: dict) -> dict:
        mode = values.get("mode") or MODE_APPEND
        options = self._options(values)
        template_css_map = self._template_css_map(mode, values)

        with TemporaryDirectory() as temp_dir:
            book = extract_epub(files[0], Path(temp_dir) / "book")
            stats = optimize_book(
                book,
                mode=mode,
                template_css_map=template_css_map,
                write=False,
                **self._run_kwargs(options),
            )
        return {
            "summary": stats.summary(),
            "detail": render_preview_html(stats),
        }

    def run(self, files: list[Path], values: dict) -> list[RunResult]:
        mode = values.get("mode") or MODE_APPEND
        options = self._options(values)
        template_css_map = self._template_css_map(mode, values)

        results: list[RunResult] = []
        for path in files:
            with TemporaryDirectory() as temp_dir:
                book = extract_epub(path, Path(temp_dir) / "book")
                stats = optimize_book(
                    book,
                    mode=mode,
                    template_css_map=template_css_map,
                    write=True,
                    **self._run_kwargs(options),
                )
                payload = write_epub(book)

            stem = path.stem if options["same_filename"] else f"{path.stem}_optimized"
            name = safe_filename(f"{stem}.epub", "book.epub")
            results.append(RunResult(name, payload.getvalue(), stats.summary()))
        return results
