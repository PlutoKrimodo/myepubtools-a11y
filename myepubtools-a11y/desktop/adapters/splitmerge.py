# -*- coding: utf-8 -*-
"""EPUB 拆分 / 合并的桌面适配器。"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from modules.epub_assemble import (
    Selection,
    SourceBook,
    assemble,
    book_metadata,
    document_titles,
    new_identifier,
)
from modules.epub_io import extract_epub, write_epub
from modules.epub_package import spine_documents
from modules.splitmerge import (
    EMPTY_TITLE_MESSAGE,
    MERGE_NEED_TWO_MESSAGE,
    MODE_MERGE,
    MODE_SPLIT,
    NO_SELECTION_MESSAGE,
    PAGE,
    SPLIT_ONE_FILE_MESSAGE,
    TOOL,
)
from modules.toolkit import ToolkitError, safe_filename

from desktop.adapters.base import BaseAdapter, RunResult


class SplitMergeAdapter(BaseAdapter):
    key = TOOL["key"]
    title = TOOL["title"]
    order = TOOL["order"]
    description = TOOL["description"]
    page = PAGE
    supports_multiple = True

    def validate_file(self, path: Path) -> str | None:
        return self._validate_epub(path)

    # ------------------------------------------------------------------ #
    def _mode(self, values: dict) -> str:
        return values.get("mode") or MODE_SPLIT

    def load(self, files: list[Path], values: dict) -> dict:
        mode = self._mode(values)
        with TemporaryDirectory() as temp_dir:
            source = extract_epub(files[0], Path(temp_dir) / "src")
            metadata = book_metadata(source)
            filled = {
                "title": (metadata.get("title") or "").strip() or files[0].stem,
                "author": (metadata.get("creator") or "").strip() or "未知",
                "language": (metadata.get("language") or "").strip() or "zh-Hans",
                "documents": [],
            }

            summary = f"已读取《{filled['title']}》。"
            if mode == MODE_SPLIT:
                if len(files) != 1:
                    raise ToolkitError(SPLIT_ONE_FILE_MESSAGE)
                titles = document_titles(source)
                documents = [
                    {
                        "value": relative,
                        "label": titles.get(relative) or Path(relative).stem,
                    }
                    for relative in spine_documents(source)
                ]
                filled["documents"] = documents
                summary += f"共 {len(documents)} 个正文章节，勾选后导出。"
            else:
                names = "、".join(path.stem for path in files[:3])
                summary = (
                    f"已选中 {len(files)} 本（{names}{'…' if len(files) > 3 else ''}），"
                    "第一本的书名与作者已填入下方。"
                )
        return {"values": filled, "summary": summary}

    # ------------------------------------------------------------------ #
    def _meta_from_values(self, values: dict, fallback_author: str = "") -> dict:
        # 书名不回落：它已被预填成原书名，被清空就是用户想清空。
        title = (values.get("title") or "").strip()
        if not title:
            raise ToolkitError(EMPTY_TITLE_MESSAGE)
        author = (values.get("author") or "").strip() or fallback_author.strip() or "未知"
        language = (values.get("language") or "").strip() or "zh-Hans"
        return {
            "title": title,
            "author": author,
            "language": language,
            "identifier": new_identifier(),
        }

    def run(self, files: list[Path], values: dict) -> list[RunResult]:
        mode = self._mode(values)

        if mode == MODE_SPLIT:
            if len(files) != 1:
                raise ToolkitError(SPLIT_ONE_FILE_MESSAGE)
        elif len(files) < 2:
            raise ToolkitError(MERGE_NEED_TWO_MESSAGE)

        with TemporaryDirectory() as temp_dir:
            roots = [
                SourceBook(extract_epub(path, Path(temp_dir) / f"src{index}"), path.name)
                for index, path in enumerate(files)
            ]

            if mode == MODE_SPLIT:
                selected = [
                    str(item).strip()
                    for item in (values.get("documents") or [])
                    if str(item).strip()
                ]
                if not selected:
                    raise ToolkitError(NO_SELECTION_MESSAGE)
                available = set(spine_documents(roots[0].root))
                unknown = [item for item in selected if item not in available]
                if unknown:
                    raise ToolkitError(f"勾选的章节不在该书正文里：{unknown[0]}")
                titles = document_titles(roots[0].root)
                selections = [
                    Selection(0, item, titles.get(item, "")) for item in selected
                ]
            else:
                selections = []
                for index, source in enumerate(roots):
                    for relative in spine_documents(source.root):
                        selections.append(Selection(index, relative, ""))

            first_metadata = book_metadata(roots[0].root)
            meta = self._meta_from_values(
                values, first_metadata.get("creator") or "未知"
            )

            destination = Path(temp_dir) / "out"
            report = assemble(roots, selections, destination, meta)
            payload = write_epub(destination)

        name = safe_filename(f"{meta['title']}.epub", f"{files[0].stem}.epub")
        action = "拆分" if mode == MODE_SPLIT else "合并"
        message = (
            f"{action}完成，收录 {report.documents} 章、{report.resources} 个资源"
            f"（来自 {report.sources} 本）"
        )
        if report.broken_links_removed:
            message += f"，摘除指向未收录内容的链接 {report.broken_links_removed} 处"
        return [RunResult(name, payload.getvalue(), message + "。")]
