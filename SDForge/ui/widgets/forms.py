"""
SDForge: поля ввода и формы с опциональной живой синхронизацией.

Назначение
----------
Вынос однотипного кода ввода (спинбоксы, комбо, свотчи цвета, vec3) из
панелей (inspector_panel, render_settings_panel, materials_panel) в один
модуль + единый механизм сверки виджетов с данными.

Поля: TextField, FloatField, IntField, SliderField, DialField, BoolField,
ComboField, ColorField, Vec2Field, Vec3Field — плюс декларативное
построение списком FieldSpec через Form.add_specs.

Конвертация единиц и Krita
--------------------------
AngleBinding оборачивает радианную привязку: UI показывает и принимает
градусы, данные остаются в радианах (скаляры и vec3, преобразование в
обе стороны). Быстрый путь — Form.add_angle / Form.add_angle_vec3.
ColorField(krita=True) добавляет кнопки «Кисть»/«Фон», берущие текущий
цвет переднего плана и фона из активного вида Krita (вне Krita кнопки
скрыты).

Модель данных
-------------
Данные ссылочные: поле не хранит копию значения, а читает/пишет через
Binding (getter/setter + commit). Поэтому «синхронизация» — это просто
перечитать источник и сравнить с тем, что показывает виджет; виджет
обновляется только при расхождении (курсор в QLineEdit не прыгает,
спинбоксы не дёргаются под рукой).

AttrBinding принимает объект ИЛИ provider — callable без аргументов,
возвращающий актуальный объект. Provider важен для узлов сцены и
материалов: SceneController._reload_structure пересоздаёт объекты, и
ссылка, захваченная один раз, протухает:

    AttrBinding(lambda: controller.selected_node(), "name",
                commit=lambda: controller.commit_node(
                    controller.selected_node()))

Живая синхронизация (флаг sync)
-------------------------------
Часть данных меняется мимо формы: мышь во вьюпорте пишет в
params.camera_pos / camera_rot и в translation/rotation выделенной фигуры
(см. render_previewer). Form(sync=True) включает FormSync: по таймеру он
вызывает refresh() всех полей; поле, которое пользователь редактирует
прямо сейчас (фокус, нажатый регулятор, открытый попап комбобокса),
пропускается. Переключить режим позже: form.sync.set_enabled(on).

Пример — настройки камеры с живой сверкой:

    form = Form(self, sync=True, sync_interval_ms=150)
    form.add_header("Камера")
    form.add_vec3("Позиция:", AttrBinding(lambda: params, "camera_pos",
                                          commit=params.emit_changed))
    form.add_vec3("Вращение:", AttrBinding(lambda: params, "camera_rot",
                                           commit=params.emit_changed))
    form.add_dial("FOV:", AttrBinding(lambda: params, "camera_fov_deg",
                                      commit=params.emit_changed),
                  minimum=10.0, maximum=120.0, step=1.0, decimals=1,
                  suffix=" °")
    # Альтернатива/дополнение к таймеру — сверка по сигналу:
    # form.sync.attach_signal(params.changed)

Пример — декларативная форма (спеки вместо кода виджетов):

    MATERIAL_SPECS = [
        FieldSpec("slider", "Шероховатость:", "roughness",
                  minimum=0.0, maximum=1.0),
        FieldSpec("slider", "Металличность:", "metallic",
                  minimum=0.0, maximum=1.0),
        FieldSpec("dial",   "IOR:", "ior", minimum=1.0, maximum=3.0),
    ]
    form.add_specs(MATERIAL_SPECS, target=lambda: self._material,
                   commit=self._commit_material)

Перестройка формы (аналог InspectorPanel.rebuild): form.clear() удаляет
поля и секции; состояние синхронизации (вкл/выкл) сохраняется.

Замечание по DialField: скорость драга мышью у QDial зашита в Qt
(весь диапазон на ~300° поворота). Управлять ею можно параметром steps —
чем меньше позиций у регулятора, тем быстрее драг (точный ввод всегда
есть в спинбоксе рядом). Колесо/клавиши: field.knob.setPageStep(n).

Модуль зависит только от PyQt5 и ui_style — про контроллер, params и БД
он не знает; связь задаётся через Binding/commit снаружи.
"""
import math
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, List, Optional, Sequence, Tuple

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor

from ... import localization

from ..ui_style import (
    DEFAULT_DECIMALS, add_axis_labels, section_header, style_color_button,
    tune_layout,
)

__all__ = [
    "Binding", "AttrBinding", "AngleBinding",
    "FormField", "TextField", "FloatField", "IntField", "SliderField",
    "DialField", "BoolField", "ComboField", "ColorField",
    "Vec2Field", "Vec3Field",
    "FieldSpec", "FormSync", "Form",
]


def _half_step(decimals: int) -> float:
    """Порог сравнения float'ов — половина последнего отображаемого знака."""
    return 0.5 * 10 ** (-decimals)


# ----------------------------------------------------------------------
# Привязки
# ----------------------------------------------------------------------
#region
class Binding:
    """Минимальная привязка: getter + setter + необязательный commit.

    commit вызывается после каждой записи из UI — сюда панель подключает
    controller.commit_node / params.emit_changed и т.п. Для не-атрибутных
    данных (например operands[2]) getter/setter пишутся вручную.
    """

    def __init__(self, getter: Callable[[], Any],
                 setter: Optional[Callable[[Any], None]] = None,
                 commit: Optional[Callable[[], None]] = None):
        self._getter = getter
        self._setter = setter
        self._commit = commit

    def get(self) -> Any:
        """Текущее значение или None (источник недоступен — поле выключится)."""
        try:
            return self._getter()
        except Exception:
            return None

    def set(self, value: Any) -> None:
        if self._setter is None:
            return
        self._setter(value)
        if self._commit is not None:
            self._commit()

class AttrBinding(Binding):
    """Привязка к атрибуту объекта.

    target — объект ИЛИ provider (callable без аргументов -> объект).
    Provider предпочтительнее для узлов сцены и материалов: контроллер
    пересоздаёт объекты при структурных изменениях, и захваченная ссылка
    протухает, а provider каждый раз возвращает актуальный объект.

    transform — необязательная функция над прочитанным значением
    (например list — чтобы поле получало копию списка, а не живой list
    фигуры).
    """

    def __init__(self, target: Any, attr: str,
                 commit: Optional[Callable[[], None]] = None,
                 transform: Optional[Callable[[Any], Any]] = None):
        self._target = target
        self._attr = attr
        self._transform = transform
        super().__init__(self._read, self._write, commit)

    def _obj(self) -> Any:
        return self._target() if callable(self._target) else self._target

    def _read(self) -> Any:
        obj = self._obj()
        if obj is None:
            return None
        value = getattr(obj, self._attr, None)
        if value is not None and self._transform is not None:
            value = self._transform(value)
        return value

    def _write(self, value: Any) -> None:
        obj = self._obj()
        if obj is not None:
            setattr(obj, self._attr, value)

class AngleBinding(Binding):
    """Обёртка над радианной привязкой: в UI — градусы.

    Данные остаются в радианах, пользователь видит и вводит градусы;
    преобразование в обе стороны выполняется здесь (get: rad -> deg,
    set: deg -> rad). Работает и со скалярами (FloatField, SliderField,
    DialField), и со списками (Vec3Field для rotation) — списки
    конвертируются поэлементно. None (источник недоступен) проходит
    как есть — поле корректно выключается.

    Пример с «крутилкой»:

        form.add_dial("Pitch:", AngleBinding(AttrBinding(
            lambda: node, "pitch", commit=commit)),
            minimum=-180.0, maximum=180.0, step=1.0, decimals=1,
            suffix=" °")
    """

    def __init__(self, inner: Binding):
        self._inner = inner
        super().__init__(self._read_deg, self._write_deg)

    @staticmethod
    def _to_deg(value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return [math.degrees(v) for v in value]
        return math.degrees(value)

    @staticmethod
    def _to_rad(value: Any) -> Any:
        if isinstance(value, (list, tuple)):
            return [math.radians(v) for v in value]
        return math.radians(value)

    def _read_deg(self) -> Any:
        value = self._inner.get()
        return None if value is None else self._to_deg(value)

    def _write_deg(self, value: Any) -> None:
        self._inner.set(self._to_rad(value))

#endregion
# ----------------------------------------------------------------------
# Базовое поле
# ----------------------------------------------------------------------
#region
class FormField(QtWidgets.QWidget):
    """Базовое поле формы: подпись слева + виджет(ы) значения.

    Наследник реализует:
      _build(row)     — создать виджеты и добавить в ряд;
      value()         — прочитать значение из виджета;
      set_value(v)    — записать значение в виджет (без эмита changed);
      focus_widgets() — виджеты, чей фокус означает «пользователь редактирует».

    Сигнал changed(value) эмитится только на правки пользователя, не на
    программный set_value при синхронизации.

    syncable=False — поле пропускается при живой синхронизации (например,
    имя, которое пользователь дописывает дольше тика таймера).

    ВАЖНО для наследников (Qt-правило): до super().__init__() нельзя
    обращаться к self (ни setattr, ни getattr) — виджет ещё не создан.
    Конфигурацию сохраняйте в обычные атрибуты через _store_range либо
    используйте атрибуты класса (как _count у Vec3Field).
    """

    changed = pyqtSignal(object)

    def __init__(self, label: str = "", binding: Optional[Binding] = None,
                 syncable: bool = True, parent=None):
        super().__init__(parent)
        self._binding: Optional[Binding] = None
        self._loading = False
        self._data_valid = True
        self.syncable = syncable

        row = QtWidgets.QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        if label:
            row.addWidget(QtWidgets.QLabel(label))
        self._build(row)

        if binding is not None:
            self.bind(binding)

    #region --- API наследников --- #

    def _build(self, row: QtWidgets.QHBoxLayout) -> None:
        raise NotImplementedError

    def value(self) -> Any:
        raise NotImplementedError

    def set_value(self, value: Any) -> None:
        raise NotImplementedError

    def focus_widgets(self) -> List[QtWidgets.QWidget]:
        return []

    def _store_range(self, minimum, maximum, step,
                     decimals=DEFAULT_DECIMALS, suffix="", cast=float):
        """Единое сохранение конфигурации числовых полей (до super().__init__)."""
        self._lo, self._hi, self._step = cast(minimum), cast(maximum), cast(step)
        self._decimals, self._suffix = decimals, suffix

    @staticmethod
    @contextmanager
    def _silence(*widgets):
        """blockSignals на время программного обновления виджетов."""
        for w in widgets:
            w.blockSignals(True)
        try:
            yield
        finally:
            for w in widgets:
                w.blockSignals(False)

    #endregion

    #region --- Привязка и запись --- #

    def bind(self, binding: Binding) -> None:
        self._binding = binding
        self.refresh()
    
    def _emit(self, value: Any) -> None:
        if self._loading:
            return
        if self._binding is not None:
            self._binding.set(value)
        self.changed.emit(value)

    #endregion
    
    #region --- Синхронизация (данные -> виджет) --- #

    def is_editing(self) -> bool:
        """Поле сейчас редактируется — синхронизация его пропускает."""
        return any(w.hasFocus() for w in self.focus_widgets())

    def refresh(self) -> None:
        """Сверить виджет с источником; обновить только при расхождении.

        enabled управляется только при смене доступности данных (None ->
        выключено): ручной setEnabled(False) панели не затирается, пока
        данные валидны.
        """
        if self._binding is None or self.is_editing():
            return
        value = self._binding.get()
        valid = value is not None
        if valid != self._data_valid:
            self._data_valid = valid
            self.setEnabled(valid)
        if not valid:
            return
        if self._values_differ(self.value(), value):
            self._loading = True
            try:
                self.set_value(value)
            finally:
                self._loading = False

    def _values_differ(self, current: Any, new: Any) -> bool:
        return current != new

    #endregion

#endregion
# ----------------------------------------------------------------------
# Скалярные поля
# ----------------------------------------------------------------------
#region
class TextField(FormField):
    """Строка; коммит по editingFinished (Enter или потеря фокуса)."""

    def _build(self, row) -> None:
        self.edit = QtWidgets.QLineEdit()
        self.edit.editingFinished.connect(
            lambda: self._emit(self.edit.text()))
        row.addWidget(self.edit, 1)

    def value(self):
        return self.edit.text()

    def set_value(self, value) -> None:
        with self._silence(self.edit):
            self.edit.setText("" if value is None else str(value))

    def focus_widgets(self):
        return [self.edit]

class FloatField(FormField):
    """Спинбокс с диапазоном, шагом, точностью и суффиксом.
    IntField — тот же класс на QSpinBox (см. _WIDGET/_cast)."""

    _WIDGET = QtWidgets.QDoubleSpinBox
    _cast = float

    def __init__(self, label="", binding=None, *, minimum=-10000.0,
                 maximum=10000.0, step=0.05, decimals=DEFAULT_DECIMALS,
                 suffix="", **kw):
        self._store_range(minimum, maximum, step, decimals, suffix,
                          cast=self._cast)
        super().__init__(label, binding, **kw)

    def _build(self, row) -> None:
        self.spin = self._WIDGET()
        self.spin.setRange(self._lo, self._hi)
        self.spin.setSingleStep(self._step)
        if isinstance(self.spin, QtWidgets.QDoubleSpinBox):
            self.spin.setDecimals(self._decimals)
            if self._suffix:
                self.spin.setSuffix(self._suffix)
        self.spin.valueChanged.connect(lambda v: self._emit(self._cast(v)))
        row.addWidget(self.spin, 1)

    def value(self):
        return self.spin.value()

    def set_value(self, value) -> None:
        with self._silence(self.spin):
            self.spin.setValue(max(self._lo, min(self._hi, self._cast(value))))

    def focus_widgets(self):
        return [self.spin]

    def _values_differ(self, current, new) -> bool:
        return abs(current - float(new)) > _half_step(self._decimals)

class IntField(FloatField):
    """QSpinBox для целых величин (счётчики, количества)."""

    _WIDGET = QtWidgets.QSpinBox
    _cast = int

    def __init__(self, label="", binding=None, *, minimum=0, maximum=100,
                 step=1, **kw):
        super().__init__(label, binding, minimum=minimum, maximum=maximum,
                         step=step, decimals=0, **kw)

class SliderField(FormField):
    """Слайдер + спинбокс — для нормированных величин (0..1: roughness,
    metallic, transmission). Регулятор — грубое изменение мышью, спинбокс —
    точный ввод. Реализация общая с DialField: QSlider и QDial имеют
    одинаковый интерфейс QAbstractSlider, меняется только _make_knob().

    steps — разрешение регулятора (позиций на полный ход). Меньше ->
    быстрее драг и крупнее насечки; точность ввода от steps не зависит
    (её даёт спинбокс)."""

    _KNOB_STRETCH = 1   # 0 — компактный регулятор, без растяжения ряда

    def __init__(self, label="", binding=None, *, minimum=0.0, maximum=1.0,
                 step=0.01, decimals=DEFAULT_DECIMALS, suffix="",
                 steps=1000, **kw):
        self._store_range(minimum, maximum, step, decimals, suffix)
        self._knob_steps = max(2, int(steps))
        super().__init__(label, binding, **kw)

    def _make_knob(self) -> QtWidgets.QAbstractSlider:
        return QtWidgets.QSlider(Qt.Horizontal)

    def _build(self, row) -> None:
        self.knob = self._make_knob()
        self.knob.setRange(0, self._knob_steps)
        self.spin = QtWidgets.QDoubleSpinBox()
        self.spin.setRange(self._lo, self._hi)
        self.spin.setSingleStep(self._step)
        self.spin.setDecimals(self._decimals)
        if self._suffix:
            self.spin.setSuffix(self._suffix)
        self.knob.valueChanged.connect(self._on_knob)
        self.spin.valueChanged.connect(self._on_spin)
        row.addWidget(self.knob, self._KNOB_STRETCH)
        row.addWidget(self.spin)
        if not self._KNOB_STRETCH:
            row.addStretch(1)

    def _to_unit(self, v: float) -> float:
        span = self._hi - self._lo
        return 0.0 if span <= 0 else (v - self._lo) / span

    def _to_knob(self, v: float) -> int:
        return round(self._to_unit(v) * self._knob_steps)

    def _on_knob(self, iv: int) -> None:
        v = self._lo + (self._hi - self._lo) * iv / self._knob_steps
        was, self._loading = self._loading, True
        try:
            self.spin.setValue(v)
        finally:
            self._loading = was
        self._emit(float(v))

    def _on_spin(self, v: float) -> None:
        was, self._loading = self._loading, True
        try:
            self.knob.setValue(self._to_knob(v))
        finally:
            self._loading = was
        self._emit(float(v))

    def value(self):
        return self.spin.value()

    def set_value(self, value) -> None:
        v = max(self._lo, min(self._hi, float(value)))
        with self._silence(self.spin, self.knob):
            self.spin.setValue(v)
            self.knob.setValue(self._to_knob(v))

    def focus_widgets(self):
        return [self.spin]

    def is_editing(self) -> bool:
        return self.knob.isSliderDown() or super().is_editing()

    def _values_differ(self, current, new) -> bool:
        return abs(current - float(new)) > _half_step(self._decimals)

class DialField(SliderField):
    """«Циферблат» (QDial, крутилка) + спинбокс. Удобен для углов и
    циклических величин; ход ~300 градусов, без wrapping.

    steps по умолчанию 100 (против 1000 у слайдера): скорость драга у
    QDial зашита в Qt и зависит от разрешения — на 1000 позиций драг
    ощутимо «вязкий». Насечки рисуются через steps/10 позиций."""

    _KNOB_STRETCH = 0

    def __init__(self, *args, steps=100, **kw):
        super().__init__(*args, steps=steps, **kw)

    def _make_knob(self) -> QtWidgets.QAbstractSlider:
        dial = QtWidgets.QDial()
        dial.setFixedSize(36, 36)  # иначе растягивает ряд по высоте
        if self._knob_steps >= 20:
            dial.setNotchTarget(max(4, self._knob_steps // 10))
            dial.setNotchesVisible(True)
        return dial

class BoolField(FormField):
    """Чекбокс; подпись — текст самого чекбокса (как в настройках рендера)."""

    def __init__(self, label="", binding=None, **kw):
        self._text = label
        super().__init__("", binding, **kw)  # отдельный QLabel не нужен

    def _build(self, row) -> None:
        self.check = QtWidgets.QCheckBox(self._text)
        self.check.toggled.connect(lambda v: self._emit(bool(v)))
        row.addWidget(self.check)
        row.addStretch(1)

    def value(self):
        return self.check.isChecked()

    def set_value(self, value) -> None:
        with self._silence(self.check):
            self.check.setChecked(bool(value))

    # У чекбокса нет состояния «редактируется»: клик мгновенно коммитит,
    # поэтому фокус синхронизацию не блокирует.
    def focus_widgets(self):
        return []

class ComboField(FormField):
    """Выпадающий список. items — [(подпись, данные)]; значение — данные
    (как currentData в инспекторе: id типа фигуры, индекс материала)."""

    def __init__(self, label="", binding=None, *,
                 items: Sequence[Tuple[str, Any]] = (), **kw):
        self._items = list(items)
        super().__init__(label, binding, **kw)

    def _build(self, row) -> None:
        self.combo = QtWidgets.QComboBox()
        for text, data in self._items:
            self.combo.addItem(text, data)
        self.combo.currentIndexChanged.connect(
            lambda _i: self._emit(self.combo.currentData()))
        row.addWidget(self.combo, 1)

    def set_items(self, items: Sequence[Tuple[str, Any]]) -> None:
        """Перезаполнить список (например, по materials_changed),
        сохранив выбранное значение, если оно ещё есть в списке."""
        current = self.value()
        with self._silence(self.combo):
            self.combo.clear()
            for text, data in items:
                self.combo.addItem(text, data)
        self.set_value(current)

    def value(self):
        return self.combo.currentData()

    def set_value(self, value) -> None:
        idx = self.combo.findData(value)
        if idx >= 0:
            with self._silence(self.combo):
                self.combo.setCurrentIndex(idx)

    def is_editing(self) -> bool:
        # Не дёргаем, пока открыт попап выбора.
        view = self.combo.view()
        return view is not None and view.isVisible()

def _krita_available() -> bool:
    """True, если код выполняется внутри Krita (импортируется krita)."""
    try:
        import krita  # noqa: F401
    except ImportError:
        return False
    return True

def _krita_color(which: str) -> Optional[List[float]]:
    """Текущий цвет Krita: 'foregroundColor' (кисть) или 'backgroundColor'.

    Возвращает [r, g, b] в 0..1 или None, если Krita недоступна либо
    нет активного вида (не открыт документ). ManagedColor переводится
    в QColor через colorForCanvas — с учётом цветоуправления вида;
    components() не используем: у RGBA в Krita порядок каналов чаще
    всего BGR.
    """
    try:
        from krita import Krita
    except ImportError:
        return None
    app = Krita.instance()
    window = app.activeWindow() if app is not None else None
    view = window.activeView() if window is not None else None
    if view is None:
        return None
    try:
        managed = getattr(view, which)()
        qcolor = managed.colorForCanvas(view.canvas())
    except Exception:
        return None
    if qcolor is None or not qcolor.isValid():
        return None
    return [qcolor.redF(), qcolor.greenF(), qcolor.blueF()]

class ColorField(FormField):
    """Свотч цвета + QColorDialog. Значение — [r, g, b], компоненты 0..1.

    krita=True добавляет кнопки «Кисть»/«Фон»: они берут текущий цвет
    переднего плана (кисти) и фона из активного вида Krita. Вне Krita
    (модуль krita не импортируется) кнопки создаются скрытыми.
    """

    def __init__(self, label="", binding=None, *, title="Цвет",
                 krita=False, **kw):
        self._title = title
        self._krita = krita
        self._color = [1.0, 1.0, 1.0]
        super().__init__(label, binding, **kw)

    def _build(self, row) -> None:
        self.btn = QtWidgets.QPushButton()
        style_color_button(self.btn, self._color)
        self.btn.clicked.connect(self._pick)
        row.addWidget(self.btn)
        if self._krita:
            self._add_krita_buttons(row)
        row.addStretch(1)

    def _add_krita_buttons(self, row) -> None:
        self.krita_fg = QtWidgets.QToolButton()
        self.krita_fg.setText("Кисть")
        self.krita_fg.setToolTip(
            "Взять цвет переднего плана (кисти) из Krita")
        self.krita_fg.clicked.connect(
            lambda: self._grab_krita("foregroundColor"))
        row.addWidget(self.krita_fg)
        self.krita_bg = QtWidgets.QToolButton()
        self.krita_bg.setText("Фон")
        self.krita_bg.setToolTip("Взять фоновый цвет из Krita")
        self.krita_bg.clicked.connect(
            lambda: self._grab_krita("backgroundColor"))
        row.addWidget(self.krita_bg)
        if not _krita_available():
            self.krita_fg.hide()
            self.krita_bg.hide()

    def _grab_krita(self, which: str) -> None:
        rgb = _krita_color(which)
        if rgb is None:
            return
        self._color = rgb
        style_color_button(self.btn, self._color)
        self._emit(list(self._color))

    def _pick(self) -> None:
        initial = QColor.fromRgbF(
            *[max(0.0, min(1.0, c)) for c in self._color])
        color = QtWidgets.QColorDialog.getColor(initial, self, self._title)
        if color.isValid():
            self._color = [color.redF(), color.greenF(), color.blueF()]
            style_color_button(self.btn, self._color)
            self._emit(list(self._color))

    def value(self):
        return list(self._color)

    def set_value(self, value) -> None:
        rgb = (list(value) + [0.0, 0.0, 0.0])[:3]
        self._color = [float(c) for c in rgb]
        style_color_button(self.btn, self._color)

    def _values_differ(self, current, new) -> bool:
        new = (list(new) + [0.0, 0.0, 0.0])[:3]
        return any(abs(a - float(b)) > 1e-3 for a, b in zip(current, new))

#endregion
# ----------------------------------------------------------------------
# Векторные поля
# ----------------------------------------------------------------------
#region
class Vec3Field(FormField):
    """Три спинбокса с цветными метками осей (X/Y/Z из ui_style).
    Значение — [x, y, z]. suffix=" °" — для углов.

    _count — атрибут КЛАССА (у Vec2Field он 2): обращаться к self до
    super().__init__() у Qt-виджетов нельзя (RuntimeError)."""

    _count = 3

    def __init__(self, label="", binding=None, *, minimum=-10000.0,
                 maximum=10000.0, step=0.05, decimals=DEFAULT_DECIMALS,
                 suffix="", **kw):
        self._store_range(minimum, maximum, step, decimals, suffix)
        super().__init__(label, binding, **kw)

    def _build(self, row) -> None:
        self.spins: List[QtWidgets.QDoubleSpinBox] = []
        for _i in range(self._count):
            spin = QtWidgets.QDoubleSpinBox()
            spin.setRange(self._lo, self._hi)
            spin.setSingleStep(self._step)
            spin.setDecimals(self._decimals)
            if self._suffix:
                spin.setSuffix(self._suffix)
            spin.valueChanged.connect(self._on_change)
            self.spins.append(spin)
        add_axis_labels(row, self.spins)

    def _on_change(self, _v) -> None:
        self._emit([s.value() for s in self.spins])

    def value(self):
        return [s.value() for s in self.spins]

    def set_value(self, value) -> None:
        vec = (list(value) + [0.0] * self._count)[:self._count]
        with self._silence(*self.spins):
            for spin, v in zip(self.spins, vec):
                spin.setValue(max(self._lo, min(self._hi, float(v))))

    def focus_widgets(self):
        return list(self.spins)

    def _values_differ(self, current, new) -> bool:
        new = (list(new) + [0.0] * self._count)[:self._count]
        eps = _half_step(self._decimals)
        return any(abs(a - float(b)) > eps for a, b in zip(current, new))

class Vec2Field(Vec3Field):
    """Два спинбокса (X/Y) — например, tiling или размер."""

    _count = 2

#endregion
# ----------------------------------------------------------------------
# Декларативные спеки
# ----------------------------------------------------------------------
#region
@dataclass
class FieldSpec:
    """Описание поля для Form.add_specs.

    kind: 'text' | 'float' | 'int' | 'slider' | 'dial' | 'bool'
          | 'combo' | 'color' | 'vec2' | 'vec3' | 'angle' | 'angle_vec3'
    attr — имя атрибута цели; options — items для combo [(подпись, данные)].
    krita — для kind='color': кнопки взятия цвета кисти/фона из Krita.
    kind='angle' / 'angle_vec3' — углы: атрибут хранит радианы (скаляр или [x, y, z]), UI показывает градусы.
    """
    kind: str
    label: str
    attr: str = ""
    minimum: float = -10000.0
    maximum: float = 10000.0
    step: float = 0.05
    decimals: int = DEFAULT_DECIMALS
    suffix: str = ""
    options: Optional[Sequence[Tuple[str, Any]]] = None
    syncable: bool = True
    krita: bool = False

#endregion
# ----------------------------------------------------------------------
# Синхронизатор
# ----------------------------------------------------------------------
#region
class FormSync(QtCore.QObject):
    """Периодическая сверка полей с данными (данные ссылочные — просто
    перечитываем и сравниваем).

    - Включается флагом Form(sync=True) или позже через set_enabled(on);
      поля с syncable=False и поля, которые пользователь редактирует прямо
      сейчас, пропускаются (см. FormField.refresh / is_editing).
    - attach_signal(сигнал) — альтернатива/дополнение к таймеру: сверка
      по сигналу (например params.changed). Учтите: сигнал может стрелять
      каждый кадр drag'а — refresh дешёвый, но для тяжёлых форм лучше
      таймер.
    - Поля, чей C++-объект удалён (форму перестроили без clear()),
      вычищаются из списка автоматически.
    """

    def __init__(self, interval_ms: int = 150, parent=None):
        super().__init__(parent)
        self._fields: List[FormField] = []
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(max(30, int(interval_ms)))
        self._timer.timeout.connect(self.sync_now)
        self._enabled = False

    def register(self, field: FormField) -> FormField:
        self._fields.append(field)
        return field

    def clear(self) -> None:
        """Забыть все поля (Form.clear вызывает сам при перестройке)."""
        self._fields.clear()

    def set_enabled(self, on: bool) -> None:
        on = bool(on)
        if on == self._enabled:
            return
        self._enabled = on
        if on:
            self.sync_now()
            self._timer.start()
        else:
            self._timer.stop()

    def is_enabled(self) -> bool:
        return self._enabled

    def attach_signal(self, signal) -> None:
        """Сверять по сигналу (params.changed, controller.node_updated...)."""
        signal.connect(self.sync_now)

    def sync_now(self) -> None:
        alive: List[FormField] = []
        for field in self._fields:
            try:
                if field.syncable:
                    field.refresh()
            except RuntimeError:
                continue  # C++-объект поля уже удалён
            alive.append(field)
        self._fields = alive

#endregion
# ----------------------------------------------------------------------
# Форма
# ----------------------------------------------------------------------
#region
class Form(QtWidgets.QWidget):
    """Вертикальная форма: заголовки секций, поля, опциональная живая
    синхронизация.

    sync=True включает периодическую сверку полей с данными (таймер
    FormSync). Имеет смысл для форм, чьи данные меняются мимо UI
    (камера/трансформации мышью во вьюпорте); для остальных — False.
    Переключить позже: form.sync.set_enabled(on).
    """

    def __init__(self, parent=None, *, sync: bool = False,
                 sync_interval_ms: int = 150):
        super().__init__(parent)
        self._layout = QtWidgets.QVBoxLayout(self)
        self._layout.setAlignment(Qt.AlignTop)
        tune_layout(self._layout)

        self.sync = FormSync(interval_ms=sync_interval_ms, parent=self)
        self._fields: List[FormField] = []
        self.sync.set_enabled(sync)

    #region --- Секции и произвольные виджеты --- #

    def add_header(self, text: str) -> QtWidgets.QWidget:
        header = section_header(text)
        self._layout.addWidget(header)
        return header

    def add_widget(self, widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        """Произвольный виджет (например TextureSlotsWidget) — без сверки."""
        self._layout.addWidget(widget)
        return widget

    def add_labeled(self, label: str,
                    widget: QtWidgets.QWidget) -> QtWidgets.QWidget:
        """Произвольный виджет с подписью слева — без сверки."""
        box = QtWidgets.QWidget()
        row = QtWidgets.QHBoxLayout(box)
        row.setContentsMargins(0, 0, 0, 0)
        row.addWidget(QtWidgets.QLabel(label))
        row.addWidget(widget, 1)
        self._layout.addWidget(box)
        return box

    #endregion
    
    #region --- Поля --- #

    def add_field(self, field: FormField) -> FormField:
        self._fields.append(field)
        self.sync.register(field)
        self._layout.addWidget(field)
        return field

    def add_text(self, label, binding=None, **kw) -> TextField:
        return self.add_field(TextField(label, binding, **kw))

    def add_float(self, label, binding=None, **kw) -> FloatField:
        return self.add_field(FloatField(label, binding, **kw))

    def add_int(self, label, binding=None, **kw) -> IntField:
        return self.add_field(IntField(label, binding, **kw))

    def add_slider(self, label, binding=None, **kw) -> SliderField:
        return self.add_field(SliderField(label, binding, **kw))

    def add_dial(self, label, binding=None, **kw) -> DialField:
        return self.add_field(DialField(label, binding, **kw))

    def add_bool(self, label, binding=None, **kw) -> BoolField:
        return self.add_field(BoolField(label, binding, **kw))

    def add_combo(self, label, items, binding=None, **kw) -> ComboField:
        return self.add_field(ComboField(label, binding, items=items, **kw))

    def add_color(self, label, binding=None, **kw) -> ColorField:
        return self.add_field(ColorField(label, binding, **kw))

    def add_angle(self, label, binding=None, **kw) -> FloatField:
        """Поле угла: данные в радианах, ввод и отображение в градусах.

        binding — привязка к РАДИАНАМ (оборачивается в AngleBinding);
        minimum/maximum/step задаются уже в градусах. Для «крутилки»
        то же вручную: add_dial(label, AngleBinding(binding),
        minimum=-180.0, maximum=180.0, suffix=" °").
        """
        kw.setdefault("minimum", -360.0)
        kw.setdefault("maximum", 360.0)
        kw.setdefault("step", 1.0)
        kw.setdefault("decimals", 1)
        kw.setdefault("suffix", " °")
        if binding is not None:
            binding = AngleBinding(binding)
        return self.add_field(FloatField(label, binding, **kw))

    def add_angle_vec3(self, label, binding=None, **kw) -> Vec3Field:
        """Три угла (например rotation): данные в радианах, UI — градусы."""
        kw.setdefault("minimum", -360.0)
        kw.setdefault("maximum", 360.0)
        kw.setdefault("step", 1.0)
        kw.setdefault("decimals", 1)
        kw.setdefault("suffix", " °")
        if binding is not None:
            binding = AngleBinding(binding)
        return self.add_field(Vec3Field(label, binding, **kw))

    def add_vec2(self, label, binding=None, **kw) -> Vec2Field:
        return self.add_field(Vec2Field(label, binding, **kw))

    def add_vec3(self, label, binding=None, **kw) -> Vec3Field:
        return self.add_field(Vec3Field(label, binding, **kw))

    #endregion
    
    #region --- Декларативное построение --- #

    def add_specs(self, specs: Sequence[FieldSpec], target,
                  commit: Optional[Callable[[], None]] = None
                  ) -> List[FormField]:
        """Построить поля по списку FieldSpec, привязав к атрибутам target.

        target — объект или provider (callable -> объект); commit — общий
        колбэк после правки любого поля (controller.commit_*,
        params.emit_changed).
        """
        fields = []
        for spec in specs:
            binding = AttrBinding(
                target, spec.attr, commit=commit,
                transform=(list if spec.kind in ("vec2", "vec3", "color",
                                                 "angle_vec3")
                           else None),
            )
            fields.append(self._add_spec(spec, binding))
        return fields

    def _add_spec(self, spec: FieldSpec, binding: Binding) -> FormField:
        simple = {"text": self.add_text, "bool": self.add_bool}
        if spec.kind in simple:
            return simple[spec.kind](spec.label, binding,
                                     syncable=spec.syncable)
        if spec.kind == "color":
            return self.add_color(spec.label, binding, krita=spec.krita,
                                  syncable=spec.syncable)
        if spec.kind == "angle":
            return self.add_angle(spec.label, binding,
                                  minimum=spec.minimum,
                                  maximum=spec.maximum,
                                  step=spec.step, decimals=spec.decimals,
                                  suffix=spec.suffix or " °",
                                  syncable=spec.syncable)
        if spec.kind == "angle_vec3":
            return self.add_angle_vec3(spec.label, binding,
                                       minimum=spec.minimum,
                                       maximum=spec.maximum,
                                       step=spec.step,
                                       decimals=spec.decimals,
                                       suffix=spec.suffix or " °",
                                       syncable=spec.syncable)
        if spec.kind == "combo":
            return self.add_combo(spec.label, spec.options or [], binding,
                                  syncable=spec.syncable)
        ranged = {"float": self.add_float, "int": self.add_int,
                  "slider": self.add_slider, "dial": self.add_dial,
                  "vec2": self.add_vec2, "vec3": self.add_vec3}
        if spec.kind not in ranged:
            raise ValueError(localization.get_text("forms.add_spec.error.unknown_kind", kind=spec.kind))
        kw = dict(minimum=spec.minimum, maximum=spec.maximum,
                  step=spec.step, syncable=spec.syncable)
        if spec.kind == "int":
            kw.update(minimum=int(spec.minimum), maximum=int(spec.maximum),
                      step=max(1, int(spec.step)))
        else:
            kw.update(decimals=spec.decimals, suffix=spec.suffix)
        return ranged[spec.kind](spec.label, binding, **kw)

    #endregion

    #region --- Сверка и перестройка --- #

    def refresh(self) -> None:
        """Разовая сверка всех полей (независимо от флага sync)."""
        self.sync.sync_now()

    def clear(self) -> None:
        """Удалить все поля и секции (аналог InspectorPanel.rebuild).
        Состояние синхронизации (вкл/выкл) сохраняется."""
        self.sync.clear()
        self._fields.clear()
        while self._layout.count():
            item = self._layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    #endregion

#endregion