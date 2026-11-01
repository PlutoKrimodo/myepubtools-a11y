# -*- coding: utf-8 -*-
"""EPUB 格式优化工具。

* **删除段间空行**——就地改写样式表 / 正文内联 ``<style>`` 里作用于 ``<p>``
  的规则：上下 margin、padding 清零，作者没写过的补上显式 0（压掉阅读器默认
  留白），段首缩进按参数统一。语义对齐 Calibre 的「删除段间空行」。

  为什么不是「往 CSS 末尾追加覆盖规则」：类选择器 ``.block { margin: .5em 0 1em }``
  的优先级高于 ``body p``，追加的规则抢不过原书声明，段间空行就删不干净——
  必须从源头改写。

* **追加紧凑规则**——保留原书样式，只在层叠最末的样式表追加 ``p`` 兜底规则；
  若整本没有 CSS，则自动新建 ``epub-compact.css`` 并链接到正文、写入 OPF。
* **替换为模板样式**——用模板样式表覆盖并给裸 ``<p>`` / ``<h1>`` 注入正文样式类，
  适合无样式或样式混乱的书；模板缺省使用项目根目录的 ``sample_test.epub``。

两种模式**都会**执行段落间距压平。此外可选删除 spine 正文空段（复用
``clean_book``，与「文本清洗」「样式排版」判定一致）以及删除 HTML 中的 ``<br>``。

页面由 :mod:`modules.toolkit` 的通用框架渲染；处理逻辑在公共层
:mod:`modules.epub_optimize`，归档读写复用 :mod:`modules.epub_io`。
"""
from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from flask import Blueprint, request, url_for

from modules.epub_io import (
    EPUB_MIMETYPE,
    INVALID_EPUB_MESSAGE,
    EpubIOError,
    extract_epub,
    is_epub_bytes,
    write_epub,
)
from modules.epub_optimize import (
    MODE_APPEND,
    MODE_REPLACE,
    MODE_VALUES,
    extract_template_css_map,
    optimize_book,
    render_preview_html,
)
from modules.toolkit import (
    Field,
    Mode,
    PageConfig,
    ToolkitError,
    batch_download_response,
    check_zip_bomb,
    install_error_handlers,
    ok,
    read_uploads,
    render_tool_page,
    safe_filename,
)

optimize_bp = Blueprint("optimize", __name__)

TOOL = {
    "key": "optimize",
    "title": "EPUB格式优化",
    "url_prefix": "/optimize",
    "order": 65,
    "description": "就地改写作用于 <p> 的样式规则以删除段间空行（追加 / 替换两种模式都有效），可选清理空段与 <br>。",
}

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_TEMPLATE = BASE_DIR / "sample_test.epub"

PAGE = PageConfig(
    key="optimize",
    title="EPUB格式优化",
    subtitle="就地改写段落样式以删除段间空行，追加紧凑化规则或整套替换为模板样式，可顺带清理正文空段与 <br> 换行标签。",
    endpoint="/optimize/api/run",
    preview_endpoint="/optimize/api/preview",
    preview_label="预览效果",
    upload_label="EPUB 文件",
    upload_accept=".epub,application/epub+zip",
    upload_hint="选择 EPUB 后可先预览将要改动的统计，确认后再导出。支持一次选择多个文件批量处理（单次上传总量 50MB）。",
    upload_multiple=True,
    upload_order_hint="多个文件将逐个应用同一套处理项，每个单独输出，结果打包为 ZIP 下载：",
    modes=(
        Mode(
            value=MODE_APPEND,
            label="追加紧凑规则",
            upload_label="EPUB 文件",
            accept=".epub,application/epub+zip",
            hint="保留原书样式，只压平段落间距：就地改写作用于 <p> 的规则，并在层叠最末的样式表追加 p 兜底规则。已压平的内容重复运行不会叠加。若 EPUB 没有任何 CSS，会自动新建 epub-compact.css 并挂到正文与 OPF。可多选批量处理（单次上传总量 50MB），预览只试跑第一个文件。",
            submit_label="优化并下载",
        ),
        Mode(
            value=MODE_REPLACE,
            label="替换为模板样式",
            upload_label="EPUB 文件",
            accept=".epub,application/epub+zip",
            hint="用模板样式表整套替换，并给无 class 的段落 / 一级标题注入正文样式类；适合无样式或样式混乱的 EPUB。段间空行同样靠就地改写压平，原书残留的段落规则也会被一并清零。可多选批量处理（单次上传总量 50MB），预览只试跑第一个文件。",
            submit_label="优化并下载",
        ),
    ),
    fields_title="处理项",
    fields_hint=(
        "「删除段间空行」会**就地改写**样式表里作用于 <p> 的规则：上下 margin / padding 清零、"
        "缺少的显式 0 补齐、作者没写过的段首缩进按需补上，因此追加模式和替换模式都有效。"
        "只在 CSS 末尾追加覆盖规则是压不住段间空行的——类选择器（.block）优先级高于标签选择器（body p）。"
        "标题 / 列表 / 图片 / 分节横线以及 <body> 一律不动；与标题共用选择器的规则会保守跳过并在预览里提示。"
        "「删除空段」按 spine 正文处理，判定与「文本清洗」一致；"
        "「删除 <br>」会移除正文与封面等 HTML 里的全部换行标签，可能影响诗节或对话排版，请先预览。"
    ),
    fields=(
        Field(
            "remove_paragraph_spacing",
            "删除段间空行（改写段落样式）",
            type="checkbox",
            default=True,
            hint="清零段落上下 margin / padding，并补上阅读器默认值的空缺；语义等同 Calibre 的「删除段间空行」",
        ),
        Field(
            "paragraph_indent",
            "段首缩进",
            type="text",
            default="1.5em",
            placeholder="1.5em",
            hint="去掉段间空行后靠缩进区分段落；留空表示完全不改原书缩进",
        ),
        Field(
            "paragraph_include_div",
            "把 <div> 也当段落",
            type="checkbox",
            default=False,
            hint="正文用 <div> 排段的书需要勾选。注意页面容器多为 div，勾选后它们的上下留白同样会被清零",
        ),
        Field(
            "template_file",
            "模板 EPUB（替换模式）",
            type="file",
            hint="仅替换模式使用；留空则用项目默认模板 sample_test.epub。追加模式忽略本项。",
        ),
        Field(
            "remove_empty_paragraphs",
            "删除空段",
            type="checkbox",
            default=True,
            hint="按 spine 删除正文里没有文字、也没有插图 / 表格的 <p>；判定与「文本清洗」「样式排版」一致",
        ),
        Field(
            "remove_br",
            "删除 <br> 换行标签",
            type="checkbox",
            default=True,
            hint="移除 HTML 中的 <br>；若正文依赖硬换行（诗节、对话），请取消勾选",
        ),
        Field(
            "same_filename",
            "输出与源文件同名",
            type="checkbox",
            default=False,
            hint="勾选后下载文件名不带 _optimized 后缀，便于覆盖本地同名文件",
        ),
    ),
    result_kind="download",
    result_hint="导出后会自动下载优化后的 EPUB（多选时打包为 ZIP 一并下载），同时在上方给出下载链接。",
)

install_error_handlers(optimize_bp, extra_exceptions=(EpubIOError,))


def _validate_upload(upload):
    """校验单个 EPUB 上传；有问题返回原因文本，没问题返回 ``None``。

    批量模式据此**逐本隔离失败**：一本坏书不拖累其它书（返回原因记入
    失败清单），而单文件模式由调用方把原因直接抛成错误。
    """
    if not is_epub_bytes(upload.data):
        return INVALID_EPUB_MESSAGE
    try:
        check_zip_bomb(upload.data)
    except ToolkitError as exc:
        return str(exc)
    return None


def _bool_field(name):
    return (request.form.get(name) or "").lower() in {"1", "true", "yes", "on"}


def _mode_from_form():
    mode = (request.form.get("mode") or MODE_APPEND).strip()
    if mode not in MODE_VALUES:
        raise ToolkitError("未知的处理模式，请刷新页面后重试")
    return mode


def _template_css_map(mode):
    """替换模式：读取上传模板或回落到默认 sample_test.epub；追加模式返回 None。"""
    if mode != MODE_REPLACE:
        return None

    template = request.files.get("template_file")
    if template is not None and template.filename:
        from werkzeug.utils import secure_filename

        suffix = Path(template.filename).suffix.lower()
        if suffix != ".epub":
            raise ToolkitError("模板文件类型不支持（仅支持 .epub）")
        data = template.read()
        if not data:
            raise ToolkitError("上传的模板文件为空")
        if not is_epub_bytes(data):
            raise ToolkitError("模板不是有效的 EPUB/ZIP")
        check_zip_bomb(data)
        return extract_template_css_map(data)

    if not DEFAULT_TEMPLATE.exists():
        raise ToolkitError(
            f"替换模式需要模板 EPUB：请上传模板，或确认默认模板存在（{DEFAULT_TEMPLATE.name}）"
        )
    return extract_template_css_map(DEFAULT_TEMPLATE.read_bytes())


def _options_from_form():
    return {
        "remove_empty_paragraphs": _bool_field("remove_empty_paragraphs"),
        "remove_br": _bool_field("remove_br"),
        "same_filename": _bool_field("same_filename"),
        "remove_paragraph_spacing": _bool_field("remove_paragraph_spacing"),
        "paragraph_indent": (request.form.get("paragraph_indent") or "").strip(),
        "paragraph_include_div": _bool_field("paragraph_include_div"),
    }


def _run_kwargs(options):
    """把页面选项换算成 :func:`optimize_book` 的关键字参数。"""
    return {
        "remove_empty_paragraphs": options["remove_empty_paragraphs"],
        "remove_br": options["remove_br"],
        "remove_paragraph_spacing": options["remove_paragraph_spacing"],
        "paragraph_indent": options["paragraph_indent"],
        "paragraph_include_div": options["paragraph_include_div"],
    }


@optimize_bp.route("/")
def index():
    return render_tool_page(
        replace(
            PAGE,
            endpoint=url_for("optimize.api_run"),
            preview_endpoint=url_for("optimize.api_preview"),
        )
    )


@optimize_bp.post("/api/preview")
def api_preview():
    """试跑，返回预览 HTML 与统计，不写盘。多选时只试跑第一个文件。"""
    uploads = read_uploads("file", {".epub"}, label="EPUB 文件")
    upload = uploads[0]
    reason = _validate_upload(upload)
    if reason:
        raise ToolkitError(reason)
    mode = _mode_from_form()
    options = _options_from_form()
    template_css_map = _template_css_map(mode)

    with TemporaryDirectory() as temp_dir:
        book = extract_epub(upload.data, Path(temp_dir) / "book")
        stats = optimize_book(
            book,
            mode=mode,
            template_css_map=template_css_map,
            write=False,
            **_run_kwargs(options),
        )

    summary = stats.summary()
    if len(uploads) > 1:
        summary = f"已选 {len(uploads)} 个文件，预览只试跑第 1 个「{upload.filename}」。{summary}"
    return ok(
        html=render_preview_html(stats),
        summary=summary,
        stats=stats.as_dict(),
    )


def _output_epub_name(upload, options):
    if options["same_filename"]:
        return safe_filename(f"{upload.stem}.epub", "book.epub")
    return safe_filename(f"{upload.stem}_optimized.epub", "book.epub")


def _process_upload(upload, mode, options, template_css_map):
    """处理单本：解包 → 优化 → 重新打包，返回 ``(产物, 摘要, 下载名)``。"""
    with TemporaryDirectory() as temp_dir:
        book = extract_epub(upload.data, Path(temp_dir) / "book")
        stats = optimize_book(
            book,
            mode=mode,
            template_css_map=template_css_map,
            write=True,
            **_run_kwargs(options),
        )
        payload = write_epub(book)
    return payload, stats.summary(), _output_epub_name(upload, options)


@optimize_bp.post("/api/run")
def api_run():
    """真正处理并导出：单本直接下载 EPUB；多本批量处理，结果打包为 ZIP。"""
    uploads = read_uploads("file", {".epub"}, label="EPUB 文件")
    mode = _mode_from_form()
    options = _options_from_form()
    template_css_map = _template_css_map(mode)

    return batch_download_response(
        uploads,
        lambda upload: _process_upload(upload, mode, options, template_css_map),
        archive_stem="优化结果",
        mimetype=EPUB_MIMETYPE,
        validate=_validate_upload,
        expected_exceptions=(EpubIOError,),
    )
