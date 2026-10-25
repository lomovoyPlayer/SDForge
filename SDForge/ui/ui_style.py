"""
SDForge: общая дизайн-система панелей.

Один модуль для консистентного вида всех панелей: константы отступов,
фабрика заголовков секций, цветные метки осей, оформление свотчей цвета
и опасных кнопок. Панели импортируют отсюда вместо разрозненных
setStyleSheet / голых QLabel.

Использование:
    from .ui_style import section_header, style_axis_label, ...

    layout.addWidget(section_header("Трансформации"))
"""

from typing import List

from PyQt5 import QtWidgets
from PyQt5.QtCore import Qt

# ----------------------------------------------------------------------
# Константы
# ----------------------------------------------------------------------

MARGIN = 8            # внешние отступы панелей (было 0..4)
SPACING = 6           # интервал между элементами формы
SECTION_SPACING = 12  # воздух перед заголовком секции
DEFAULT_DECIMALS = 3  # 4 знака — визуальный шум ("0.0500")

# Цветовое кодирование осей — конвенция 3D-редакторов (Blender, Maya)
AXIS_STYLE = (
    ("X", "#e06c75"),  # красный
    ("Y", "#98c379"),  # зелёный
    ("Z", "#61afef"),  # синий
)

# ----------------------------------------------------------------------
# Заголовки секций
# ----------------------------------------------------------------------

def section_header(text: str) -> QtWidgets.QWidget:
    """Заголовок секции: приглушённый капс + линия до конца ряда.

    Замена голым QLabel("Камера") в render_settings_panel и
    QLabel("Текстуры") в inspector_panel — даёт единый визуальный якорь.
    """
    box = QtWidgets.QWidget()
    row = QtWidgets.QHBoxLayout(box)
    row.setContentsMargins(0, SECTION_SPACING, 0, 2)
    row.setSpacing(SPACING)

    label = QtWidgets.QLabel(text.upper())
    label.setStyleSheet(
        "color: palette(window-text); font-size: 11px; font-weight: bold;"
        " letter-spacing: 1px;"
    )
    row.addWidget(label)

    line = QtWidgets.QFrame()
    line.setFrameShape(QtWidgets.QFrame.HLine)
    line.setStyleSheet("color: palette(mid);")
    row.addWidget(line, 1)
    return box

# ----------------------------------------------------------------------
# Метки осей и vec3-поля
# ----------------------------------------------------------------------

def axis_label(index: int) -> QtWidgets.QLabel:
    """Цветная метка оси (X — красная, Y — зелёная, Z — синяя)."""
    name, color = AXIS_STYLE[index % len(AXIS_STYLE)]
    label = QtWidgets.QLabel(name)
    label.setStyleSheet(f"color: {color}; font-weight: bold;")
    label.setFixedWidth(12)
    label.setAlignment(Qt.AlignCenter)
    return label


def add_axis_labels(row: QtWidgets.QHBoxLayout,
                    spins: List[QtWidgets.QDoubleSpinBox]) -> None:
    """Добавляет спинбоксы в ряд с цветной меткой перед каждым.

    Замена прямым row.addWidget(spin) в _add_vec3_field инспектора
    и _add_vec3_row настроек рендера.
    """
    for i, spin in enumerate(spins):
        if i:
            row.addSpacing(2)
        row.addWidget(axis_label(i))
        row.addWidget(spin)

# ----------------------------------------------------------------------
# Свотч цвета (замена «пятну без рамки» в инспекторе)
# ----------------------------------------------------------------------

def style_color_button(btn: QtWidgets.QPushButton, rgb) -> None:
    """Фиксированный размер + рамка: пятно цвета читается как свотч,
    а не как случайная заливка кнопки."""
    r, g, b = (int(max(0, min(1, c)) * 255)
               for c in (list(rgb) + [1, 1, 1])[:3])
    lum = 0.299 * r + 0.587 * g + 0.114 * b
    border = "#ffffff" if lum < 90 else "#666666"
    btn.setFixedSize(48, 22)
    btn.setStyleSheet(
        f"background-color: rgb({r},{g},{b});"
        f" border: 1px solid {border}; border-radius: 3px;"
    )

# ----------------------------------------------------------------------
# Опасные действия
# ----------------------------------------------------------------------

def style_danger_button(btn: QtWidgets.QPushButton) -> None:
    """«Удалить» — приглушённый красный оттенок текста, чтобы опасное
    действие отличалось от нейтральных кнопок панели."""
    btn.setStyleSheet("QPushButton { color: #e06c75; }")

# ----------------------------------------------------------------------
# Формы: единые отступы
# ----------------------------------------------------------------------

def tune_layout(layout) -> None:
    """Единые поля/интервалы для корневых layout'ов панелей."""
    layout.setContentsMargins(MARGIN, MARGIN, MARGIN, MARGIN)
    layout.setSpacing(SPACING)
