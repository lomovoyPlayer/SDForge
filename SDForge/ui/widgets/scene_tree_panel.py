"""
SDForge: панель дерева узлов сцены.

Независимый блок: читает params.scene_root, структурные изменения делает
только через controller (add/move/duplicate/remove в БД), перечитывает
дерево по сигналу node_structure_changed. Значения item'ов — db_id узлов.

Выделение — множественное (ExtendedSelection) и синхронизируется с
params.selected_operation_indices через controller.select_nodes():
выделенная группа разворачивается во все листья-фигуры ниже по иерархии
включительно, поэтому групповые трансформации во вьюпорте
(get_selected_figures) работают на всё поддерево.

Drag&drop: сброс НА группе (индикатор OnItem) — перемещение внутрь неё;
Above/Below — соседом до/после; мимо item'ов — в корень. Qt item'ы сама
не двигает: после move_node дерево перестраивается из БД.
"""

from typing import List, Optional

from PyQt5 import QtWidgets, QtCore
from PyQt5.QtCore import Qt

from ...core.data_classes import GroupOperation, Operation, TreeObject

from ..ui_style import style_danger_button, tune_layout

from ..scene_controller import SceneController
from ..render_previewer import RenderPreviewParams

from ...defaults import OP_TYPE_NAMES

from ... import localization

ROLE_NODE_ID = Qt.UserRole
ROLE_IS_GROUP = Qt.UserRole + 1


class SceneTreeWidget(QtWidgets.QTreeWidget):
    """QTreeWidget с internal drag&drop, переложенным на controller.move_node."""

    def __init__(self, panel: "SceneTreePanel"):
        super().__init__()
        self.panel = panel

    def dropEvent(self, event):
        dragged_items = self.selectedItems()
        if not dragged_items and self.currentItem() is not None:
            dragged_items = [self.currentItem()]
        if not dragged_items:
            event.ignore()
            return

        # Если выделены и группа, и её потомок — перемещается только группа
        def has_selected_ancestor(item):
            parent = item.parent()
            while parent is not None:
                if parent in dragged_items:
                    return True
                parent = parent.parent()
            return False

        dragged_items = [it for it in dragged_items if not has_selected_ancestor(it)]

        root_id = self.panel.root_db_id
        target = self.itemAt(event.pos())
        pos = self.dropIndicatorPosition()
        view = QtWidgets.QAbstractItemView

        if target in dragged_items:
            event.ignore()
            return

        if target is None or pos == view.OnViewport:
            # мимо item'ов — в корень
            new_parent_id, index = root_id, None
        elif pos == view.OnItem and target.data(0, ROLE_IS_GROUP):
            # сброс на группе — внутрь неё, в конец
            new_parent_id, index = target.data(0, ROLE_NODE_ID), None
        else:
            # соседом до/после цели; OnItem на листе трактуем как "после"
            parent = target.parent()
            new_parent_id = (
                parent.data(0, ROLE_NODE_ID) if parent is not None else root_id
            )
            row = (parent if parent is not None
                   else self.invisibleRootItem()).indexOfChild(target)
            index = row if pos == view.AboveItem else row + 1

        # db_id снимаем заранее: после первого move_node дерево перечитывается
        # из БД и Qt-item'ы пересоздаются, сами объекты использовать нельзя
        node_ids = [it.data(0, ROLE_NODE_ID) for it in dragged_items]

        event.ignore()  # перестроение — из БД по node_structure_changed
        for offset, node_id in enumerate(node_ids):
            try:
                self.panel.controller.move_node(
                    node_id, new_parent_id,
                    None if index is None else index + offset,
                )
            except ValueError as e:
                QtWidgets.QMessageBox.warning(self, localization.get_text("scene_tree_widget.drop_error"), str(e))
                break


class SceneTreePanel(QtWidgets.QWidget):
    def __init__(self, params: "RenderPreviewParams",
                 controller: "SceneController", parent=None):
        super().__init__(parent)
        self.params = params
        self.controller = controller
        self.root_db_id: Optional[int] = None
        self._restoring_selection = False

        layout = QtWidgets.QVBoxLayout(self)
        tune_layout(layout)

        layout.addWidget(QtWidgets.QLabel(localization.get_text("scene_tree_panel.label")))

        self.tree = SceneTreeWidget(self)
        self.tree.setHeaderHidden(True)
        self.tree.setDragDropMode(QtWidgets.QAbstractItemView.InternalMove)
        self.tree.setSelectionMode(QtWidgets.QAbstractItemView.ExtendedSelection)
        self.tree.itemSelectionChanged.connect(self._on_selection_changed)
        layout.addWidget(self.tree, 1)

        buttons = QtWidgets.QHBoxLayout()
        self.btn_add_fig = QtWidgets.QPushButton(localization.get_text("scene_tree_panel.buttons.add"))
        self.btn_add_grp = QtWidgets.QPushButton(localization.get_text("scene_tree_panel.buttons.add_group"))
        self.btn_dup = QtWidgets.QPushButton(localization.get_text("scene_tree_panel.buttons.duplicate"))
        self.btn_del = QtWidgets.QPushButton(localization.get_text("scene_tree_panel.buttons.delete"))
        buttons.addWidget(self.btn_add_fig)
        buttons.addWidget(self.btn_add_grp)
        buttons.addWidget(self.btn_dup)
        buttons.addWidget(self.btn_del)
        style_danger_button(self.btn_del)
        layout.addLayout(buttons)

        self.btn_add_fig.clicked.connect(self._on_add_figure)
        self.btn_add_grp.clicked.connect(self._on_add_group)
        self.btn_dup.clicked.connect(self._on_duplicate)
        self.btn_del.clicked.connect(self._on_delete)

        controller.tree_loaded.connect(lambda _id: self.reload())
        controller.node_structure_changed.connect(self.reload)
        controller.selection_list_changed.connect(self._on_external_selection)

    # ------------------------------------------------------------------
    # Построение дерева из params.scene_root
    # ------------------------------------------------------------------
    #region
    def reload(self) -> None:
        root = self.params.scene_root
        expanded_ids, known_ids = self._snapshot_expansion()
        self.tree.blockSignals(True)
        self.tree.clear()
        if root is None:
            self.root_db_id = None
            self.tree.blockSignals(False)
            return
        self.root_db_id = getattr(root, "db_id", None)
        root_item = self._build_item(root)
        self.tree.addTopLevelItem(root_item)
        # Раскрытие применяется только после вставки item'ов в дерево:
        # флаг expanded, установленный до вставки, теряется
        self._restore_expansion(root_item, expanded_ids, known_ids)
        self.tree.blockSignals(False)
        self._restore_selection()

        #region Хелперы
    def _snapshot_expansion(self) -> tuple:
        expanded, known = set(), set()

        def walk(item):
            db_id = item.data(0, ROLE_NODE_ID)
            if db_id is not None and db_id >= 0:
                known.add(db_id)
                if item.isExpanded():
                    expanded.add(db_id)
            for i in range(item.childCount()):
                walk(item.child(i))

        walk(self.tree.invisibleRootItem())
        return expanded, known

    def _restore_expansion(self, item, expanded_ids: set, known_ids: set) -> None:
        db_id = item.data(0, ROLE_NODE_ID)
        is_group = bool(item.data(0, ROLE_IS_GROUP))
        # Известные узлы сохраняют своё состояние, новые группы раскрываются
        item.setExpanded(db_id in expanded_ids or (is_group and db_id not in known_ids))
        for i in range(item.childCount()):
            self._restore_expansion(item.child(i), expanded_ids, known_ids)

        #endregion

    def _build_item(self, node) -> QtWidgets.QTreeWidgetItem:
        label = getattr(node, "name", "") or self._auto_label(node)
        item = QtWidgets.QTreeWidgetItem([label])
        item.setData(0, ROLE_NODE_ID, getattr(node, "db_id", -1))
        item.setData(0, ROLE_IS_GROUP, isinstance(node, GroupOperation))
        if isinstance(node, GroupOperation):
            for child in node.childs:
                child_item = self._build_item(child)
                item.addChild(child_item)
        return item

    @staticmethod
    def _auto_label(node) -> str:
        if isinstance(node, GroupOperation):
            op = node.op_type
            label = OP_TYPE_NAMES[op] if 0 <= op < len(OP_TYPE_NAMES) else f"Op {op}"
            if getattr(node, "unary_op_type", -1) != -1:
                u = node.unary_op_type
                if 0 <= u < len(OP_TYPE_NAMES):
                    label += f" [{OP_TYPE_NAMES[u]}]"
            return label
        if isinstance(node, TreeObject):
            base = node.base_object
            label = f"Figure {base.figure.type}" if base.figure else "Empty"
            if node.unary_op is not None:
                u = node.unary_op.op_type
                if 0 <= u < len(OP_TYPE_NAMES):
                    label += f" [{OP_TYPE_NAMES[u]}]"
            return label
        if isinstance(node, Operation):
            return f"Figure {node.figure.type}" if node.figure else "Empty"
        return "Node"

    #endregion
    # ------------------------------------------------------------------
    # Выделение (множественное; группа = всё поддерево — разворот в контроллере)
    # ------------------------------------------------------------------
    #region
    def _on_selection_changed(self) -> None:
        if self._restoring_selection:
            return
        self.controller.select_nodes(self._selected_ids())

    def _on_external_selection(self, db_ids) -> None:
        # Выделение пришло извне (инспектор, вьюпорт) — подсветим в дереве
        current_ids = {it.data(0, ROLE_NODE_ID) for it in self.tree.selectedItems()}
        if current_ids != set(db_ids or []):
            self._restore_selection()

    def _restore_selection(self) -> None:
        ids = list(getattr(self.controller, "selected_node_ids", None) or [])
        primary = self.controller.selected_node_id
        if not ids and primary is not None and primary >= 0:
            ids = [primary]
        self._restoring_selection = True
        try:
            self.tree.clearSelection()
            for db_id in ids:
                item = self._find_item(self.tree.invisibleRootItem(), db_id)
                if item is not None:
                    item.setSelected(True)
            if primary is not None and primary >= 0:
                item = self._find_item(self.tree.invisibleRootItem(), primary)
                if item is not None:
                    # current без изменения выделения
                    self.tree.setCurrentItem(
                        item, 0, QtCore.QItemSelectionModel.NoUpdate
                    )
                    # Раскрываем цепочку родителей, чтобы выделенный
                    # (например, только что добавленный) узел был виден
                    parent = item.parent()
                    while parent is not None:
                        parent.setExpanded(True)
                        parent = parent.parent()
        finally:
            self._restoring_selection = False

    def _find_item(self, parent: QtWidgets.QTreeWidgetItem,
                   db_id: int) -> Optional[QtWidgets.QTreeWidgetItem]:
        for i in range(parent.childCount()):
            item = parent.child(i)
            if item.data(0, ROLE_NODE_ID) == db_id:
                return item
            found = self._find_item(item, db_id)
            if found is not None:
                return found
        return None

    def _selected_ids(self) -> List[int]:
        ids = []
        for item in self.tree.selectedItems():
            db_id = item.data(0, ROLE_NODE_ID)
            if db_id is not None and db_id >= 0:
                ids.append(db_id)
        if ids:
            return ids
        fallback = self.controller.selected_node_id
        return [fallback] if fallback is not None and fallback >= 0 else []
    #endregion
    # ------------------------------------------------------------------
    # Кнопки
    # ------------------------------------------------------------------
    #region
    def _selected_or_root_id(self) -> Optional[int]:
        db_id = self.controller.selected_node_id
        node = self.controller.find_node(db_id)
        # Добавляем в выделенную группу; если выделен лист — в его родителя
        if isinstance(node, GroupOperation):
            return db_id
        if node is not None and getattr(node, "db_id", None) is not None:
            return self.controller.find_parent_db_id(db_id)
        return self.root_db_id

    def _on_add_figure(self) -> None:
        parent_id = self._selected_or_root_id()
        if parent_id is None:
            return
        node_id = self.controller.add_figure_node(parent_id, figure_type=0)
        self.controller.select_node(node_id)

    def _on_add_group(self) -> None:
        parent_id = self._selected_or_root_id()
        if parent_id is None:
            return

        items = [it for it in self.tree.selectedItems()
                 if it.data(0, ROLE_NODE_ID) not in (None, -1)
                 and it.data(0, ROLE_NODE_ID) != self.root_db_id]

        # Выделены и группа, и её потомок — переносится только группа
        def has_selected_ancestor(item):
            parent = item.parent()
            while parent is not None:
                if parent in items:
                    return True
                parent = parent.parent()
            return False

        items = [it for it in items if not has_selected_ancestor(it)]

        if len(items) < 2:
            node_id = self.controller.add_group_node(parent_id, optype=0)
            self.controller.select_node(node_id)
            return

        # Группируем только узлы с общим родителем; сравнение по db_id,
        # т.к. QTreeWidgetItem нехешируемый
        parent_ids = {
            (it.parent().data(0, ROLE_NODE_ID) if it.parent() is not None
             else self.root_db_id)
            for it in items
        }
        if len(parent_ids) != 1:
            QtWidgets.QMessageBox.information(
                self, "Создать группу",
                "Выделенные узлы должны быть в одной группе-родителе.")
            return

        parent_db_id = next(iter(parent_ids))
        parent_item = items[0].parent()
        container = (parent_item if parent_item is not None
                     else self.tree.invisibleRootItem())
        index = min(container.indexOfChild(it) for it in items)
        node_ids = [it.data(0, ROLE_NODE_ID)
                    for it in sorted(items, key=container.indexOfChild)]

        node_id = self.controller.group_nodes(parent_db_id, node_ids, index=index)
        self.controller.select_node(node_id)

    def _on_duplicate(self) -> None:
        items = self.tree.selectedItems()
        if not items:
            # запасной путь — текущий узел контроллера
            db_id = self.controller.selected_node_id
            item = (self._find_item(self.tree.invisibleRootItem(), db_id)
                    if db_id is not None and db_id >= 0 else None)
            items = [item] if item is not None else []

        items = [it for it in items
                 if it.data(0, ROLE_NODE_ID) not in (None, -1)
                 and it.data(0, ROLE_NODE_ID) != self.root_db_id]
        if not items:
            return

        # Выделены и группа, и её потомок — копируется только группа:
        # потомок уже войдёт в её копию
        def has_selected_ancestor(item):
            parent = item.parent()
            while parent is not None:
                if parent in items:
                    return True
                parent = parent.parent()
            return False

        items = [it for it in items if not has_selected_ancestor(it)]

        self.controller.duplicate_nodes([it.data(0, ROLE_NODE_ID) for it in items])

    def _on_delete(self) -> None:
        for db_id in self._selected_ids():
            if db_id == self.root_db_id:
                continue
            if self.controller.find_node(db_id) is None:
                continue  # уже удалён вместе с родителем при вложенном выделении
            self.controller.remove_node(db_id)

    #endregion