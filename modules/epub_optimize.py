# -*- coding: utf-8 -*-
"""EPUB 格式优化公共层。

移植自 ``txt_to_epub/build_epub.py`` 的 ``optimize_epub``，改为对**已解包目录**
操作；归档读写交给 :mod:`modules.epub_io`，正文空段清理复用
:func:`modules.epub_text.clean_book`，段落间距的改写复用
:func:`modules.epub_css.flatten_paragraph_spacing`，不另写一套判定。

两类模式
--------
* **append**——保留原书样式，只压平段落间距：就地改写样式表里作用于 ``<p>``
  的规则，并在层叠最末的样式表追加一段 ``p`` 兜底规则。整本没有任何样式表时，
  新建 ``epub-compact.css`` 并补 ``<link>`` 与 OPF manifest，保证规则一定生效。
* **replace**——用模板样式表覆盖 / 写入，并给无 class 的 ``<p>`` / ``<h1>``
  注入 ``bodyContent-1`` / ``chaptertitle-c``，确保 ``<head>`` 链到模板 CSS，
  必要时把新 CSS 补进 ``content.opf`` manifest。适合无样式或样式混乱的书。
  两种模式**都**会执行段落间距压平——只在末尾追加覆盖规则是压不住段间空行的：
  类选择器（``.block``）优先级高于 ``body p`` 这类标签选择器，追加的规则抢不过
  原书已有声明。

两模式均可选：
    * 压平段落间距（上下 margin / padding 清零 + 段首缩进，语义对齐 Calibre 的
      「删除段间空行」）；
    * 删除 spine 正文里的空 ``<p>``（判定与「文本清洗」「样式排版」一致）；
    * 删除 HTML 中的 ``<br>`` 标签（独立于空段开关，统计数量）。

本模块**没有** ``TOOL`` 字典，属于公共模块，``registry.discover()`` 会自动跳过。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from modules.epub_css import (
    ParagraphSpacingOptions,
    build_paragraph_block,
    flatten_paragraph_spacing,
    validate_value,
)
from modules.epub_io import HTML_EXTS, EpubIOError, decode_text, write_text
from modules.epub_text import CleanOptions, clean_book

MODE_APPEND = "append"
MODE_REPLACE = "replace"
MODE_VALUES = (MODE_APPEND, MODE_REPLACE)

#: 「删除段间空行」的默认段首缩进；留空表示不改缩进。
DEFAULT_PARAGRAPH_INDENT = "1.5em"

#: 「删除空段」的选项：只打开删空段这一项，其余清洗开关全部关闭。
#: 判定标准与「文本清洗」「样式排版」逐字一致，避免两套实现各自演化。
EMPTY_PARAGRAPH_OPTIONS = CleanOptions(
    normalize_whitespace=False,
    remove_empty_paragraphs=True,
    merge_broken_paragraphs=False,
    strip_inline=False,
)

def build_spacing_options(indent=DEFAULT_PARAGRAPH_INDENT, include_div=False):
    """把页面选项换算成 :class:`~modules.epub_css.ParagraphSpacingOptions`。

    ``indent`` 留空表示**不改缩进**，只压间距；取值先过一遍样式表字符校验，
    避免把能破坏 CSS 的内容写进声明里。
    """
    text = str(indent or "").strip()
    if text:
        text = validate_value("text_indent", text)
    return ParagraphSpacingOptions(indent=text, include_div=bool(include_div))


_BR_RE = re.compile(r"<br\s*/?\s*>", re.IGNORECASE)
_BARE_P_RE = re.compile(r"<p(?!\s[^>]*class=)(\s|>)", re.IGNORECASE)
_BARE_H1_RE = re.compile(r"<h1(?!\s[^>]*class=)(\s|>)", re.IGNORECASE)
_CSS_SUFFIX = ".css"
#: append 模式在「完全没有样式表」时新建的文件名（固定名，便于幂等与 OPF 去重）。
_FALLBACK_CSS_NAME = "epub-compact.css"


class EpubOptimizeError(EpubIOError):
    """格式优化失败。继承 ``EpubIOError``，便于工具统一兜底。"""


@dataclass
class OptimizeStats:
    """一次格式优化的统计与告警。"""

    mode: str = MODE_APPEND
    stylesheets: int = 0
    css_written: int = 0
    css_created: int = 0
    html_total: int = 0
    html_updated: int = 0
    opf_updated: bool = False
    empty_paragraphs_removed: int = 0
    empty_documents_changed: int = 0
    spine_documents: int = 0
    br_removed: int = 0

    # ---- 段落间距压平（去除段间空行）----
    spacing_enabled: bool = True
    spacing_stylesheets: int = 0
    spacing_changed_stylesheets: int = 0
    spacing_rules_flattened: int = 0
    spacing_declarations_rewritten: int = 0
    spacing_declarations_added: int = 0
    spacing_style_blocks_flattened: int = 0
    spacing_inline_cleaned: int = 0
    spacing_not_flattened: int = 0
    spacing_skipped_declarations: int = 0
    spacing_paragraph_classes: int = 0
    spacing_bare_paragraphs: bool = False
    spacing_fallback_target: str = ""
    spacing_samples: list = field(default_factory=list)

    warnings: list = field(default_factory=list)

    def as_dict(self):
        return {
            "mode": self.mode,
            "stylesheets": self.stylesheets,
            "css_written": self.css_written,
            "css_created": self.css_created,
            "html_total": self.html_total,
            "html_updated": self.html_updated,
            "opf_updated": self.opf_updated,
            "empty_paragraphs_removed": self.empty_paragraphs_removed,
            "empty_documents_changed": self.empty_documents_changed,
            "spine_documents": self.spine_documents,
            "br_removed": self.br_removed,
            "spacing": self.spacing_dict(),
            "warnings": list(self.warnings),
        }

    def spacing_dict(self):
        return {
            "enabled": self.spacing_enabled,
            "stylesheets": self.spacing_stylesheets,
            "changed_stylesheets": self.spacing_changed_stylesheets,
            "rules_flattened": self.spacing_rules_flattened,
            "declarations_rewritten": self.spacing_declarations_rewritten,
            "declarations_added": self.spacing_declarations_added,
            "style_blocks_flattened": self.spacing_style_blocks_flattened,
            "inline_cleaned": self.spacing_inline_cleaned,
            "not_flattened": self.spacing_not_flattened,
            "skipped_declarations": self.spacing_skipped_declarations,
            "paragraph_classes": self.spacing_paragraph_classes,
            "bare_paragraphs": self.spacing_bare_paragraphs,
            "fallback_target": self.spacing_fallback_target,
            "samples": list(self.spacing_samples),
        }

    def summary(self):
        """一句话中文摘要，供状态条 / 下载提示使用。"""
        mode_label = "追加紧凑规则" if self.mode == MODE_APPEND else "替换模板样式"
        parts = [mode_label]

        if self.mode == MODE_APPEND:
            if self.css_created:
                parts.append(f"新建样式表 {_FALLBACK_CSS_NAME}（{self.css_created} 个）")
            if self.css_created and self.html_updated:
                parts.append(f"链接到正文 {self.html_updated} 处")
            if self.css_created and self.opf_updated:
                parts.append("已更新 OPF 清单")
        else:
            if self.css_written:
                parts.append(f"应用模板样式表 {self.css_written} 个")
            else:
                parts.append("模板未提供样式表")
            if self.html_updated:
                parts.append(f"更新正文 {self.html_updated} 处")
            if self.opf_updated:
                parts.append("已更新 OPF 清单")

        if self.spacing_enabled:
            if self.spacing_rules_flattened or self.spacing_declarations_added:
                bits = [f"压平段落规则 {self.spacing_rules_flattened} 条"]
                if self.spacing_declarations_rewritten:
                    bits.append(f"清零间距 {self.spacing_declarations_rewritten} 处")
                if self.spacing_declarations_added:
                    bits.append(f"补显式 0 {self.spacing_declarations_added} 处")
                if self.spacing_style_blocks_flattened:
                    bits.append(
                        f"含正文内联样式 {self.spacing_style_blocks_flattened} 处"
                    )
                parts.append("、".join(bits))
            elif not self.css_created:
                parts.append("段落间距无需改写")
            if self.spacing_inline_cleaned:
                parts.append(f"清理段落内联间距 {self.spacing_inline_cleaned} 处")
            if self.spacing_fallback_target and not self.css_created:
                parts.append(f"兜底规则已在 {self.spacing_fallback_target}")
            elif self.spacing_fallback_target:
                parts.append(f"兜底规则写入 {self.spacing_fallback_target}")
            if self.spacing_not_flattened:
                parts.append(f"保守跳过 {self.spacing_not_flattened} 条风险规则")

        if self.empty_paragraphs_removed:
            parts.append(
                f"删除空段 {self.empty_paragraphs_removed} 个"
                f"（涉及 {self.empty_documents_changed} 个正文文档）"
            )
        if self.br_removed:
            parts.append(f"删除 <br> {self.br_removed} 个")

        if len(parts) == 1:
            parts.append("没有需要处理的内容")
        return "；".join(parts) + "。"


def extract_template_css_map(template_data):
    """从模板 EPUB 字节流里抽出全部样式表，返回 ``{文件名: 字节内容}``。"""
    import io
    import zipfile

    if not template_data:
        return {}
    try:
        archive = zipfile.ZipFile(io.BytesIO(template_data))
    except zipfile.BadZipFile as exc:
        raise EpubOptimizeError("模板不是有效的 EPUB/ZIP") from exc

    mapping = {}
    with archive:
        for name in archive.namelist():
            if name.lower().endswith(_CSS_SUFFIX):
                mapping[Path(name).name] = archive.read(name)
    return mapping


def _iter_html_files(book_root):
    for path in sorted(Path(book_root).rglob("*")):
        if path.is_file() and path.suffix.lower() in HTML_EXTS:
            yield path


def _iter_css_files(book_root):
    for path in sorted(Path(book_root).rglob("*")):
        if path.is_file() and path.suffix.lower() == _CSS_SUFFIX:
            yield path


def _read_text(path):
    try:
        return decode_text(Path(path).read_bytes())
    except OSError:
        return None


def _normalized_text(content):
    return content.replace("\r\n", "\n").replace("\r", "\n") if content else content


def _find_content_root(book_root, *, write=True):
    """定位样式表与正文所在的目录（优先 OEBPS / oebps，否则找含 HTML 的目录）。"""
    book_root = Path(book_root)
    for candidate in (book_root / "OEBPS", book_root / "oebps"):
        if candidate.is_dir():
            return candidate
    for directory in sorted(book_root.rglob("*")):
        if not directory.is_dir() or directory.name == "META-INF":
            continue
        if any(
            child.is_file() and child.suffix.lower() in HTML_EXTS
            for child in directory.iterdir()
        ):
            return directory
    fallback = book_root / "OEBPS"
    if write:
        fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _find_opf(book_root, content_root):
    book_root = Path(book_root)
    content_root = Path(content_root)
    for candidate in (book_root / "content.opf", content_root / "content.opf"):
        if candidate.is_file():
            return candidate
    for path in sorted(book_root.rglob("*.opf")):
        if path.is_file():
            return path
    return None


def _create_fallback_stylesheet(book_root, content_root, html_files, options, *, write, stats):
    """完全没有样式表时：新建一张只含段落兜底规则的 CSS，并挂到 HTML / OPF。

    挂接这一步不能省——样式表没进 OPF manifest、正文没有 ``<link>``，
    阅读器根本不会加载它，段间空行照样删不掉。
    """
    target = Path(content_root) / _FALLBACK_CSS_NAME
    block = build_paragraph_block(options, include_indent=True)
    already = False
    if target.is_file():
        existing = _read_text(target)
        if existing is not None and block.strip() in _normalized_text(existing):
            already = True

    stats.stylesheets += 1
    if not already:
        stats.css_created += 1
        if write:
            target.parent.mkdir(parents=True, exist_ok=True)
            write_text(target, block)
            stats.css_written += 1

    _link_stylesheet_in_html(html_files, target, write=write, stats=stats)
    _update_opf_manifest(book_root, content_root, [_FALLBACK_CSS_NAME], write=write, stats=stats)


def _link_stylesheet_in_html(html_files, css_file, *, write, stats):
    """给 HTML 的 ``</head>`` 前补一条指向 ``css_file`` 的 link。"""
    if not html_files:
        return

    css_file = Path(css_file)
    link_template = '<link href="{href}" rel="stylesheet" type="text/css" />'

    for html_path in html_files:
        stats.html_total += 1
        content = _read_text(html_path)
        if content is None:
            stats.warnings.append(f"无法读取文档：{html_path.name}")
            continue
        if css_file.name in content:
            continue
        if "</head>" not in content:
            stats.warnings.append(f"文档缺少 </head>，未链接紧凑样式表：{html_path.name}")
            continue
        try:
            href = os.path.relpath(str(css_file), str(html_path.parent)).replace("\\", "/")
        except ValueError:
            href = css_file.name
        link_tag = link_template.format(href=href)
        new_content = content.replace("</head>", f"{link_tag}\n</head>", 1)
        stats.html_updated += 1
        if write:
            write_text(html_path, new_content)


def _flatten_paragraph_spacing(book_root, options, *, write, stats, floor_indent=False):
    """压平段落间距（去除段间空行）。

    两种模式都要做这一步：它**就地改写**样式表里作用于段落的规则，而不是靠
    猜测类名往末尾追加覆盖规则——类选择器优先级高于标签选择器，追加的规则
    抢不过原书已有的 ``.block { margin: .5em 0 1em }``，这正是旧实现「有时删不掉」的原因。
    """
    spacing, warning = flatten_paragraph_spacing(
        book_root, options, write=write, floor_indent=floor_indent
    )
    stats.spacing_stylesheets = spacing.stylesheets
    stats.spacing_changed_stylesheets = spacing.changed_stylesheets
    stats.spacing_rules_flattened = spacing.rules_flattened
    stats.spacing_declarations_rewritten = spacing.declarations_rewritten
    stats.spacing_declarations_added = spacing.declarations_added
    stats.spacing_style_blocks_flattened = spacing.style_blocks_flattened
    stats.spacing_inline_cleaned = spacing.inline_styles_cleaned
    stats.spacing_not_flattened = spacing.rules_not_flattened
    stats.spacing_skipped_declarations = spacing.skipped_declarations
    stats.spacing_paragraph_classes = spacing.paragraph_classes
    stats.spacing_bare_paragraphs = spacing.bare_paragraphs
    if spacing.fallback_block_applied:
        stats.spacing_fallback_target = spacing.fallback_target
    stats.spacing_samples = list(spacing.samples[:6])
    if warning:
        stats.warnings.append(warning)
    return spacing


def _replace_css_and_inject_html(book_root, content_root, html_files, template_css_map, *, write, stats):
    injected_names = []
    for css_name, css_bytes in template_css_map.items():
        stats.stylesheets += 1
        if write:
            target_path = content_root / css_name
            # 模板字节原样落盘，不做文本换行翻译
            target_path.write_bytes(css_bytes)
        # 预览不落盘，但仍计入「将应用」的样式表数量
        stats.css_written += 1
        injected_names.append(css_name)

    if not html_files:
        if injected_names:
            stats.warnings.append("未找到 HTML 正文，仅更新了样式表")
        return injected_names

    first_html_dir = html_files[0].parent
    try:
        css_rel = os.path.relpath(content_root, first_html_dir).replace("\\", "/")
        css_rel = "" if css_rel == "." else css_rel + "/"
    except ValueError:
        css_rel = ""

    link_tags = "\n".join(
        f'  <link href="{css_rel}{name}" rel="stylesheet" type="text/css" />'
        for name in injected_names
    )

    for html_path in html_files:
        stats.html_total += 1
        content = _read_text(html_path)
        if content is None:
            stats.warnings.append(f"无法读取文档：{html_path.name}")
            continue

        original = content

        if injected_names and "</head>" in content:
            already_linked = any(name in content for name in injected_names)
            if not already_linked:
                content = content.replace("</head>", f"{link_tags}\n</head>")

        content = _BARE_P_RE.sub(r'<p class="bodyContent-1"\1', content)
        content = _BARE_H1_RE.sub(r'<h1 class="chaptertitle-c"\1', content)

        if content != original:
            stats.html_updated += 1
            if write:
                write_text(html_path, content)

    _update_opf_manifest(book_root, content_root, injected_names, write=write, stats=stats)
    return injected_names


def _update_opf_manifest(book_root, content_root, injected_names, *, write, stats):
    if not injected_names:
        return
    opf_path = _find_opf(book_root, content_root)
    if opf_path is None:
        stats.warnings.append("未找到 OPF，跳过更新样式表清单")
        return

    content = _read_text(opf_path)
    if content is None:
        stats.warnings.append("无法读取 OPF，跳过更新样式表清单")
        return

    original = content
    for css_name in injected_names:
        if css_name in content:
            continue
        item_id = f"css_{css_name.replace('.', '_').replace('-', '_')}"
        try:
            css_href = os.path.relpath(content_root / css_name, opf_path.parent).replace("\\", "/")
        except ValueError:
            css_href = f"OEBPS/{css_name}"
        manifest_item = f'    <item id="{item_id}" href="{css_href}" media-type="text/css"/>'
        if "</manifest>" in content:
            content = content.replace("</manifest>", f"{manifest_item}\n  </manifest>", 1)

    if content != original:
        stats.opf_updated = True
        if write:
            write_text(opf_path, content)


def _remove_br_tags(html_files, *, write, stats):
    for html_path in html_files:
        content = _read_text(html_path)
        if content is None:
            continue
        new_content, count = _BR_RE.subn("", content)
        if not count:
            continue
        stats.br_removed += count
        if write and new_content != content:
            write_text(html_path, new_content)


def _clear_empty_paragraphs(book_root, *, write, stats):
    text_stats, _samples = clean_book(book_root, EMPTY_PARAGRAPH_OPTIONS, write=write)
    stats.empty_paragraphs_removed = text_stats.empty_paragraphs_removed
    stats.empty_documents_changed = text_stats.changed_documents
    stats.spine_documents = text_stats.documents


def optimize_book(
    book_root,
    *,
    mode=MODE_APPEND,
    template_css_map=None,
    remove_empty_paragraphs=True,
    remove_br=True,
    remove_paragraph_spacing=True,
    paragraph_indent=DEFAULT_PARAGRAPH_INDENT,
    paragraph_include_div=False,
    write=True,
):
    """对已解包的 EPUB 目录执行格式优化，返回 :class:`OptimizeStats`。

    Args:
        book_root: 已解包的 EPUB 根目录。
        mode: ``append`` 或 ``replace``。
        template_css_map: replace 模式所需的 ``{文件名: 字节}``；append 忽略。
        remove_empty_paragraphs: 是否删除 spine 正文空段（复用 clean_book）。
        remove_br: 是否删除 HTML 中的 ``<br>``。
        remove_paragraph_spacing: 是否压平段落间距（去除段间空行）。
        paragraph_indent: 段首缩进取值；空串＝不改缩进。
        paragraph_include_div: 是否把 ``<div>`` 也当段落处理。
        write: False 时只统计不落盘，用于预览。
    """
    if mode not in MODE_VALUES:
        raise EpubOptimizeError(f"未知的优化模式：{mode}")

    book_root = Path(book_root)
    if not book_root.is_dir():
        raise EpubOptimizeError(f"解包目录不存在：{book_root}")

    if mode == MODE_REPLACE and template_css_map is None:
        raise EpubOptimizeError("替换模式需要模板样式表")

    stats = OptimizeStats(mode=mode)
    stats.spacing_enabled = bool(remove_paragraph_spacing)
    css_files = list(_iter_css_files(book_root))
    html_files = list(_iter_html_files(book_root))
    spacing_options = (
        build_spacing_options(paragraph_indent, paragraph_include_div)
        if remove_paragraph_spacing
        else None
    )
    floor_indent = False

    if mode == MODE_APPEND:
        if not css_files and spacing_options is not None:
            # 一本书都没有样式表时，段落兜底规则需要一个「能被加载」的载体
            content_root = _find_content_root(book_root, write=write)
            _create_fallback_stylesheet(
                book_root, content_root, html_files, spacing_options,
                write=write, stats=stats,
            )
            # 没有可改写的段落规则，段首缩进只能由兜底区块提供
            floor_indent = True
            if not html_files:
                stats.warnings.append("未找到 HTML 正文，仅新建了样式表")
    else:
        content_root = _find_content_root(book_root, write=write)
        if template_css_map is not None and not template_css_map:
            stats.warnings.append("模板 EPUB 中没有样式表")
        _replace_css_and_inject_html(
            book_root,
            content_root,
            html_files,
            template_css_map or {},
            write=write,
            stats=stats,
        )
        if not html_files:
            stats.warnings.append("未找到 HTML 文档")

    if spacing_options is not None:
        _flatten_paragraph_spacing(
            book_root, spacing_options, write=write, stats=stats, floor_indent=floor_indent
        )

    if remove_empty_paragraphs:
        try:
            _clear_empty_paragraphs(book_root, write=write, stats=stats)
        except EpubIOError as exc:
            stats.warnings.append(str(exc))

    if remove_br and html_files:
        _remove_br_tags(html_files, write=write, stats=stats)

    return stats


def render_preview_html(stats):
    """把统计渲染成一段简单 HTML，供「预览效果」接口返回。"""

    def row(label, value):
        return f"<tr><td>{escape(label)}</td><td>{escape(str(value))}</td></tr>"

    mode_label = "追加紧凑规则" if stats.mode == MODE_APPEND else "替换模板样式"
    rows = [
        row("模式", mode_label),
        row("样式表", stats.stylesheets),
    ]
    if stats.mode == MODE_APPEND:
        if stats.css_created:
            rows.extend(
                [
                    row("新建样式表", stats.css_created),
                    row("链接到正文", stats.html_updated),
                    row("更新 OPF 清单", "是" if stats.opf_updated else "否"),
                ]
            )
    else:
        rows.extend(
            [
                row("应用模板样式表", stats.css_written),
                row("更新的正文文档", stats.html_updated),
                row("更新 OPF 清单", "是" if stats.opf_updated else "否"),
            ]
        )
    if stats.spacing_enabled:
        rows.extend(
            [
                row("识别到的段落样式", stats.spacing_paragraph_classes),
                row("压平的段落规则", stats.spacing_rules_flattened),
                row("清零的间距声明", stats.spacing_declarations_rewritten),
                row("补写的显式 0", stats.spacing_declarations_added),
                row("改写的样式表", f"{stats.spacing_changed_stylesheets} / {stats.spacing_stylesheets}"),
                row("正文内联 &lt;style&gt;", stats.spacing_style_blocks_flattened),
                row("清理的内联间距", stats.spacing_inline_cleaned),
                row("兜底规则位置", stats.spacing_fallback_target or "（无样式表）"),
                row("无 class 的裸段落", "有，已由兜底规则压制" if stats.spacing_bare_paragraphs else "无"),
                row(
                    "保守跳过",
                    f"{stats.spacing_not_flattened} 条规则 / {stats.spacing_skipped_declarations} 条声明",
                ),
            ]
        )
    rows.extend(
        [
            row("删除空段", stats.empty_paragraphs_removed),
            row("涉及正文文档", stats.empty_documents_changed),
            row("删除 &lt;br&gt;", stats.br_removed),
        ]
    )
    warning_html = ""
    if stats.warnings:
        items = "".join(f"<li>{escape(item)}</li>" for item in stats.warnings)
        warning_html = f"<p><strong>提示</strong></p><ul>{items}</ul>"
    detail_html = ""
    if stats.spacing_samples:
        items = "".join(f"<li>{escape(item)}</li>" for item in stats.spacing_samples)
        detail_html = f"<p><strong>改写明细</strong></p><ul>{items}</ul>"

    return (
        "<!DOCTYPE html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<title>格式优化预览</title>"
        "<style>"
        "body{font-family:system-ui,sans-serif;margin:16px;color:#20242a}"
        "table{border-collapse:collapse;width:100%;max-width:480px}"
        "td{border:1px solid #e1e5ea;padding:6px 10px;font-size:14px}"
        "td:first-child{background:#fafbfc;width:40%}"
        "</style></head><body>"
        f"<p>{escape(stats.summary())}</p>"
        f"<table>{''.join(rows)}</table>"
        f"{detail_html}"
        f"{warning_html}"
        "</body></html>"
    )
