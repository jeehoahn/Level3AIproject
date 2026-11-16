"""PySide6 기본 위젯만 사용한 스크롤 가능한 가로 막대그래프.

QtCharts/matplotlib 등 별도 그래프 패키지 설치가 필요 없습니다.
"""
from __future__ import annotations
import math

from PySide6.QtCore import Qt, QRectF, QSize, Signal
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QWidget, QToolTip


PALETTE = ('#2563eb', '#0d9488', '#d97706', '#7c3aed', '#db2777', '#4f46e5', '#0891b2')


def format_chart_value(value: float, unit: str, precision: int = 3) -> str:
    amount = f'{value:,.{precision}f}' if precision else f'{value:,.0f}'
    return f'{amount} {unit}'.strip()


class HorizontalBarChart(QWidget):
    """길어지는 파일 목록은 QScrollArea가 처리. 0 기준선으로 음수도 표현."""
    recordSelected = Signal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows: list[dict] = []
        self.unit = '%'
        self.precision = 3
        self.metric_name = '용량 절감률'
        self._hit_regions: list[tuple[QRectF, dict]] = []
        self.setMouseTracking(True)
        self.setMinimumWidth(680)
        self.setMinimumHeight(260)
        self._algorithm_colors: dict[str, QColor] = {}

    def set_data(self, rows: list[dict], metric_name: str, unit: str, precision: int):
        self.rows = rows
        self.metric_name = metric_name
        self.unit = unit
        self.precision = precision
        for name in sorted({str(row['algorithm']) for row in rows}):
            if name not in self._algorithm_colors:
                self._algorithm_colors[name] = QColor(
                    PALETTE[len(self._algorithm_colors) % len(PALETTE)])
        self.setMinimumHeight(max(260, 80 + len(rows) * 43))
        self._hit_regions = []
        self.updateGeometry()
        self.update()

    def sizeHint(self):
        return QSize(1000, max(260, 80 + len(self.rows) * 43))

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), self.palette().base())
        self._hit_regions = []
        if not self.rows:
            painter.setPen(QColor('#64748b'))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter,
                             '표시할 수치 데이터가 없습니다.\n파일 실험을 실행하거나 지표를 변경하세요.')
            return

        painter.setFont(QFont(self.font().family(), 10))
        fm = QFontMetrics(painter.font())
        width = self.width()
        # 좌측에는 파일+알고리즘, 우측에는 숫자값. 막대는 중앙 영역에 그림.
        label_width = min(330, max(185, int(width * .28)))
        plot_left = label_width + 22
        plot_right = max(plot_left + 160, width - 170)
        plot_width = plot_right - plot_left
        top, row_height = 47, 43
        values = [row['value'] for row in self.rows]
        minimum, maximum = min(0.0, *values), max(0.0, *values)
        if minimum == maximum:
            maximum = minimum + 1
        span = maximum - minimum
        # 양쪽 여유 공간 확보. 모든 값이 음수여도 0의 위치가 유지됨.
        pad = max(span * .07, 1e-10)
        minimum -= pad
        maximum += pad
        span = maximum - minimum
        x = lambda v: plot_left + (v - minimum) / span * plot_width
        zero_x = x(0)

        painter.setPen(QColor('#64748b'))
        painter.drawText(QRectF(plot_left, 6, plot_width, 24),
                         Qt.AlignmentFlag.AlignCenter, self.metric_name)
        for i in range(len(self.rows)):
            if i % 2 == 0:
                painter.fillRect(QRectF(5, top + i * row_height-2,
                                        width-10, row_height), QColor('#f8fafc'))
        # 눈금과 기준선을 행에 걸쳐 그린다.
        for i in range(5):
            tick_value = minimum + span * (i / 4)
            tick_x = x(tick_value)
            painter.setPen(QPen(QColor('#e2e8f0'), 1))
            painter.drawLine(int(tick_x), 38, int(tick_x),
                             top + len(self.rows) * row_height - 5)
            painter.setPen(QColor('#64748b'))
            painter.drawText(QRectF(tick_x - 55, 23, 110, 16),
                             Qt.AlignmentFlag.AlignCenter, f'{tick_value:.3g}')
        painter.setPen(QPen(QColor('#475569'), 1.5))
        painter.drawLine(int(zero_x), 38, int(zero_x),
                         top + len(self.rows) * row_height - 5)

        for i, row in enumerate(self.rows):
            y = top + i * row_height
            label = f"{row['file']}  |  {row['algorithm']}"
            elided = fm.elidedText(label, Qt.TextElideMode.ElideMiddle, label_width - 15)
            painter.setPen(QColor('#0f172a'))
            painter.drawText(QRectF(12, y + 3, label_width-12, 24),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, elided)
            value_x = x(row['value'])
            rect = QRectF(min(zero_x, value_x), y + 4,
                          max(1.2, abs(value_x - zero_x)), 23)
            color = self._algorithm_colors.get(row['algorithm'], QColor(PALETTE[0]))
            painter.setBrush(color)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRoundedRect(rect, 3, 3)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.setPen(QColor('#0f172a'))
            painter.drawText(QRectF(plot_right + 14, y+3, width - plot_right - 20, 24),
                             Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                             format_chart_value(row['value'], self.unit, self.precision))
            self._hit_regions.append((QRectF(2, y, width-4, 31), row))

        # 아래쪽 범례: 압축 방식별 막대 색상
        legend_x = 12
        for name in sorted({r['algorithm'] for r in self.rows}):
            color = self._algorithm_colors[name]
            w = fm.horizontalAdvance(name) + 28
            if legend_x + w >= width:
                break
            painter.fillRect(QRectF(legend_x, 9, 11, 11), color)
            painter.setPen(QColor('#475569'))
            painter.drawText(QRectF(legend_x+16, 4, w-16, 22),
                             Qt.AlignmentFlag.AlignVCenter, name)
            legend_x += w + 12

    def mouseMoveEvent(self, event):
        pos = event.position()
        for rect, row in self._hit_regions:
            if rect.contains(pos):
                status = ('통과' if row['verified'] is True else
                          '실패' if row['verified'] is False else '미검사')
                QToolTip.showText(event.globalPosition().toPoint(),
                                  f"{row['source']}\n{row['algorithm']} · 실행 #{row['run']}\n"
                                  f"{self.metric_name}: {format_chart_value(row['value'], self.unit, self.precision)}\n"
                                  f"복원 검증: {status}", self)
                return
        QToolTip.hideText()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            for rect, row in self._hit_regions:
                if rect.contains(event.position()):
                    self.recordSelected.emit(row['record_index'])
                    return
        super().mousePressEvent(event)

    def leaveEvent(self, event):
        QToolTip.hideText()
        super().leaveEvent(event)
