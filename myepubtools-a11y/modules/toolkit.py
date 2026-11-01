# -*- coding: utf-8 -*-
"""通用工具页面框架：把「上传 → 配置 → 处理 → 下载」收敛成一套约定。

新工具只需要三步就能拥有统一风格的页面：

1. 在自己的模块里声明一份 :class:`PageConfig`（标题、模式、字段、结果类型）；
2. 在 ``GET /`` 视图里 ``return render_tool_page(page)``；
3. 处理接口里用 :func:`ok` / :func:`fail` 返回，或直接抛 :class:`ToolkitError`
   由 :func:`install_error_handlers` 统一转成中文 JSON。

如果工具需要「读取现有值并预填表单」（如元数据编辑），额外设 ``load_endpoint``：
前端会在文件选中后自动 POST 到该接口，并把返回的 ``values`` 填进对应控件。

本模块**没有** ``TOOL`` 字典，属于「公共模块」，``registry.discover()`` 会
自动跳过它，因此不会被当成工具挂载——与 ``epub_io`` 同性质。
"""
from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import quote

from flask import current_app, jsonify, render_template, request, send_file
from werkzeug.exceptions import HTTPException

DEFAULT_SUBMIT_LABEL = "开始处理"
DEFAULT_LOAD_LABEL = "读取现有值"
DEFAULT_PREVIEW_LABEL = "预览效果"
FIELDS_TITLE = "处理设置"
RESULT_TITLE = "处理结果"
UPLOAD_LABEL = "文件"

#: 默认上传提示语。
DEFAULT_UPLOAD_HINT = "最大 50MB。文件只在本地服务器处理，不会上传到第三方服务。"

FIELD_TYPES = ("text", "textarea", "number", "select", "checkbox", "file", "filelist")
RESULT_KINDS = ("download", "html", "none")

#: 下载类响应用于附带一条可读提示的自定义响应头（前端会显示在状态条）。
MESSAGE_HEADER = "X-Toolkit-Message"

#: 解压后体积上限，用于拦截「压缩炸弹」（上传很小、展开极大）。
MAX_UNCOMPRESSED_SIZE = 512 * 1024 * 1024
TOO_LARGE_MESSAGE = "文件超过 50MB 限制"
INVALID_ARCHIVE_MESSAGE = "上传的文件不是有效的 ZIP/EPUB 压缩包"
UNEXPECTED_MESSAGE = "处理失败，请确认文件是否完整后重试"

_UNSAFE_FILENAME_RE = re.compile(r'[\\/:*?"<>|\r\n\t]+')
#: 页头印章取字用：标题里的第一个汉字。
_HAN_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}


class ToolkitError(Exception):
    """工具层可预期的失败，消息会原样返回给用户。"""


# --------------------------------------------------------------------------- #
# 页面声明
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Option:
    """``select`` 字段的一个选项。"""

    value: str
    label: str

    def as_dict(self):
        return {"value": self.value, "label": self.label}


@dataclass(frozen=True)
class Field:
    """配置区的一个表单字段。"""

    name: str
    label: str
    type: str = "text"
    default: object = ""
    placeholder: str = ""
    hint: str = ""
    options: tuple = ()
    required: bool = False
    rows: int = 6

    def __post_init__(self):
        if self.type not in FIELD_TYPES:
            raise ToolkitError(f"未知字段类型：{self.type}（可用：{'、'.join(FIELD_TYPES)}）")
        if self.type == "select" and not self.options:
            raise ToolkitError(f"select 字段 {self.name} 需要至少一个选项")

    def as_dict(self):
        return {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "default": self.default,
            "placeholder": self.placeholder,
            "hint": self.hint,
            "options": [
                option.as_dict() if isinstance(option, Option) else {"value": option, "label": option}
                for option in self.options
            ],
            "required": self.required,
            "rows": self.rows,
        }


@dataclass(frozen=True)
class Mode:
    """一种处理模式，切换时联动上传控件与提示文案。"""

    value: str
    label: str
    upload_label: str = UPLOAD_LABEL
    accept: str = ""
    hint: str = ""
    submit_label: str = DEFAULT_SUBMIT_LABEL
    #: 是否允许多选。None 表示沿用页面级 ``upload_multiple``。
    multiple: object = None

    def as_dict(self):
        return {
            "value": self.value,
            "label": self.label,
            "upload_label": self.upload_label,
            "accept": self.accept,
            "hint": self.hint,
            "submit_label": self.submit_label,
            "multiple": self.multiple,
        }


def seal_mark(title):
    """页头那枚「印章」里的字：取标题中第一个汉字，全是拉丁字母时回落到「箱」。"""
    for char in str(title or ""):
        if _HAN_RE.match(char):
            return char
    return "箱"


@dataclass(frozen=True)
class PageConfig:
    """一个工具页面的完整声明。"""

    key: str
    title: str
    endpoint: str
    subtitle: str = ""
    #: 「读取现有值」接口。留空则该页面不出现读取按钮，也不会自动预填。
    load_endpoint: str = ""
    load_label: str = DEFAULT_LOAD_LABEL
    #: 选中文件后是否自动调用 ``load_endpoint`` 预填表单。
    autoload: bool = True
    #: 「预览效果」接口。留空则该页面不出现预览按钮。
    preview_endpoint: str = ""
    preview_label: str = DEFAULT_PREVIEW_LABEL
    modes: tuple = ()
    fields: tuple = ()
    upload_label: str = UPLOAD_LABEL
    upload_accept: str = ""
    upload_hint: str = DEFAULT_UPLOAD_HINT
    #: 是否允许一次选多个文件；选多个时页面会列出顺序并支持上移 / 下移。
    upload_multiple: bool = False
    #: 多选时可选的辅助说明（例如「顺序即合并顺序」）。
    upload_order_hint: str = ""
    fields_title: str = FIELDS_TITLE
    fields_hint: str = ""
    result_title: str = RESULT_TITLE
    result_kind: str = "download"
    result_hint: str = ""
    submit_label: str = DEFAULT_SUBMIT_LABEL

    def __post_init__(self):
        if not self.key or not self.title or not self.endpoint:
            raise ToolkitError("PageConfig 的 key、title、endpoint 均为必填")
        if self.result_kind not in RESULT_KINDS:
            raise ToolkitError(f"未知结果类型：{self.result_kind}（可用：{'、'.join(RESULT_KINDS)}）")

    def as_dict(self):
        return {
            "key": self.key,
            "title": self.title,
            "seal": seal_mark(self.title),
            "subtitle": self.subtitle,
            "endpoint": self.endpoint,
            "load_endpoint": self.load_endpoint,
            "load_label": self.load_label,
            "autoload": self.autoload,
            "preview_endpoint": self.preview_endpoint,
            "preview_label": self.preview_label,
            "modes": [mode.as_dict() for mode in self.modes],
            "fields": [field.as_dict() for field in self.fields],
            "upload": {
                "label": self.upload_label,
                "accept": self.upload_accept,
                "hint": self.upload_hint,
                "multiple": self.upload_multiple,
                "order_hint": self.upload_order_hint,
            },
            "fields_title": self.fields_title,
            "fields_hint": self.fields_hint,
            "result_title": self.result_title,
            "result_kind": self.result_kind,
            "result_hint": self.result_hint,
            "submit_label": self.submit_label,
        }


def render_tool_page(page):
    """渲染共享的页面壳，前端再按配置生成具体控件。"""
    return render_template("_toolkit/page.html", page=page.as_dict())


# --------------------------------------------------------------------------- #
# 统一响应
# --------------------------------------------------------------------------- #
def ok(**payload):
    """成功响应：``{"ok": true, ...}``。"""
    return jsonify({"ok": True, **payload})


def fail(message, status=400):
    """失败响应：``{"ok": false, "error": "..."}``。"""
    return jsonify({"ok": False, "error": str(message)}), status


def attach_message(response, text):
    """给文件下载响应附带一条可读提示，前端会显示在状态条。

    自定义响应头只能是 ASCII，所以这里按 UTF-8 百分号编码，前端负责解码。
    """
    if response is not None and text:
        response.headers[MESSAGE_HEADER] = quote(str(text), safe="")
    return response


def install_error_handlers(blueprint, extra_exceptions=()):
    """给工具 Blueprint 装上统一的中文 JSON 错误响应。

    ``extra_exceptions`` 用于登记工具特有的异常类型（如 ``EpubIOError``），
    它们的消息会原样返回；其余未预期异常只记日志、返回通用中文提示，
    不向用户暴露堆栈。
    """

    def _expected(exc):
        return fail(exc)

    blueprint.register_error_handler(ToolkitError, _expected)
    for exc_type in extra_exceptions:
        blueprint.register_error_handler(exc_type, _expected)

    @blueprint.errorhandler(413)
    def _payload_too_large(_exc):  # pragma: no cover - 由 Flask 触发
        return fail(TOO_LARGE_MESSAGE, 413)

    @blueprint.errorhandler(Exception)
    def _unexpected(exc):
        if isinstance(exc, HTTPException):
            return exc.get_response()
        current_app.logger.exception("工具 %s 处理失败", blueprint.name)
        return fail(UNEXPECTED_MESSAGE, 500)

    return blueprint


# --------------------------------------------------------------------------- #
# 上传与归档
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Upload:
    """一次上传的结果。"""

    filename: str
    stem: str
    suffix: str
    data: bytes

    @property
    def size(self):
        return len(self.data)


def _upload_from_storage(storage, allowed_suffixes, label):
    """把单个 ``FileStorage`` 收成 :class:`Upload`，顺带做类型与空文件校验。"""
    original = Path(storage.filename).name
    suffix = Path(original).suffix.lower()
    if allowed_suffixes and suffix not in allowed_suffixes:
        allowed = "、".join(sorted(allowed_suffixes))
        raise ToolkitError(f"文件类型不支持：{original}（仅支持 {allowed}）")

    data = storage.read()
    if not data:
        raise ToolkitError(f"上传的文件为空：{original}")

    return Upload(
        filename=original,
        stem=Path(original).stem or "output",
        suffix=suffix,
        data=data,
    )


def read_upload(name="file", allowed_suffixes=None, label=UPLOAD_LABEL):
    """读取并校验上传文件，失败时抛 :class:`ToolkitError`。"""
    storage = request.files.get(name)
    if storage is None or not storage.filename:
        raise ToolkitError(f"请选择{label}")
    return _upload_from_storage(storage, allowed_suffixes, label)


def read_uploads(name="file", allowed_suffixes=None, label=UPLOAD_LABEL):
    """读取一次上传的全部文件（多选批量），逐个校验，保持选择顺序。

    单文件页面用 :func:`read_upload`；批量页面用本函数，得到的
    :class:`Upload` 列表与前端文件列表的顺序一致。
    """
    storages = [
        storage for storage in request.files.getlist(name)
        if storage is not None and storage.filename
    ]
    if not storages:
        raise ToolkitError(f"请选择{label}")
    return [
        _upload_from_storage(storage, allowed_suffixes, label)
        for storage in storages
    ]


def check_zip_bomb(data, limit=MAX_UNCOMPRESSED_SIZE):
    """累加 ZIP 成员声明的解压体积，超过 ``limit`` 直接拒绝。

    仅凭上传体积无法判断真实开销，这里按成员声明大小先行拦截，
    避免「上传几十 KB、展开占满磁盘」的压缩炸弹。
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            total = sum(info.file_size for info in archive.infolist())
    except zipfile.BadZipFile as exc:
        raise ToolkitError(INVALID_ARCHIVE_MESSAGE) from exc

    if total > limit:
        raise ToolkitError(
            f"压缩包解压后约 {total / 1024 / 1024:.0f}MB，"
            f"超过 {limit // 1024 // 1024}MB 上限，已拒绝处理"
        )
    return total


def zip_directory(root):
    """把目录打包成 ZIP，返回已就绪的 ``BytesIO``（保留相对结构）。"""
    root = Path(root)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(root.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(root).as_posix())
    buffer.seek(0)
    return buffer


def safe_filename(name, fallback="output"):
    """清洗下载文件名：只保留文件名部分，去掉非法字符，避免响应头注入。"""
    text = str(name or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = _UNSAFE_FILENAME_RE.sub("_", text).strip(" ._")
    stem = Path(cleaned).stem if cleaned else ""
    suffix = Path(cleaned).suffix if cleaned else ""
    if not stem or stem.upper() in _WINDOWS_RESERVED:
        stem = fallback
    return f"{stem[:120]}{suffix[:16]}" or fallback


def unique_name(name, used):
    """批量打包时给 ZIP 成员名去重：与 ``used`` 冲突时在词干后加 ``-2``、``-3``。

    处理的同名书（不同目录下的同名文件）不能用同一成员名，否则后写覆盖前写。
    ``used`` 是已占用的名字集合，本函数会把最终名字登记进去。
    """
    if name not in used:
        used.add(name)
        return name
    stem = Path(name).stem
    suffix = Path(name).suffix
    index = 2
    while f"{stem}-{index}{suffix}" in used:
        index += 1
    final = f"{stem}-{index}{suffix}"
    used.add(final)
    return final


def batch_download_response(
    uploads,
    process,
    archive_stem,
    mimetype,
    validate=None,
    expected_exceptions=(),
):
    """批量上传的统一出口：单本直接下载，多本逐个处理并打包 ZIP。

    ``process(upload) -> (payload, summary, download_name)``：处理单本，
    返回产物（``BytesIO`` 或 bytes）、一句结果摘要（单本时显示在状态条）
    与下载文件名（多本时即 ZIP 成员名）。
    ``validate(upload) -> str | None``：可选前置校验，返回原因文本即计为
    失败。多本时一本书失败不拖累其它书（原因点名写进提示消息），全部
    失败才抛 :class:`ToolkitError`；单本时失败直接报错、行为与旧版一致。
    """
    if len(uploads) == 1:
        upload = uploads[0]
        if validate:
            reason = validate(upload)
            if reason:
                raise ToolkitError(reason)
        payload, summary, download_name = process(upload)
        response = send_file(
            payload,
            mimetype=mimetype,
            as_attachment=True,
            download_name=download_name,
        )
        return attach_message(response, summary)

    caught = (ToolkitError, *expected_exceptions)
    with TemporaryDirectory() as temp_dir:
        results_dir = Path(temp_dir) / "results"
        results_dir.mkdir()
        used_names = set()
        succeeded = 0
        failures = []
        for upload in uploads:
            reason = validate(upload) if validate else None
            if reason:
                failures.append((upload.filename, reason))
                continue
            try:
                payload, _summary, download_name = process(upload)
                blob = payload.getvalue() if hasattr(payload, "getvalue") else bytes(payload)
                (results_dir / unique_name(download_name, used_names)).write_bytes(blob)
                succeeded += 1
            except caught as exc:
                failures.append((upload.filename, str(exc)))

        if not succeeded:
            detail = "；".join(f"「{name}」{reason}" for name, reason in failures[:5])
            raise ToolkitError(f"批量处理失败：{detail}")

        zip_payload = zip_directory(results_dir)
        archive_name = safe_filename(f"{archive_stem}_{succeeded}本.zip", "results.zip")

    message = f"批量完成：成功 {succeeded} 本，已打包为 ZIP 下载。"
    if failures:
        shown = "；".join(f"「{name}」{reason}" for name, reason in failures[:3])
        more = "" if len(failures) <= 3 else f" 等 {len(failures)} 本"
        message = (
            f"批量完成：成功 {succeeded} 本，失败 {len(failures)} 本（{shown}{more}）；"
            "失败的书未包含在压缩包中。"
        )
    response = send_file(
        zip_payload,
        mimetype="application/zip",
        as_attachment=True,
        download_name=archive_name,
    )
    return attach_message(response, message)
