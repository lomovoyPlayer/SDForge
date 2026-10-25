"""
SDForge: инспектор — параметры выделенного узла, включая текстурные слоты.

Независимый блок: получает (params, controller). Правки мутируют объект
узла в памяти и сохраняются через controller.commit_node (1 строка в БД +
перерисовка). Текстуры хранятся в fig.texture_slots по имени файла;
runtime id резолвится контроллером при коммите.
"""

from PyQt5 import QtCore, QtWidgets
from ...core.data_classes import GroupOperation, Operation, TreeObject
from ...defaults import BINARY_OP_TYPE_NAMES, UNARY_OP_TYPE_NAMES, OP_TYPE_NAMES
from .texture_picker_panel import TextureSlotsWidget
from .forms import Form, FormField, AttrBinding, Binding

from ..scene_controller import SceneController
from ..render_previewer import RenderPreviewParams
from ... import localization




class InspectorPanel(QtWidgets.QScrollArea):
    """Панель инспектора: параметры выделенного узла."""
    def __init__(self, params: "RenderPreviewParams", controller: "SceneController", parent=None):
        super().__init__(parent)
        self.params = params
        self.controller = controller
        self.setWidgetResizable(True)
        self.form = Form(self, sync=True)
        self.form.add_header(localization.get_text("inspector_panel.header"))
        self.setWidget(self.form)


        self._history_field = None

        self._wheel_history_field = None
        self._wheel_history_timer = QtCore.QTimer(self)
        self._wheel_history_timer.setSingleShot(True)
        self._wheel_history_timer.timeout.connect(self._finish_wheel_history)

        QtWidgets.QApplication.instance().focusChanged.connect(
            self._on_history_focus_changed
        )
        self._mat_field = None

        controller.selection_changed.connect(lambda _id: self.rebuild())
        controller.node_structure_changed.connect(self.rebuild)
        controller.tree_loaded.connect(lambda _id: self.rebuild())
        controller.materials_changed.connect(self._on_materials_changed)

        self.setMinimumSize(420, 200)
        self.rebuild()

    # ------------------------------------------------------------------
    # Построение формы
    # ------------------------------------------------------------------
    #region
    @staticmethod
    def _op_items():
        """Список операций."""
        # Наложение листа и клей группы — всегда бинарная операция
        return [(name, i) for i, name in enumerate(BINARY_OP_TYPE_NAMES)]

    @staticmethod
    def _unary_items():
        """Список унарных операций."""
        # Унарный модификатор; -1 = нет
        offset = len(BINARY_OP_TYPE_NAMES)
        return [("None", -1)] + [
            (name, offset + i) for i, name in enumerate(UNARY_OP_TYPE_NAMES)
        ]

    def _material_items(self):
        """Список материалов."""
        return [(m["name"] or f"Material {i}", i)
                for i, m in enumerate(self.controller.materials())]

    def _on_materials_changed(self):
        """Обновить список материалов в форме, если он изменился."""
        if self._mat_field is None:
            return
        try:
            self._mat_field.set_items(self._material_items())
        except RuntimeError:
            self._mat_field = None  # форму уже перестроили

    def _finish_wheel_history(self):
        """Завершить серию прокруток одного поля."""
        self._wheel_history_timer.stop()
        if self._wheel_history_field is None:
            return

        self._wheel_history_field = None
        self.params.end_scene_change()

    def eventFilter(self, obj, event):
        """Отслеживать события колеса мыши на виджетах формы, чтобы объединять их в одно действие в журнале изменений."""
        if event.type() == QtCore.QEvent.Wheel:
            field = self._field_for_widget(obj)

            if field is not None and field is not self._history_field:
                if self._wheel_history_field is not field:
                    self._finish_wheel_history()
                    self.params.begin_scene_change()
                    self._wheel_history_field = field

                # Каждое новое событие колеса продлевает ту же правку.
                self._wheel_history_timer.start(200)

        return super().eventFilter(obj, event)

    def _install_history_wheel_filters(self):
        """Следить и за основной формой, и за вложенными формами слотов."""
        for widget_type in (
            QtWidgets.QAbstractSpinBox,
            QtWidgets.QAbstractSlider,
        ):
            for widget in self.form.findChildren(widget_type):
                widget.installEventFilter(self)

    def _field_for_widget(self, widget):
        """Найти поле инспектора, которому принадлежит виджет с фокусом."""
        while widget is not None:
            if isinstance(widget, FormField):
                return widget if self.form.isAncestorOf(widget) else None
            widget = widget.parentWidget()
        return None

    def _finish_history_edit(self):
        """Завершить составную правку предыдущего поля."""
        if self._history_field is None:
            return
        self._history_field = None
        self.params.end_scene_change()

    def _on_history_focus_changed(self, old, new):
        """При переходе к другому полю закончить прежнюю правку."""
        field = self._field_for_widget(new)
        if field is self._history_field:
            return

        self._finish_history_edit()

        if self._wheel_history_field is not None:
            if self._wheel_history_field is not field:
                self._finish_wheel_history()

        if field is not None:
            self.params.begin_scene_change()
            self._history_field = field

    def rebuild(self):
        """Перестроить форму."""
        self._finish_history_edit()
        self._finish_wheel_history()
        self.form.clear()
        self._mat_field = None
        node = self._node()
        if node is None:
            self.form.add_widget(QtWidgets.QLabel(localization.get_text("inspector_panel.no_selection")))
            return
        self.form.add_text(localization.get_text("inspector_panel.node_name"), self._bind_attr(self._node, "name"))
        if isinstance(node, GroupOperation):
            self._build_group_fields(node)
        elif isinstance(node, (TreeObject, Operation)):
            self._build_figure_fields(node)
        self._install_history_wheel_filters()

    def _bind_operand(self, index):
        """Привязка операндов бинарной операции"""
        # Операнды бинарной операции: у группы — на самом узле,
        # у листа — на base_object обёртки TreeObject
        def target():
            node = self._node()
            if isinstance(node, TreeObject):
                return node.base_object
            return node
        def get():
            ops = getattr(target(), "operands", None)
            return None if ops is None else (list(ops) + [0.0] * 4)[index]
        def set_(v):
            t = target()
            ops = list(getattr(t, "operands", [])) + [0.0] * 4
            ops[index] = v
            t.operands = ops[:4]
        return Binding(get, set_, commit=self._commit)

    def _bind_unary_operand(self, index):
        """Привязка операндов унарного модификатора"""
        # Операнды унарного модификатора: у группы — unary_operands,
        # у листа — unary_op.operands
        def target():
            node = self._node()
            if isinstance(node, GroupOperation):
                return node.unary_operands
            if isinstance(node, TreeObject) and node.unary_op is not None:
                return node.unary_op.operands
            return None
        def get():
            ops = target()
            return None if ops is None else (list(ops) + [0.0] * 4)[index]
        def set_(v):
            ops = target()
            if ops is None:
                return
            ops = list(ops) + [0.0] * 4
            ops[index] = v
            node = self._node()
            if isinstance(node, GroupOperation):
                node.unary_operands = ops[:4]
            else:
                node.unary_op.operands = ops[:4]
        return Binding(get, set_, commit=self._commit)

    def _bind_leaf_unary_type(self):
        """Привязка типа унарного модификатора у листа."""
        # -1 = снять унарную операцию с листа
        def get():
            node = self._node()
            if isinstance(node, TreeObject) and node.unary_op is not None:
                return node.unary_op.op_type
            return -1
        def set_(v):
            node = self._node()
            if not isinstance(node, TreeObject):
                return
            if v == -1:
                node.unary_op = None
            elif node.unary_op is not None:
                node.unary_op.op_type = v
            else:
                node.unary_op = Operation(v)
        return Binding(get, set_, commit=self._commit)

    def _build_group_fields(self, node):
        """Сборка полей для группы операций."""
        op_field = self.form.add_combo(
            localization.get_text("inspector_panel.build_group_fields.operation"), self._op_items(),
            self._bind_attr(self._node, "op_type"))
        smooth = self.form.add_float(
            localization.get_text("inspector_panel.build_group_fields.radius_or_coeff"), self._bind_operand(2),
            minimum=-1000.0, maximum=1000.0, step=0.01)
        smooth.setEnabled(node.op_type >= 3)
        op_field.changed.connect(lambda v: smooth.setEnabled(v >= 3))

        unary_field = self.form.add_combo(
            localization.get_text("inspector_panel.build_group_fields.modifier"), self._unary_items(),
            self._bind_attr(self._node, "unary_op_type"))
        unary_k = self.form.add_float(
            localization.get_text("inspector_panel.build_group_fields.unary_k"), self._bind_unary_operand(2),
            minimum=-1000.0, maximum=1000.0, step=0.01)
        unary_k.setEnabled(node.unary_op_type != -1)
        unary_field.changed.connect(lambda v: unary_k.setEnabled(v != -1))

    def _build_figure_fields(self, node):
        """Сборка полей для листа (TreeObject)"""
        base = node.base_object if isinstance(node, TreeObject) else node
        if base.figure is None:
            self.form.add_widget(QtWidgets.QLabel(localization.get_text("inspector_panel.build_figure_fields.no_figure")))
            return

        # Тип фигуры (из реестра sdf_types; fallback — встроенные)
        sdf_types = self.controller.sdf_types()
        items = ([(t["name"], t["id"]) for t in sdf_types]
                 or [("Sphere", 0), ("Box", 1)])
        self.form.add_combo(localization.get_text("inspector_panel.build_figure_fields.type"), items,
                            self._bind_attr(self._figure, "type"))

        # Наложение листа — та же связка, что и у группы
        op_field = self.form.add_combo(localization.get_text("inspector_panel.build_figure_fields.operation"), self._op_items(),
                                       self._bind_attr(self._base, "op_type"))
        smooth = self.form.add_float(localization.get_text("inspector_panel.build_figure_fields.radius_or_coeff"),
                                     self._bind_operand(2),
                                     minimum=-1000.0, maximum=1000.0, step=0.01)
        smooth.setEnabled(base.op_type >= 3)
        op_field.changed.connect(lambda v: smooth.setEnabled(v >= 3))

        # Унарная операция над SDF листа (round/shell)
        if isinstance(node, TreeObject):
            unary_field = self.form.add_combo(
                localization.get_text("inspector_panel.build_figure_fields.unary_op"), self._unary_items(),
                self._bind_leaf_unary_type())
            unary_k = self.form.add_float(
                localization.get_text("inspector_panel.build_figure_fields.unary_k"), self._bind_unary_operand(2),
                minimum=-1000.0, maximum=1000.0, step=0.01)
            unary_k.setEnabled(node.unary_op is not None)
            unary_field.changed.connect(lambda v: unary_k.setEnabled(v != -1))

        # Материал
        self._mat_field = self.form.add_combo(
            localization.get_text("inspector_panel.build_figure_fields.material"), self._material_items(),
            self._bind_attr(self._figure, "material_id"))

        # Цвет
        self.form.add_color(localization.get_text("inspector_panel.build_figure_fields.albedo"), self._bind_attr(self._figure, "albedo",
                                                     transform=list), krita=True)

        # Трансформации
        self.form.add_header(localization.get_text("inspector_panel.build_figure_fields.transformations"))
        self.form.add_vec3(localization.get_text("inspector_panel.build_figure_fields.position"),   self._bind_attr(self._figure, "translation", transform=list))
        self.form.add_angle_vec3(localization.get_text("inspector_panel.build_figure_fields.rotation"),  self._bind_attr(self._figure, "rotation",    transform=list))
        self.form.add_vec3(localization.get_text("inspector_panel.build_figure_fields.elongation"), self._bind_attr(self._figure, "elongation",  transform=list))
        self.form.add_float(localization.get_text("inspector_panel.build_figure_fields.scale"),  self._bind_attr(self._figure, "scale"),
                            minimum=0.001, maximum=1000.0, step=0.01)

        # Текстуры
        self.form.add_header(localization.get_text("inspector_panel.build_figure_fields.textures"))
        self.form.add_widget(TextureSlotsWidget(
            self.params, self.controller, base, commit=self._commit))

    #endregion

    # ------------------------------------------------------------------
    # Provider'ы и единый коммит
    # ------------------------------------------------------------------
    #region
    def _node(self):
        """Provider: актуальный выделенный узел."""
        return self.controller.selected_node()

    def _base(self):
        """Provider: актуальный базовый узел."""
        node = self._node()
        if isinstance(node, TreeObject):
            return node.base_object
        return node if isinstance(node, Operation) else None

    def _figure(self):
        """Provider: актуальная фигура (SDF) выделенного узла."""
        base = self._base()
        return base.figure if base is not None else None

    def _commit(self):
        """Сохранить изменения в выделенном узле через контроллер."""
        node = self._node()
        if node is not None:
            self.controller.commit_node(node)

    def _bind_attr(self, target, attr, **kw):
        """Привязка атрибута объекта к полю формы."""
        return AttrBinding(target, attr, commit=self._commit, **kw)

    #endregion