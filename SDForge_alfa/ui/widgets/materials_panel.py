"""
SDForge: панель материалов (глобальная библиотека) — вкладка правой
колонки диалога.

Независимый блок: получает (params, controller). Слева — список материалов,
справа — редактор выбранного. Правки коммитятся сразу через
controller.commit_material (1 строка в БД + перерисовка).

Редактор НЕ перестраивается по materials_changed (иначе спинбоксы теряли
бы фокус посреди ввода) — обновляется только список; редактор перечитывает
материал при смене выделения.
"""

from typing import Optional

from PyQt5 import QtWidgets
from PyQt5.QtCore import Qt

from .forms import FieldSpec, Form, Binding
from ..scene_controller import SceneController
from ..render_previewer import RenderPreviewParams

from ... import localization

from ..ui_style import style_danger_button, tune_layout


MATERIAL_SPECS = [
    FieldSpec("slider", localization.get_text("material_panel.fields_specs.roughness"),     "roughness",   minimum=0.0, maximum=1.0, step=0.01),
    FieldSpec("slider", localization.get_text("material_panel.fields_specs.metallic"),      "metallic",    minimum=0.0, maximum=1.0, step=0.01),
    FieldSpec("slider", localization.get_text("material_panel.fields_specs.transmission"),  "transmission", minimum=0.0, maximum=1.0, step=0.01),
    FieldSpec("dial",   localization.get_text("material_panel.fields_specs.ior"),           "ior",         minimum=1.0, maximum=3.0, step=0.01),
    FieldSpec("slider", localization.get_text("material_panel.fields_specs.subsurface"),    "subsurface", minimum=0.0, maximum=2.0, step=0.01),
    FieldSpec("color",  localization.get_text("material_panel.fields_specs.sss_color"),     "sss_color",   krita=True),
    FieldSpec("float",  localization.get_text("material_panel.fields_specs.emission"),      "emission",    minimum=0.0, maximum=100.0, step=0.1),
]


class MaterialsPanel(QtWidgets.QWidget):
    """
    Панель материалов (глобальная библиотека)"""
    def __init__(self, params: "RenderPreviewParams", controller: "SceneController", parent=None):
        super().__init__(parent)
        self.params = params
        self.controller = controller
        self._current_id: Optional[int] = None
        self._material = None      # редактируемый объект (копия из библиотеки)
        self._name = ""

        layout = QtWidgets.QHBoxLayout(self)
        tune_layout(layout)

        #region  --- Список --- #
        left = QtWidgets.QVBoxLayout()
        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_row_changed)
        left.addWidget(self.list_widget, 1)

        buttons = QtWidgets.QHBoxLayout()
        self.btn_add = QtWidgets.QPushButton(localization.get_text("material_panel.buttons.add"))
        self.btn_dup = QtWidgets.QPushButton(localization.get_text("material_panel.buttons.duplicate"))
        self.btn_del = QtWidgets.QPushButton(localization.get_text("material_panel.buttons.delete"))
        style_danger_button(self.btn_del)
        buttons.addWidget(self.btn_add)
        buttons.addWidget(self.btn_dup)
        buttons.addWidget(self.btn_del)
        left.addLayout(buttons)
        layout.addLayout(left, 1)

        self.btn_add.clicked.connect(self._on_add)
        self.btn_dup.clicked.connect(self._on_duplicate)
        self.btn_del.clicked.connect(self._on_delete)
        #endregion

        #region  --- Редактор --- #
        self.form = Form(self)
        self.form.add_text(localization.get_text("material_panel.fields.name"), Binding(
            lambda: self._name if self._material is not None else None,
            lambda v: setattr(self, "_name", v),\
            commit=self._commit
        ))
        self.form.add_specs(MATERIAL_SPECS, 
                            target=lambda: self._material, 
                            commit=self._commit)

        layout.addWidget(self.form, 2)

        controller.materials_changed.connect(self.reload_list)
        self.reload_list()
        #endregion

    # ------------------------------------------------------------------
    # Список
    # ------------------------------------------------------------------
    #region
    def reload_list(self) -> None:
        """Перечитывает список из библиотеки, сохраняя выделение.
        Редактор здесь не трогаем — он живёт на своей копии материала."""
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        selected_row = -1
        for i, m in enumerate(self.controller.materials()):
            label = m["name"] or f"Material {i}"
            if m.get("is_builtin"):
                label = f"🔒 {label}"
            item = QtWidgets.QListWidgetItem(label)
            item.setData(Qt.UserRole, m["id"])
            self.list_widget.addItem(item)
            if m["id"] == self._current_id:
                selected_row = i
        self.list_widget.blockSignals(False)
        if selected_row >= 0:
            self.list_widget.setCurrentRow(selected_row)
        elif self.list_widget.count():
            self.list_widget.setCurrentRow(0)
        else:
            self.form.setEnabled(False)

    def _on_row_changed(self, row: int) -> None:
        item = self.list_widget.item(row)
        if item is None:
            return
        db_id = item.data(Qt.UserRole)
        # Свежая копия из библиотеки (list_materials создаёт новые объекты)
        for m in self.controller.materials():
            if m["id"] == db_id:
                self._current_id = db_id
                self._material = m["material"]
                self._name = m["name"] or ""
                self.form.setEnabled(not m.get("is_builtin"))
                self.form.refresh()
                return

    #endregion
    # ------------------------------------------------------------------
    # Кнопки, коммит и хелперы
    # ------------------------------------------------------------------
    #region
    def _commit(self):
        if self._material is None or self._current_id is None:
            return
        if self._is_builtin(self._current_id):
            QtWidgets.QMessageBox.warning(
                self, localization.get_text("material_panel.commit.warning.is_builtin.title"),
                localization.get_text("material_panel.commit.warning.is_builtin.text"),
            )
            return
        self.controller.commit_material(
            self._current_id, material=self._material, name=self._name)

    def _on_add(self) -> None:
        db_id = self.controller.add_material(
            name=f"Material {self.list_widget.count()}"
        )
        self._current_id = db_id
        self.reload_list()

    def _on_delete(self) -> None:
        if self._current_id is None:
            return
        if self._is_builtin(self._current_id):
            QtWidgets.QMessageBox.warning(
                self, localization.get_text("material_panel.on_delete.warning.is_builtin.title"),
                localization.get_text("material_panel.on_delete.warning.is_builtin.text"),
            )
            return
        if self.controller.is_material_in_use(self._current_id):
            answer = QtWidgets.QMessageBox.question(
                self, localization.get_text("material_panel.on_delete.warning.in_use.title"),
                localization.get_text("material_panel.on_delete.warning.in_use.text")
            )
            if answer != QtWidgets.QMessageBox.Yes:
                return
        self.controller.remove_material(self._current_id)
        self._current_id = None
        self._material = None
        self.reload_list()

    def _on_duplicate(self) -> None:
        if self._current_id is None:
            return
        # Дублирование разрешено и для встроенных: копия — пользовательская
        db_id = self.controller.duplicate_material(self._current_id)
        self._current_id = db_id
        self.reload_list()

    def _is_builtin(self, db_id: int) -> bool:
        for m in self.controller.materials():
            if m["id"] == db_id:
                return bool(m.get("is_builtin"))
        return False
    #endregion