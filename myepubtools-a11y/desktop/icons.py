# -*- coding: utf-8 -*-
"""应用图标：以「摊开的书 + 朱砂印章」呼应 Web 端的书卷气质。

图标用 QPainter 依尺寸重绘，任务栏 16px 与窗口 256px 都保持清晰，
不依赖外部图片资源。
"""
from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import (
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
)

from desktop.theme import INK_FAINT, NIGHT, PAPER, RULE_STRONG, SEAL, SHEET

_ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)


def _render(size: int) -> QPixmap:
    pixmap = QPixmap(size, size)
    pixmap.fill(Qt.GlobalColor.transparent)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)

    scale = size / 256.0

    # 深色圆角底：与侧边栏同色，任务栏上辨识度高
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(NIGHT))
    painter.drawRoundedRect(
        QRectF(size * 0.05, size * 0.05, size * 0.90, size * 0.90),
        size * 0.20,
        size * 0.20,
    )

    # 摊开的书：左右两页书页
    page_pen = QPen(QColor(RULE_STRONG), max(1.0, size * 0.010))
    painter.setPen(page_pen)
    painter.setBrush(QColor(PAPER))

    left_page = QPainterPath()
    left_page.moveTo(QPointF(size * 0.50, size * 0.30))
    left_page.cubicTo(
        size * 0.38, size * 0.26, size * 0.24, size * 0.27, size * 0.16, size * 0.30
    )
    left_page.lineTo(QPointF(size * 0.16, size * 0.68))
    left_page.cubicTo(
        size * 0.26, size * 0.65, size * 0.38, size * 0.66, size * 0.50, size * 0.72
    )
    left_page.closeSubpath()
    painter.drawPath(left_page)

    right_page = QPainterPath()
    right_page.moveTo(QPointF(size * 0.50, size * 0.30))
    right_page.cubicTo(
        size * 0.62, size * 0.26, size * 0.76, size * 0.27, size * 0.84, size * 0.30
    )
    right_page.lineTo(QPointF(size * 0.84, size * 0.68))
    right_page.cubicTo(
        size * 0.74, size * 0.65, size * 0.62, size * 0.66, size * 0.50, size * 0.72
    )
    right_page.closeSubpath()
    painter.drawPath(right_page)

    # 书页上的文字线（16px 时简化，避免糊成一团）
    if size >= 32:
        painter.setPen(QPen(QColor(INK_FAINT), max(1.0, size * 0.016)))
        for index in range(3):
            y = size * (0.40 + index * 0.085)
            painter.drawLine(QPointF(size * 0.22, y), QPointF(size * 0.42, y))
            painter.drawLine(QPointF(size * 0.58, y), QPointF(size * 0.78, y))

    # 朱砂印章
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor(SEAL))
    seal_rect = QRectF(size * 0.655, size * 0.665, size * 0.205, size * 0.205)
    painter.drawRoundedRect(seal_rect, size * 0.032, size * 0.032)

    # 印面「书」字：小尺寸下退化为一点，避免糊字
    if size >= 48:
        painter.setPen(QColor(SHEET))
        font = QFont("SimSun")
        font.setPixelSize(int(size * 0.135))
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(seal_rect, int(Qt.AlignmentFlag.AlignCenter), "书")
    else:
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(SHEET))
        painter.drawEllipse(
            QPointF(seal_rect.center().x(), seal_rect.center().y()),
            size * 0.035,
            size * 0.035,
        )

    painter.end()
    return pixmap


def make_app_icon() -> QIcon:
    """构建多尺寸应用图标，供窗口与任务栏使用。"""
    icon = QIcon()
    for size in _ICON_SIZES:
        icon.addPixmap(_render(size))
    return icon
