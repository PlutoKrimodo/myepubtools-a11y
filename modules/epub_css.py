# -*- coding: utf-8 -*-
"""样式表（CSS）读写公共层。

与其它公共层的分工：``epub_io`` 管归档、``epub_package`` 管 OPF/NCX、
``epub_text`` 管正文、本模块管**样式表**。

本模块**没有** ``TOOL`` 字典，属于公共模块，``registry.discover()`` 会自动跳过。

两类能力
--------
1. **安全清理**——只做语义上零风险的删除：
   * 空规则块（``.kaiti { }``，无论选择器是什么）
   * 逐字重复的 ``@font-face``
   * 选择器与声明块都重复的规则

   故意**不做**的两件事：删除「未被 HTML 引用」的规则（误删风险高）、
   合并多个样式表（结构性改动）。

2. **统一排版覆盖**——把行距 / 段首缩进 / 段间距 / 字号作为一段带标记的
   覆盖规则追加到「最后一个被链接的样式表」末尾，保证在层叠中胜出；
   重复运行会就地替换该区块，不会越积越多。

3. **压平段落间距（去除段间空行）**——见 :func:`flatten_paragraph_spacing`。
   先扫描正文，找出真正用在 ``<p>`` 上的样式类 / id，再**就地改写**样式表里
   作用于段落的规则：上下 margin / padding 压成 0、按需补回 UA 默认值缺失的
   显式 0、并把段首缩进设为指定值。语义对齐 Calibre 的
   「删除段间空行」（``--remove-paragraph-spacing``）。

   为什么不用「追加覆盖规则」：类选择器（``.block``）的优先级高于
   ``body p`` 这类标签选择器，追加的覆盖规则抢不过原书已有的
   ``.block { margin: .5em 0 1em }``，段间空行因此删不掉——只能从源头改写。

保真度
------
与 :mod:`modules.epub_text` 同样的原则：**没有实际改动的样式表不写回**，
保持逐字节不变；只有改动过的才重写。清理过程保留原有注释、缩进与换行
（用户选择的是「保留可读性」，不做压缩）。
"""
from __future__ import annotations

import posixpath
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from xml.sax.saxutils import escape

from modules.epub_io import HTML_EXTS, EpubIOError, decode_text, write_text
from modules.epub_package import spine_documents

CSS_SUFFIX = ".css"

OVERRIDE_START = "/* === EPUB 工具箱：统一排版（重复运行会覆盖本区块）=== */"
OVERRIDE_END = "/* === EPUB 工具箱：统一排版 结束 === */"

_NO_STYLESHEET_MESSAGE = "EPUB 中找不到样式表文件，无法追加排版覆盖规则"

#: 排版取值允许的字符集——顺带挡掉 `{` `}` `;` 这类能破坏样式表的注入。
_VALUE_RE = re.compile(r"^[A-Za-z0-9%.,\-+*/()# ]+$")

_PARAM_LABELS = {
    "font_size": "正文字号",
    "line_height": "行距",
    "text_indent": "段首缩进",
    "paragraph_spacing": "段间距",
}


class EpubCssError(EpubIOError):
    """样式表读写失败。继承 ``EpubIOError``，便于工具统一兜底。"""


@dataclass(frozen=True)
class TypographyOptions:
    """统一排版参数；空字符串表示该项不动。"""

    font_size: str = ""
    line_height: str = ""
    text_indent: str = ""
    paragraph_spacing: str = ""

    @property
    def enabled(self):
        return any((self.font_size, self.line_height, self.text_indent, self.paragraph_spacing))


@dataclass
class CssStats:
    """样式表处理统计。"""

    stylesheets: int = 0
    changed_stylesheets: int = 0
    empty_rules_removed: int = 0
    duplicate_font_faces_removed: int = 0
    duplicate_rules_removed: int = 0
    repeated_family_kept: int = 0
    override_applied: bool = False
    override_target: str = ""
    changed_files: list = field(default_factory=list)
    removed_samples: list = field(default_factory=list)

    def as_dict(self):
        return {
            "stylesheets": self.stylesheets,
            "changed_stylesheets": self.changed_stylesheets,
            "empty_rules_removed": self.empty_rules_removed,
            "duplicate_font_faces_removed": self.duplicate_font_faces_removed,
            "duplicate_rules_removed": self.duplicate_rules_removed,
            "repeated_family_kept": self.repeated_family_kept,
            "override_applied": self.override_applied,
            "override_target": self.override_target,
            "changed_files": list(self.changed_files),
            "removed_samples": list(self.removed_samples),
        }


def stats_text(stats):
    """把统计压成一句中文说明。"""
    parts = []
    if stats.stylesheets:
        parts.append(f"检查 {stats.stylesheets} 个样式表，改了 {stats.changed_stylesheets} 个")
    if stats.empty_rules_removed:
        parts.append(f"删除空规则 {stats.empty_rules_removed} 条")
    if stats.duplicate_font_faces_removed:
        parts.append(f"删除重复 @font-face {stats.duplicate_font_faces_removed} 条")
    if stats.duplicate_rules_removed:
        parts.append(f"删除重复规则 {stats.duplicate_rules_removed} 条")
    if stats.repeated_family_kept:
        parts.append(
            f"保留同名字体 @font-face {stats.repeated_family_kept} 条（src 回退顺序有意义）"
        )
    if stats.override_applied:
        parts.append(f"排版覆盖规则已写入 {stats.override_target}")
    return "；".join(parts) if parts else "没有需要处理的内容。"


# --------------------------------------------------------------------------- #
# 扫描：切成「块」与「其余文本」，拼接可逐字节还原
# --------------------------------------------------------------------------- #
def _skip_string(text, index):
    quote = text[index]
    index += 1
    length = len(text)
    while index < length:
        char = text[index]
        if char == "\\":
            index += 2
            continue
        if char == quote:
            return index + 1
        index += 1
    return length


def _skip_comment(text, index):
    end = text.find("*/", index + 2)
    return len(text) if end < 0 else end + 2


def _find_matching_brace(text, open_index):
    """返回与 ``open_index`` 处 ``{`` 配对的 ``}`` 下标；未闭合返回 None。"""
    depth = 0
    index = open_index
    length = len(text)
    while index < length:
        char = text[index]
        if char == "/" and text.startswith("/*", index):
            index = _skip_comment(text, index)
            continue
        if char in "\"'":
            index = _skip_string(text, index)
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
        index += 1
    return None


def scan(text):
    """把样式表切成片段序列。

    片段有两种，拼接起来可逐字节还原原文：

    * ``("filler", 文本)``——空白、注释、``@import ...;`` 这类顶层语句
    * ``("block", prelude, body)``——``prelude`` 为 ``{`` 之前的原文，``body`` 为大括号内原文
    """
    items = []
    index = 0
    length = len(text)
    filler_start = 0

    while index < length:
        char = text[index]
        if char == "/" and text.startswith("/*", index):
            index = _skip_comment(text, index)
            continue
        if char in "\"'":
            index = _skip_string(text, index)
            continue
        if char == ";":
            # 顶层语句（@import / @charset 之类）到此结束
            items.append(("filler", text[filler_start:index + 1]))
            index += 1
            filler_start = index
            continue
        if char == "{":
            close = _find_matching_brace(text, index)
            if close is None:
                # 未闭合，当作普通字符，避免把文件截断
                index += 1
                continue
            items.append(("block", text[filler_start:index], text[index + 1:close]))
            index = close + 1
            filler_start = index
            continue
        index += 1

    if filler_start < length:
        items.append(("filler", text[filler_start:]))
    return items


def _split_prelude(prelude):
    """把 prelude 拆成 ``(前导空白与注释, 选择器本体)``。"""
    index = 0
    length = len(prelude)
    while index < length:
        char = prelude[index]
        if char.isspace():
            index += 1
            continue
        if prelude.startswith("/*", index):
            end = prelude.find("*/", index + 2)
            if end < 0:
                return prelude[:index], prelude[index:]
            index = end + 2
            continue
        break
    return prelude[:index], prelude[index:]


# --------------------------------------------------------------------------- #
# 归一化：用于判断「逐字重复」
# --------------------------------------------------------------------------- #
def _strip_comments(text):
    return re.sub(r"/\*.*?\*/", " ", text, flags=re.S)


def normalize_selector(text):
    return re.sub(r"\s+", " ", _strip_comments(text)).strip()


def normalize_body(text):
    """把声明块归一化：去注释、压空白、去标点两侧空白、去尾分号。"""
    text = _strip_comments(text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([:;,])\s*", r"\1", text)
    return text.strip().rstrip(";")


def _font_family_of(body):
    match = re.search(r"font-family\s*:\s*([^;]+)", _strip_comments(body), re.I)
    return match.group(1).strip().strip("\"'").lower() if match else ""


# --------------------------------------------------------------------------- #
# 安全清理
# --------------------------------------------------------------------------- #
def clean_stylesheet(text):
    """对单个样式表做安全清理，返回 ``(新文本, 计数, 被删样本)``。

    没有任何实际改动时**原样返回入参**，调用方据此跳过写盘。
    """
    counters = {
        "empty_rules_removed": 0,
        "duplicate_font_faces_removed": 0,
        "duplicate_rules_removed": 0,
        "repeated_family_kept": 0,
    }
    samples = []
    if not text.strip():
        return text, counters, samples

    # 预先统计「同名字体出现多次」的条目数，用于解释为何 @font-face 数量没怎么降。
    families = Counter()
    for item in scan(text):
        if item[0] == "block":
            _head, selector = _split_prelude(item[1])
            if normalize_selector(selector).lower().startswith("@font-face"):
                families[_font_family_of(item[2])] += 1
    counters["repeated_family_kept"] = sum(count for count in families.values() if count > 1)

    result = []

    # 第一遍：删除空块（保留其前导空白与注释，维持可读性）
    for item in scan(text):
        if item[0] != "block":
            result.append(item)
            continue
        head, selector = _split_prelude(item[1])
        if not item[2].strip():
            # 连选择器都是空的 `{ }` 属于无效语法、同样无任何效果，一并删除
            counters["empty_rules_removed"] += 1
            if len(samples) < 6:
                samples.append(f"{normalize_selector(selector) or '(无选择器)'} {{ }}")
            if head:
                result.append(("filler", head))
            continue
        result.append(item)

    # 第二遍：删除逐字重复的 @font-face 与重复规则
    seen_font_faces = set()
    seen_rules = set()
    final = []
    for item in result:
        if item[0] != "block":
            final.append(item)
            continue
        head, selector = _split_prelude(item[1])
        name = normalize_selector(selector)
        if not name:
            final.append(item)
            continue

        lowered = name.lower()
        if lowered.startswith("@font-face"):
            key = normalize_body(item[2])
            if key in seen_font_faces:
                counters["duplicate_font_faces_removed"] += 1
                if len(samples) < 6:
                    samples.append(f"@font-face（重复）{key[:60]}")
                if head:
                    final.append(("filler", head))
                continue
            seen_font_faces.add(key)
            final.append(item)
            continue

        if lowered.startswith("@"):
            final.append(item)
            continue

        key = (name, normalize_body(item[2]))
        if key in seen_rules:
            counters["duplicate_rules_removed"] += 1
            if len(samples) < 6:
                samples.append(f"{name} {{ {key[1][:60]} }}")
            if head:
                final.append(("filler", head))
            continue
        seen_rules.add(key)
        final.append(item)

    if not any(
        counters[key]
        for key in ("empty_rules_removed", "duplicate_font_faces_removed", "duplicate_rules_removed")
    ):
        return text, counters, samples

    rebuilt = "".join(
        item[1] if item[0] == "filler" else item[1] + "{" + item[2] + "}"
        for item in final
    )
    return rebuilt, counters, samples


# --------------------------------------------------------------------------- #
# 统一排版覆盖
# --------------------------------------------------------------------------- #
def validate_value(name, value):
    """校验排版取值，顺带挡掉能破坏样式表的注入字符。"""
    text = str(value or "").strip()
    if not text:
        return ""
    if "/*" in text or "*/" in text or not _VALUE_RE.match(text):
        label = _PARAM_LABELS.get(name, name)
        raise EpubCssError(
            f"{label}的取值不合法：{text}（只允许数字、单位与常见 CSS 关键字）"
        )
    return text


def check_options(options):
    """提前校验所有排版参数，避免处理到一半才报错。"""
    return TypographyOptions(
        font_size=validate_value("font_size", options.font_size),
        line_height=validate_value("line_height", options.line_height),
        text_indent=validate_value("text_indent", options.text_indent),
        paragraph_spacing=validate_value("paragraph_spacing", options.paragraph_spacing),
    )


def build_override_block(options):
    """生成排版覆盖规则区块；所有参数都为空时返回空串。"""
    body_declarations = []
    paragraph_declarations = []
    if options.font_size:
        body_declarations.append(("font-size", options.font_size))
    if options.line_height:
        body_declarations.append(("line-height", options.line_height))
    if options.text_indent:
        paragraph_declarations.append(("text-indent", options.text_indent))
    if options.paragraph_spacing:
        paragraph_declarations.append(("margin-bottom", options.paragraph_spacing))

    if not body_declarations and not paragraph_declarations:
        return ""

    lines = [OVERRIDE_START]
    for selector, declarations in (("body", body_declarations), ("p", paragraph_declarations)):
        if not declarations:
            continue
        lines.append(f"{selector} {{")
        lines.extend(f"    {name}: {value};" for name, value in declarations)
        lines.append("}")
    lines.append(OVERRIDE_END)
    return "\n".join(lines) + "\n"


def find_override_block(text):
    """定位已存在的排版覆盖区块，返回 ``(起, 止)`` 或 None。

    结尾会一并吞掉紧跟区块的空白：区块自身以换行收尾，若不吞掉它，
    每替换一次就会多出一个空行。
    """
    start = text.find(OVERRIDE_START)
    if start < 0:
        return None
    end = text.find(OVERRIDE_END, start)
    if end < 0:
        return None
    end += len(OVERRIDE_END)
    while end < len(text) and text[end] in " \t\r\n":
        end += 1
    return start, end


def apply_override(text, options):
    """把排版覆盖规则写进样式表；重复运行就地替换，不会叠加。返回 ``(新文本, 是否变化)``。"""
    block = _match_newlines(build_override_block(options), text)
    existing = find_override_block(text)

    if existing is not None:
        start, end = existing
        if not block:
            trimmed = text[:start].rstrip()
            new_text = f"{trimmed}{_join_separator(trimmed)}" if trimmed else ""
        else:
            new_text = text[:start] + block + text[end:]
        return new_text, new_text != text

    if not block:
        return text, False

    return text + _join_separator(text) + block, True


# --------------------------------------------------------------------------- #
# 定位样式表
# --------------------------------------------------------------------------- #
_LINK_RE = re.compile(r"<link\b[^>]*>", re.I)
_HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)
_REL_RE = re.compile(r"""rel\s*=\s*["']([^"']*)["']""", re.I)
_CHARSET_RE = re.compile(r"""^(\s*)@charset\s+["'][^"']*["']\s*;""", re.I)


def all_stylesheets(book_root):
    """列出归档内所有 CSS 文件的相对路径（排序后）。"""
    book_root = Path(book_root)
    return sorted(
        path.relative_to(book_root).as_posix()
        for path in book_root.rglob("*")
        if path.is_file() and path.suffix.lower() == CSS_SUFFIX
    )


def linked_stylesheets(book_root):
    """按文档顺序收集被 ``<link>`` 引用的样式表，返回 ``(每文档的最后一张, 全部)``。"""
    book_root = Path(book_root)
    lasts = []
    everything = []
    for path in sorted(book_root.rglob("*")):
        if not path.is_file() or path.suffix.lower() not in (".html", ".htm", ".xhtml"):
            continue
        text = decode_text(path.read_bytes())
        directory = path.parent.relative_to(book_root).as_posix()
        hrefs = []
        for tag in _LINK_RE.findall(text):
            href_match = _HREF_RE.search(tag)
            if not href_match:
                continue
            rel_match = _REL_RE.search(tag)
            rel = rel_match.group(1).lower() if rel_match else ""
            href = href_match.group(1).strip()
            if "stylesheet" not in rel and not href.lower().split("?")[0].endswith(CSS_SUFFIX):
                continue
            target = posixpath.normpath(posixpath.join(directory, href.split("?")[0])).lstrip("./")
            if (book_root / target).is_file():
                hrefs.append(target)
        if hrefs:
            everything.extend(hrefs)
            lasts.append(hrefs[-1])  # 层叠中最后链接的优先级最高
    return lasts, everything


def resolve_override_target(book_root):
    """挑出「追加排版覆盖规则」的目标样式表。

    选所有文档里被**最后链接**次数最多的那一个——它在这本书的层叠中优先级最高，
    覆盖规则放这里最稳。找不到时回落到归档内任意一个样式表。
    """
    book_root = Path(book_root)
    lasts, everything = linked_stylesheets(book_root)

    if lasts:
        counter = Counter(lasts)
        best = max(counter.items(), key=lambda pair: (pair[1], -lasts.index(pair[0])))
        return best[0]

    candidates = all_stylesheets(book_root)
    if candidates:
        return candidates[-1]
    raise EpubCssError(_NO_STYLESHEET_MESSAGE)


def _fix_charset(text):
    """把 ``@charset`` 声明统一成 UTF-8，与实际写出的编码保持一致。"""
    match = _CHARSET_RE.match(text)
    if not match or "utf-8" in match.group(0).lower():
        return text
    return '@charset "UTF-8";' + text[match.end():]


# --------------------------------------------------------------------------- #
# 压平段落间距：就地改写「作用于段落的规则」
# --------------------------------------------------------------------------- #:
#: 始终视为段落标记的标签。
PARAGRAPH_TAGS = ("p",)

#: 用户勾选「把 <div> 也当段落」后才纳入的标签。
OPTIONAL_PARAGRAPH_TAGS = ("div",)

#: 兜底区块的起止标记：重复运行就地替换，不会越积越多。
PARAGRAPH_BLOCK_START = "/* === EPUB 工具箱：段落间距兜底（重复运行会覆盖本区块）=== */"
PARAGRAPH_BLOCK_END = "/* === EPUB 工具箱：段落间距兜底 结束 === */"

#: 简写属性：上下分量压成 0，左右分量原样保留。
_SPACING_SHORTHANDS = frozenset({"margin", "padding", "border-width"})

#: 长写属性：直接置 0。
_VERTICAL_LONGHANDS = frozenset(
    {"margin-top", "margin-bottom", "padding-top", "padding-bottom"}
)

#: 这些关键字按规范只能作为简写的唯一取值，拆成分量会写出非法声明，故整条跳过。
_CSS_WIDE_KEYWORDS = frozenset({"inherit", "initial", "unset", "revert", "revert-layer"})

#: 内部还能再嵌套规则的 at-rule。
_NESTING_AT_RULES = ("@media", "@supports", "@layer", "@document")

#: 不改变排版的对齐方式：这些段落不加首行缩进（缩进对它们没有分段效果）。
_NO_INDENT_ALIGNMENTS = frozenset({"center", "right", "end"})

_IMPORTANT_RE = re.compile(r"!\s*important\b", re.I)
_COMBINATOR_SPLIT_RE = re.compile(r"\s*[>+~]\s*|\s+")
_PSEUDO_RE = re.compile(r"::?[A-Za-z-]+(?:\([^)]*\))?")
_PSEUDO_ELEMENT_RE = re.compile(
    r"::[\w-]+|:(?:first-line|first-letter|before|after|selection)\b", re.I
)
_ELEMENT_RE = re.compile(r"^\*|^[A-Za-z][\w-]*")
_CLASS_RE = re.compile(r"\.([\w-]+)")
_ID_RE = re.compile(r"#([\w-]+)")
_ATTR_VALUE_RE = re.compile(r"""["']([^"']+)["']""")
_TAG_RE = re.compile(r"<(/?)([A-Za-z][\w:.-]*)((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>", re.I)
_CLASS_ATTR_RE = re.compile(r"""\bclass\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s/>]+))""", re.I)
_ID_ATTR_RE = re.compile(r"""\bid\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s/>]+))""", re.I)
_STYLE_ATTR_RE = re.compile(r"""\bstyle\s*=\s*(?:"([^"]*)"|'([^']*)')""", re.I)
_STYLE_BLOCK_RE = re.compile(r"(<style\b[^>]*>)(.*?)(</style\s*>|\Z)", re.I | re.S)


@dataclass(frozen=True)
class ParagraphSpacingOptions:
    """一次「去除段间空行」的参数。"""

    #: 段首缩进取值；空串表示完全不改缩进（只压间距）。
    indent: str = "1.5em"
    #: 是否把 ``<div>`` 也当作段落标记（默认否：页面容器多为 div，误伤风险高）。
    include_div: bool = False
    #: 是否清理段落标签上内联 ``style`` 里的垂直间距（内联优先级最高，不清就压不住）。
    strip_inline: bool = True

    def tags(self):
        return PARAGRAPH_TAGS + (OPTIONAL_PARAGRAPH_TAGS if self.include_div else ())


@dataclass
class ParagraphSpacingStats:
    """段落间距压平的统计。"""

    documents: int = 0
    stylesheets: int = 0
    changed_stylesheets: int = 0
    changed_documents: int = 0
    rules_flattened: int = 0
    declarations_rewritten: int = 0
    declarations_added: int = 0
    inline_styles_cleaned: int = 0
    style_blocks_flattened: int = 0
    paragraph_classes: int = 0
    bare_paragraphs: bool = False
    skipped_declarations: int = 0
    rules_not_flattened: int = 0
    fallback_block_applied: bool = False
    fallback_target: str = ""
    changed_files: list = field(default_factory=list)
    samples: list = field(default_factory=list)

    def as_dict(self):
        return {
            "documents": self.documents,
            "stylesheets": self.stylesheets,
            "changed_stylesheets": self.changed_stylesheets,
            "changed_documents": self.changed_documents,
            "rules_flattened": self.rules_flattened,
            "declarations_rewritten": self.declarations_rewritten,
            "declarations_added": self.declarations_added,
            "inline_styles_cleaned": self.inline_styles_cleaned,
            "style_blocks_flattened": self.style_blocks_flattened,
            "paragraph_classes": self.paragraph_classes,
            "bare_paragraphs": self.bare_paragraphs,
            "skipped_declarations": self.skipped_declarations,
            "rules_not_flattened": self.rules_not_flattened,
            "fallback_block_applied": self.fallback_block_applied,
            "fallback_target": self.fallback_target,
            "changed_files": list(self.changed_files),
            "samples": list(self.samples),
        }


# ---- 选择器判定 ----------------------------------------------------------- #
def _split_selector_alternatives(selector):
    """按顶层逗号拆分选择器组（括号内的逗号不拆）。"""
    parts, depth, current = [], 0, []
    for char in selector:
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        if char == "," and depth == 0:
            parts.append("".join(current))
            current = []
            continue
        current.append(char)
    parts.append("".join(current))
    return [p for p in (part.strip() for part in parts) if p]


def _subject_compound(alternative):
    """取选择器的**主体**复合体（规则真正作用的那个元素），并去掉伪类 / 伪元素。"""
    cleaned = _COMBINATOR_SPLIT_RE.split(alternative.strip())
    compounds = [part for part in cleaned if part]
    if not compounds:
        return ""
    return _PSEUDO_RE.sub("", compounds[-1]).strip()


def _compound_names(compound):
    """从复合体里取出元素名、类名、id 与属性选择器的字面值（全部小写）。"""
    element = ""
    match = _ELEMENT_RE.match(compound)
    if match:
        element = match.group(0).lower()
    classes = {name.lower() for name in _CLASS_RE.findall(compound)}
    ids = {name.lower() for name in _ID_RE.findall(compound)}
    # ``[class*="bodyContent"]`` 这类属性选择器：把字面值也算进「类名」集合
    classes |= {value.lower() for value in _ATTR_VALUE_RE.findall(compound)}
    return element, classes, ids


#: 选择器判定结果。
TARGET = "target"      # 每个分支都作用于段落
MIXED = "mixed"        # 一部分分支作用于段落，一部分作用于标题等别的东西
DIV_ONLY = "div-only"  # 只作用于 div（未勾选「把 div 当段落」时保守跳过）
OTHER = "other"


def _alternative_status(alternative, names, tags, div_names):
    """单个选择器分支的主体是否作用于段落。"""
    if _PSEUDO_ELEMENT_RE.search(alternative):
        # ``p::first-line`` 之类不是段落盒子本身，margin 对它无意义
        return OTHER
    compound = _subject_compound(alternative)
    if not compound:
        return OTHER
    element, classes, ids = _compound_names(compound)
    handles = classes | ids
    if element == "*":
        # ``*`` 会波及标题、图片等一切元素，改写它风险太高
        return OTHER
    if element:
        # 主体限定了标签名：``p`` / 勾选后的 ``div`` 才算段落，``h1``、``body`` 一律放过
        if element in tags:
            return TARGET
        return DIV_ONLY if element in OPTIONAL_PARAGRAPH_TAGS else OTHER
    if handles & names:
        return TARGET
    if handles & div_names:
        return DIV_ONLY
    return OTHER


def _selector_status(selector, names, tags, div_names):
    """判定整条规则：全部分支都命中段落才算 ``TARGET``，命中一半算 ``MIXED``。"""
    statuses = []
    for alternative in _split_selector_alternatives(normalize_selector(selector)):
        if alternative.startswith("@"):
            continue
        statuses.append(_alternative_status(alternative, names, tags, div_names))
    if not statuses:
        return OTHER
    if all(status == TARGET for status in statuses):
        return TARGET
    if any(status == TARGET for status in statuses):
        return MIXED
    if any(status == DIV_ONLY for status in statuses):
        return DIV_ONLY
    return OTHER


# ---- 声明解析与改写 ------------------------------------------------------- #
def _skip_ws_comments(text, index):
    length = len(text)
    while index < length:
        char = text[index]
        if char.isspace():
            index += 1
            continue
        if char == "/" and text.startswith("/*", index):
            index = _skip_comment(text, index)
            continue
        break
    return index


def _find_decl_end(text, index):
    """找到本条声明结束的 ``;`` 下标；没有则返回文本长度。"""
    length = len(text)
    depth = 0
    while index < length:
        char = text[index]
        if char == "/" and text.startswith("/*", index):
            index = _skip_comment(text, index)
            continue
        if char in "\"'":
            index = _skip_string(text, index)
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth = max(0, depth - 1)
        elif char == ";" and depth == 0:
            return index
        index += 1
    return length


def _find_colon(text, index, limit):
    """找到属性名与值之间的 ``:``；遇到 ``;`` / ``{`` / ``}`` 视为解析失败。"""
    while index < limit:
        char = text[index]
        if char == "/" and text.startswith("/*", index):
            index = _skip_comment(text, index)
            continue
        if char in "\"'":
            index = _skip_string(text, index)
            continue
        if char == ":":
            return index
        if char in ";{}":
            return None
        index += 1
    return None


def _parse_declarations(body):
    """把声明块正文拆成声明项，拼接可逐字节还原原文。

    每项为 ``dict``：``kind`` 为 ``"decl"`` 时带 ``head`` / ``value`` / ``bang`` /
    ``tail`` / ``name``；否则只有 ``raw``（空白、注释、残缺片段）。
    """
    items = []
    index, length = 0, len(body)
    while index < length:
        start = index
        name_start = _skip_ws_comments(body, index)
        if name_start >= length:
            items.append({"kind": "filler", "raw": body[start:]})
            break
        end = _find_decl_end(body, name_start)
        semi = end if end < length else None
        full_end = end + 1 if semi is not None else length
        colon = _find_colon(body, name_start, end)
        if colon is None:
            items.append({"kind": "filler", "raw": body[start:full_end]})
            index = full_end
            continue
        core_end = semi if semi is not None else length
        core = body[colon + 1:core_end]
        bang_match = None
        for match in _IMPORTANT_RE.finditer(core):
            if not core[match.end():].strip():
                bang_match = match
        value = core[:bang_match.start()] if bang_match else core
        items.append({
            "kind": "decl",
            "raw": body[start:full_end],
            "head": body[start:colon + 1],
            "value": value,
            "bang": core[bang_match.start():core_end] if bang_match else "",
            "tail": body[core_end:full_end],
            "name": _strip_comments(body[name_start:colon]).strip().lower(),
            "modified": False,
        })
        index = full_end
    return items


def _clean_value(value):
    return re.sub(r"\s+", " ", _strip_comments(value)).strip()


def _split_top_level(value):
    """按空白拆分简写取值（括号内的空格不算分隔）；含括号不闭合时返回 None。"""
    parts, current, depth, index, length = [], [], 0, 0, len(value)
    while index < length:
        char = value[index]
        if char == "/" and value.startswith("/*", index):
            index = _skip_comment(value, index)
            continue
        if char in "\"'":
            end = _skip_string(value, index)
            current.append(value[index:end])
            index = end
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth < 0:
                return None
        if char.isspace() and depth == 0:
            if current:
                parts.append("".join(current))
                current = []
            index += 1
            continue
        current.append(char)
        index += 1
    if current:
        parts.append("".join(current))
    if depth != 0 or not parts:
        return None
    return parts


def _zero_vertical_shorthand(value):
    """把 ``margin`` / ``padding`` 简写的上下分量压成 0，左右原样保留。

    无法安全拆分（含 ``inherit`` 一类全局关键字、取值超过 4 段）时返回 None。
    """
    parts = _split_top_level(value)
    if not parts or len(parts) > 4:
        return None
    lowered = [part.lower() for part in parts]
    if any(part in _CSS_WIDE_KEYWORDS for part in lowered):
        return None
    if len(parts) == 1:
        top, right, bottom, left = parts * 4
    elif len(parts) == 2:
        top, right, bottom, left = parts[0], parts[1], parts[0], parts[1]
    elif len(parts) == 3:
        top, right, bottom, left = parts[0], parts[1], parts[2], parts[1]
    else:
        top, right, bottom, left = parts
    top = bottom = "0"
    if top == bottom == left == right:
        return "0"
    if left == right:
        # 两值形式是「上下 / 左右」，只有左右相等才写得出来
        return f"{top} {left}"
    # 四值形式的顺序是「上 右 下 左」，写反会串位
    return f"{top} {right} {bottom} {left}"


def _render_declaration(item):
    if not item["modified"]:
        return item["raw"]
    return f"{item['head']} {item['value']}{item['bang']}{item['tail']}"


def _detect_indent(rendered):
    """取声明块里第一条声明前面的缩进，供新增声明沿用同一风格。"""
    for match in re.finditer(r"\n([ \t]*)\S", rendered):
        return match.group(1)
    return "  "


def _append_declarations(rendered, additions):
    """把新增声明接在已有内容之后，沿用原块的多行 / 单行风格。"""
    if not additions:
        return rendered
    core = rendered.rstrip()
    trailing = rendered[len(core):]
    if core and not core.endswith((";", "}", "{")):
        core = f"{core};"
    if "\n" in rendered:
        indent = _detect_indent(rendered)
        joined = "".join(f"\n{indent}{name}: {value};" for name, value in additions)
        return f"{core}{joined}\n"
    joined = "".join(f" {name}: {value};" for name, value in additions)
    tail = trailing if trailing.endswith((" ", "\t")) else f"{trailing} "
    return f"{core}{joined}{tail}"


def flatten_declarations(body, options):
    """压平单个声明块，返回 ``(新正文, 重写条数, 新增条数, 跳过条数, 是否缩进跳过)``。"""
    items = _parse_declarations(body)
    declarations = [item for item in items if item["kind"] == "decl"]
    props = {item["name"] for item in declarations}

    alignment = ""
    for item in declarations:
        if item["name"] == "text-align":
            alignment = _clean_value(item["value"]).lower()
    skip_indent = alignment in _NO_INDENT_ALIGNMENTS
    want_indent = bool(options.indent) and not skip_indent

    rewritten = skipped = 0
    for item in declarations:
        name = item["name"]
        new_value = None
        if name in _SPACING_SHORTHANDS:
            new_value = _zero_vertical_shorthand(_clean_value(item["value"]))
            if new_value is None and _clean_value(item["value"]):
                skipped += 1
                continue
        elif name in _VERTICAL_LONGHANDS:
            new_value = "0"
        elif name == "text-indent" and want_indent:
            new_value = options.indent
        if new_value is None:
            continue
        if _clean_value(item["value"]).lower() == new_value.lower() and not item["bang"]:
            continue
        item["value"] = new_value
        item["bang"] = ""
        item["modified"] = True
        rewritten += 1

    additions = []
    # 上下 margin / padding：作者没写过的补显式 0，压掉阅读器 UA 默认留白
    for group, sides in (
        ("margin", ("margin-top", "margin-bottom")),
        ("padding", ("padding-top", "padding-bottom")),
    ):
        if any(item["name"] == group for item in declarations):
            continue
        for side in sides:
            if side not in props:
                additions.append((side, "0"))
    if not any(name.startswith("border") for name in props):
        additions.extend([("border-top", "0"), ("border-bottom", "0")])
    if want_indent and "text-indent" not in props:
        additions.append(("text-indent", options.indent))

    rendered = "".join(
        item["raw"] if item["kind"] != "decl" else _render_declaration(item) for item in items
    )
    new_body = _append_declarations(rendered, additions) if additions else rendered
    return new_body, rewritten, len(additions), skipped, skip_indent


# ---- 样式表文本级别 ------------------------------------------------------- #
def flatten_css_text(text, names, options, div_names=frozenset(), *, depth=0):
    """就地压平一段 CSS 文本里所有作用于段落的规则。

    返回 ``(新文本, 命中规则数, 重写条数, 新增条数, 跳过条数, 未压平条数)``。
    「跳过」指取值无法安全拆分的声明（``margin: inherit`` 之类）；
    「未压平」指命中了段落但被保守放过的整条规则（混合选择器、div 专用规则）。

    没有命中任何规则时**原样返回入参**，调用方据此跳过写盘。
    """
    tags = options.tags()
    counters = [0, 0, 0, 0, 0]  # rules, rewritten, added, skipped, 未压平
    rebuilt = []
    changed = False

    for item in scan(text):
        if item[0] != "block":
            rebuilt.append(item[1])
            continue
        prelude, body = item[1], item[2]
        _head, selector = _split_prelude(prelude)
        normalized = normalize_selector(selector).lower()

        if PARAGRAPH_BLOCK_START in prelude:
            # 我们自己的兜底区块，内容已经是我们想要的样子，不再二次改写，
            # 否则重复运行会不断往里追加声明。
            rebuilt.append(f"{prelude}{{{body}}}")
            continue

        if normalized.startswith("@"):
            if any(normalized.startswith(rule) for rule in _NESTING_AT_RULES) and depth < 6:
                new_body, *sub = flatten_css_text(
                    body, names, options, div_names, depth=depth + 1
                )
                for index, value in enumerate(sub):
                    counters[index] += value
                if new_body != body:
                    changed = True
                    rebuilt.append(f"{prelude}{{{new_body}}}")
                    continue
            rebuilt.append(f"{prelude}{{{body}}}")
            continue

        status = _selector_status(selector, names, tags, div_names)
        if status != TARGET:
            if status in (MIXED, DIV_ONLY):
                counters[4] += 1
            rebuilt.append(f"{prelude}{{{body}}}")
            continue

        new_body, rewritten, added, skipped, _skip_indent = flatten_declarations(body, options)
        if new_body == body:
            counters[3] += skipped
            rebuilt.append(f"{prelude}{{{body}}}")
            continue
        counters[0] += 1
        counters[1] += rewritten
        counters[2] += added
        counters[3] += skipped
        changed = True
        rebuilt.append(f"{prelude}{{{new_body}}}")

    if not changed:
        return text, 0, 0, 0, counters[3], counters[4]
    return "".join(rebuilt), *counters


# ---- 正文发现：谁真的用在 <p> 上 ------------------------------------------ #
def _attribute_value(pattern, text):
    for groups in pattern.findall(text):
        for value in groups:
            if value:
                return value
    return ""


def discover_paragraph_styles(source, tags, div_tags=()):
    """从一个 HTML 文本里发现段落样式名，并顺带统计待清理的内联间距。

    返回 ``(段落样式名集合, 出现在 div 上的样式名, 内联间距声明数, 是否有裸段落)``。
    ``div_tags`` 用于「未勾选把 div 当段落」时统计有多少规则只作用于 div，仅作提示；
    两个集合都按**全书**汇总后再相减，避免同一个类在甲文件用于 <p>、在乙文件用于
    <div> 时被误判成「div 专用」而漏改。
    """
    names = set()
    div_names = set()
    inline_count = 0
    bare = False
    for match in _TAG_RE.finditer(source):
        closing, tag, attributes = match.group(1), match.group(2).lower(), match.group(3)
        if closing or (tag not in tags and tag not in div_tags):
            continue
        class_attr = _attribute_value(_CLASS_ATTR_RE, attributes).strip()
        id_value = _attribute_value(_ID_ATTR_RE, attributes).strip()
        tokens = {token.lower() for token in class_attr.split() if token}
        if id_value:
            tokens.add(id_value.lower())
        if tag in tags:
            if not tokens:
                bare = True
            names |= tokens
            style = _attribute_value(_STYLE_ATTR_RE, attributes)
            for declaration in style.split(";") if style else ():
                name = declaration.split(":")[0].strip().lower()
                if name in _VERTICAL_LONGHANDS or name in _SPACING_SHORTHANDS:
                    inline_count += 1
        else:
            div_names |= tokens
    return names, div_names, inline_count, bare


def _clean_inline_style(style, options):
    """清洗内联 ``style``：上下间距压成 0、段首缩进按参数统一，其余声明原样保留。"""
    kept, cleaned = [], 0
    for declaration in style.split(";"):
        if not declaration.strip():
            continue
        head, _, raw_value = declaration.partition(":")
        name = head.strip().lower()
        if not raw_value.strip():
            kept.append(declaration)
            continue
        value = raw_value.strip()
        bang = ""
        bang_match = _IMPORTANT_RE.search(value)
        if bang_match and not value[bang_match.end():].strip():
            bang = value[bang_match.start():].strip()
            value = value[:bang_match.start()].strip()
        new_value = None
        if name in _SPACING_SHORTHANDS:
            new_value = _zero_vertical_shorthand(value)
            if new_value is None:
                new_value = "0" if name in ("margin", "padding") else None
        elif name in _VERTICAL_LONGHANDS:
            new_value = "0"
        elif name == "text-indent" and options.indent:
            new_value = options.indent
        if new_value is None:
            kept.append(declaration)
            continue
        if new_value != value or bang:
            cleaned += 1
        kept.append(f"{head.strip()}: {new_value}")
    if not cleaned:
        return style, 0
    return "; ".join(kept), cleaned


def _rewrite_tags_spacing(source, tags, options):
    """把正文标签上的内联间距改写掉，返回 ``(新文本, 清理处数)``。"""
    total = 0

    def _replace(match):
        nonlocal total
        closing, tag, attributes = match.group(1), match.group(2).lower(), match.group(3)
        if closing or tag not in tags:
            return match.group(0)
        style_match = _STYLE_ATTR_RE.search(attributes)
        if not style_match:
            return match.group(0)
        original = next((group for group in style_match.groups() if group is not None), "")
        cleaned, count = _clean_inline_style(original, options)
        if not count:
            return match.group(0)
        total += count
        start, end = style_match.span()
        quote = style_match.group(0)[style_match.group(0).index("=") + 1:].lstrip()[0]
        # 属性串在整段标签文本里的偏移：``<`` 与可能的 ``/`` 之后才是属性
        offset = 1 + len(closing)
        whole = match.group(0)
        return f"{whole[: offset + start]}style={quote}{cleaned}{quote}{whole[offset + end:]}"

    output = _TAG_RE.sub(_replace, source)
    return output, total


def _flatten_style_blocks(source, names, div_names, options, stats):
    """改写 HTML 内联 ``<style>`` 里的段落规则（很多书把样式写在每个正文文件里）。"""

    def _replace(match):
        opening, inner, closing = match.group(1), match.group(2), match.group(3)
        if "{" not in inner:
            return match.group(0)
        new_inner, rules, rewritten, added, skipped, not_flattened = flatten_css_text(
            inner, names, options, div_names
        )
        stats.declarations_rewritten += rewritten
        stats.declarations_added += added
        stats.skipped_declarations += skipped
        stats.rules_not_flattened += not_flattened
        if new_inner == inner:
            return match.group(0)
        stats.style_blocks_flattened += 1
        stats.rules_flattened += rules
        return f"{opening}{new_inner}{closing}"

    return _STYLE_BLOCK_RE.sub(_replace, source)


# ---- 兜底区块 ------------------------------------------------------------- #
def build_paragraph_block(options, include_indent=False):
    """生成兜底区块正文：压掉阅读器对 ``<p>`` 的默认上下留白。

    默认只写 margin——作者写在类里的 padding / border 由就地改写负责，兜底不越权。
    ``include_indent`` 用于「整本没有样式表、这是唯一一条规则」的场景：此时没有
    任何类规则会被就地改写，段首缩进只能由兜底区块提供。
    """
    declarations = ["margin-top: 0", "margin-bottom: 0"]
    if include_indent and options.indent:
        declarations.append(f"text-indent: {options.indent}")
    lines = [PARAGRAPH_BLOCK_START, "p {"]
    lines.extend(f"    {declaration};" for declaration in declarations)
    lines.append("}")
    lines.append(PARAGRAPH_BLOCK_END)
    return "\n".join(lines) + "\n"


def _match_newlines(block, host):
    """让新写入的区块沿用宿主文件的换行风格。

    样式表常见 CRLF（Calibre 一类工具链产出的书就是），而这里生成的区块一律用 LF；
    不统一的话，写出来就是一份「半 CRLF 半 LF」的表，重复运行还会因为换行不一致
    反复判定成「有改动」。
    """
    if "\r\n" in host and "\r\n" not in block:
        return block.replace("\n", "\r\n")
    return block


def _join_separator(text):
    """在文件末尾追加内容前需要的分隔换行；沿用该文件已有的换行风格。"""
    if not text or text.endswith("\n"):
        return ""
    return "\r\n" if "\r\n" in text else "\n"


def find_paragraph_block(text):
    """定位已存在的兜底区块，返回 ``(起, 止)`` 或 None。"""
    start = text.find(PARAGRAPH_BLOCK_START)
    if start < 0:
        return None
    end = text.find(PARAGRAPH_BLOCK_END, start)
    if end < 0:
        return None
    end += len(PARAGRAPH_BLOCK_END)
    while end < len(text) and text[end] in " \t\r\n":
        end += 1
    return start, end


def apply_paragraph_block(text, options, include_indent=False):
    """写入 / 就地替换兜底区块，返回 ``(新文本, 是否变化)``。"""
    block = _match_newlines(
        build_paragraph_block(options, include_indent=include_indent), text
    )
    existing = find_paragraph_block(text)
    if existing is not None:
        start, end = existing
        new_text = text[:start] + block + text[end:]
        return new_text, new_text != text
    return text + _join_separator(text) + block, True


# ---- 书级入口 ------------------------------------------------------------- #
def paragraph_documents(book_root):
    """按 spine 取正文文档；spine 不可用时回落到全部 HTML 并给出提示。"""
    book_root = Path(book_root)
    try:
        documents = spine_documents(book_root)
    except (EpubIOError, KeyError, TypeError, ValueError):
        # OPF 缺失 / XML 解析失败都不该让整个功能崩掉，回落到全书 HTML
        documents = []
    if documents:
        return [relative for relative in documents if (book_root / relative).is_file()], ""
    fallback = sorted(
        path.relative_to(book_root).as_posix()
        for path in book_root.rglob("*")
        if path.is_file() and path.suffix.lower() in HTML_EXTS
    )
    message = (
        "" if not fallback else "按 spine 未找到正文文档，已回落到全书 HTML 识别段落样式"
    )
    return fallback, message


def flatten_paragraph_spacing(book_root, options=None, write=True, floor_indent=False):
    """压平整本书的段落间距（去除段间空行），返回 ``(stats, warning)``。

    处理顺序：

    1. 读 spine 正文，找出**真正用在** ``<p>`` 上的样式类 / id；
    2. 据此就地改写每个样式表里作用于段落的规则（上下间距清零 + 段首缩进）；
    3. 改写正文文件里内联 ``<style>`` 的同批规则；
    4. 清理段落标签上的内联 ``style`` 间距（内联优先级最高，不清就压不住）；
    5. 在层叠最末的样式表写一段 ``p`` 兜底规则，压掉阅读器对段落的默认留白。

    ``write=False`` 时只统计不落盘，用于预览；``floor_indent`` 表示这本书没有
    任何可改写的段落规则、兜底区块是唯一的样式来源，此时把段首缩进也写进兜底。
    """
    options = options or ParagraphSpacingOptions()
    book_root = Path(book_root)
    stats = ParagraphSpacingStats()

    documents, warning = paragraph_documents(book_root)
    tags = options.tags()
    div_tags = () if options.include_div else OPTIONAL_PARAGRAPH_TAGS

    paragraph_names, all_div_names, inline_total, bare_paragraphs = set(), set(), 0, False
    for relative in documents:
        source = decode_text((book_root / relative).read_bytes())
        found, found_div, count, bare = discover_paragraph_styles(source, tags, div_tags)
        paragraph_names |= found
        all_div_names |= found_div
        inline_total += count
        bare_paragraphs = bare_paragraphs or bare
        stats.documents += 1
    div_names = all_div_names - paragraph_names
    names = paragraph_names
    stats.paragraph_classes = len(names)
    stats.bare_paragraphs = bare_paragraphs

    if not documents:
        return stats, warning or "EPUB 中没有正文文档，段落间距未处理"

    for relative in all_stylesheets(book_root):
        path = book_root / relative
        source = decode_text(path.read_bytes())
        text, rules, rewritten, added, skipped, not_flattened = flatten_css_text(
            source, names, options, div_names
        )
        stats.stylesheets += 1
        stats.rules_flattened += rules
        stats.declarations_rewritten += rewritten
        stats.declarations_added += added
        stats.skipped_declarations += skipped
        stats.rules_not_flattened += not_flattened
        if rules:
            stats.samples.append(f"{relative}：{rules} 条段落规则已压平")
        if text != source:
            stats.changed_stylesheets += 1
            stats.changed_files.append(relative)
            if write:
                write_text(path, _fix_charset(text))

    for relative in documents:
        path = book_root / relative
        source = decode_text(path.read_bytes())
        text = source
        if "<style" in text.lower():
            text = _flatten_style_blocks(text, names, div_names, options, stats)
        if options.strip_inline and inline_total:
            text, count = _rewrite_tags_spacing(text, tags, options)
            stats.inline_styles_cleaned += count
        if text != source:
            stats.changed_documents += 1
            stats.changed_files.append(relative)
            if write:
                write_text(path, text)

    try:
        target = resolve_override_target(book_root)
    except EpubCssError:
        target = ""
    if target:
        path = book_root / target
        source = decode_text(path.read_bytes())
        text, changed = apply_paragraph_block(source, options, include_indent=floor_indent)
        stats.fallback_target = target
        stats.fallback_block_applied = True
        if find_override_block(source) is not None and stats.stylesheets:
            warning = (
                warning
                or f"兜底规则排在 {target} 里「样式排版」覆盖区块之后，会覆盖那里设置的段间距；"
                "需要恢复请重新运行「样式排版」"
            )
        if changed:
            if target not in stats.changed_files:
                stats.changed_stylesheets += 1
                stats.changed_files.append(target)
            if write:
                write_text(path, _fix_charset(text))

    return stats, warning


# --------------------------------------------------------------------------- #
# 对外接口
# --------------------------------------------------------------------------- #
def process_book(book_root, clean=True, typography=None, write=True):
    """处理整本书的样式表，返回 ``(stats, samples, override_block)``。

    ``write=False`` 时只做「试用」，不落盘，用于预览。
    """
    book_root = Path(book_root)
    typography = typography or TypographyOptions()
    stats = CssStats()

    stylesheets = all_stylesheets(book_root)
    stats.stylesheets = len(stylesheets)

    samples = []
    for relative in stylesheets:
        path = book_root / relative
        source = decode_text(path.read_bytes())
        text = source

        if clean:
            text, counters, removed = clean_stylesheet(text)
            stats.empty_rules_removed += counters["empty_rules_removed"]
            stats.duplicate_font_faces_removed += counters["duplicate_font_faces_removed"]
            stats.duplicate_rules_removed += counters["duplicate_rules_removed"]
            stats.repeated_family_kept += counters["repeated_family_kept"]
            for sample in removed:
                if len(samples) < 8:
                    samples.append(f"{relative}: {sample}")

        if text != source:
            stats.changed_stylesheets += 1
            stats.changed_files.append(relative)
            if write:
                write_text(path, _fix_charset(text))

    override_block = ""
    if typography.enabled:
        target = resolve_override_target(book_root)
        stats.override_target = target
        path = book_root / target
        source = decode_text(path.read_bytes())
        text, changed = apply_override(source, typography)
        override_block = build_override_block(typography)
        if changed:
            # 目标文件若已统计过改动，不要重复计数
            if target not in stats.changed_files:
                stats.changed_stylesheets += 1
                stats.changed_files.append(target)
            if write:
                write_text(path, _fix_charset(text))
        stats.override_applied = bool(override_block)

    return stats, samples, override_block


# --------------------------------------------------------------------------- #
# 预览
# --------------------------------------------------------------------------- #
_PREVIEW_STYLE = """
body{margin:0;padding:20px;background:#fafbfc;color:#20242a;
  font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","Microsoft YaHei",sans-serif;
  font-size:15px;line-height:1.7}
h1{font-size:19px;margin:0 0 8px}
.summary{color:#68717d;font-size:13px;margin:0 0 18px}
.card{border:1px solid #e1e5ea;border-radius:8px;background:#fff;padding:16px;margin-bottom:14px}
.card h2{font-size:13px;margin:0 0 10px;color:#68717d;font-weight:600}
pre{margin:0;padding:12px;background:#fafbfc;border:1px solid #e1e5ea;border-radius:6px;
  font-family:Consolas,monospace;font-size:12px;line-height:1.6;overflow:auto;white-space:pre-wrap}
.mock{border:1px dashed #ccd2d9;border-radius:6px;padding:14px 18px}
.mock p{margin-top:0}
.empty{color:#77818c;font-size:13px}
"""


def render_preview(stats, samples, override_block, typography=None, text_stats=None):
    """把处理结果渲染成可直接塞进 iframe 的预览文档。

    ``text_stats`` 是 :func:`modules.epub_text.clean_book` 的统计结果，用于展示
    「清除空段」那一节；为 ``None`` 表示本次没有启用该能力。这里只读取它的
    ``documents`` / ``changed_documents`` / ``empty_paragraphs_removed`` /
    ``changed_files`` 四个属性，不依赖该类型本身。
    """
    blocks = [f'<p class="summary">{stats_text(stats)}</p>']

    if typography is not None and typography.enabled:
        style = []
        if typography.font_size:
            style.append(f"font-size:{typography.font_size}")
        if typography.line_height:
            style.append(f"line-height:{typography.line_height}")
        paragraph_style = []
        if typography.text_indent:
            paragraph_style.append(f"text-indent:{typography.text_indent}")
        if typography.paragraph_spacing:
            paragraph_style.append(f"margin-bottom:{typography.paragraph_spacing}")
        mock = (
            f'<div class="mock" style="{";".join(style)}">'
            f'<p style="{";".join(paragraph_style)}">第一段：用来示意行距、段首缩进与段间距的实际观感。'
            "这一段足够长，可以看到首行缩进与行与行之间的疏密。</p>"
            f'<p style="{";".join(paragraph_style)}">第二段：段间距体现在两段之间，缩进只作用于每段首行。</p>'
            "</div>"
        )
        blocks.append(
            '<section class="card"><h2>排版效果示意（仅示意参数，不含原书字体）</h2>'
            f"{mock}</section>"
        )

    if override_block:
        blocks.append(
            '<section class="card"><h2>将追加到 '
            f"{escape(stats.override_target)} 的覆盖规则</h2>"
            f"<pre>{escape(override_block)}</pre></section>"
        )

    if text_stats is not None:
        if text_stats.empty_paragraphs_removed:
            files = list(text_stats.changed_files[:8])
            more = "" if len(text_stats.changed_files) <= 8 else "及以上"
            blocks.append(
                '<section class="card"><h2>空段清除</h2>'
                f'<p class="empty">扫描 {text_stats.documents} 个正文文档，其中 '
                f"{text_stats.changed_documents} 个有改动，共删除空段 "
                f"{text_stats.empty_paragraphs_removed} 个{more}。</p>"
                f"<pre>{escape(chr(10).join(files))}</pre></section>"
            )
        else:
            blocks.append(
                '<section class="card"><h2>空段清除</h2>'
                f'<p class="empty">扫描 {text_stats.documents} 个正文文档，没有发现空段。</p>'
                "</section>"
            )

    if samples:
        blocks.append(
            '<section class="card"><h2>被清理的规则（最多列出 8 条）</h2>'
            f"<pre>{escape(chr(10).join(samples))}</pre></section>"
        )
    else:
        blocks.append(
            '<section class="card"><h2>被清理的规则</h2>'
            '<p class="empty">没有发现可安全清理的内容（空规则 / 重复 @font-face / 重复声明块）。</p>'
            "</section>"
        )

    return (
        '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">'
        f"<title>样式表处理预览</title><style>{_PREVIEW_STYLE}</style></head><body>"
        "<h1>样式表处理预览</h1>" + "".join(blocks) + "</body></html>"
    )
