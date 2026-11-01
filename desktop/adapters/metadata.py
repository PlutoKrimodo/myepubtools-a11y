# -*- coding: utf-8 -*-
"""元数据编辑的桌面适配器。"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from modules.epub_io import extract_epub, write_epub
from modules.epub_package import (
    FIELD_KEYS,
    read_metadata,
    sync_titles,
    write_metadata,
)
from modules.metadata import EMPTY_TITLE_MESSAGE, PAGE, TOOL, _status_text
from modules.toolkit import ToolkitError, safe_filename

from desktop.adapters.base import BaseAdapter, RunResult


class MetadataAdapter(BaseAdapter):
    key = TOOL["key"]
    title = TOOL["title"]
    order = TOOL["order"]
    description = TOOL["description"]
    page = PAGE

    def validate_file(self, path: Path) -> str | None:
        return self._validate_epub(path)

    def load(self, files: list[Path], values: dict) -> dict:
        with TemporaryDirectory() as temp_dir:
            book = extract_epub(files[0], Path(temp_dir) / "book")
            snapshot = read_metadata(book)

        filled = {key: snapshot.values.get(key, "") for key in FIELD_KEYS}
        if snapshot.filled:
            summary = (
                f"已从 {snapshot.opf_path} 读取 {snapshot.filled} 项元数据，"
                "可修改后提交。"
            )
        else:
            summary = f"{snapshot.opf_path} 中没有可识别的元数据，可直接填写后提交。"
        return {"values": filled, "summary": summary}

    def run(self, files: list[Path], values: dict) -> list[RunResult]:
        path = files[0]
        data = {key: (values.get(key) or "").strip() for key in FIELD_KEYS}
        if not data["title"]:
            raise ToolkitError(EMPTY_TITLE_MESSAGE)

        with TemporaryDirectory() as temp_dir:
            book = extract_epub(path, Path(temp_dir) / "book")

            before = read_metadata(book)
            opf_path, opf_changed = write_metadata(book, data)

            # 只在书名真的发生变化时才动目录。
            synced = []
            if data["title"] != (before.values.get("title") or "").strip():
                synced = sync_titles(book, data["title"])

            payload = write_epub(book)

        name = safe_filename(f"{data['title']}.epub", f"{path.stem}.epub")
        summary = _status_text(opf_path, opf_changed, synced)
        return [RunResult(name, payload.getvalue(), summary)]
