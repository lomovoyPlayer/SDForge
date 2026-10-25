"""
SDForge: панель "файлов" — список деревьев объектов(сцен/проектов).

Независимый блок: получает (params, controller), ничего не знает о других
панелях. Все изменения — через controller, обновление списка — по сигналам
tree_list_changed / tree_loaded.
"""

from typing import Optional

from PyQt5 import QtWidgets
from PyQt5.QtCore import Qt

from ..ui_style import style_danger_button, tune_layout

from ..scene_controller import SceneController
from ..render_previewer import RenderPreviewParams

from ... import localization


class ProjectsPanel(QtWidgets.QWidget):
    """Панель "файлов" — список деревьев объектов(сцен/проектов)."""
    def __init__(self, params: "RenderPreviewParams", controller: "SceneController", parent=None):
        super().__init__(parent)
        self.params = params
        self.controller = controller

        layout = QtWidgets.QVBoxLayout(self)
        tune_layout(layout)

        layout.addWidget(QtWidgets.QLabel(localization.get_text("projects_panel.label")))

        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.currentRowChanged.connect(self._on_current_row)
        layout.addWidget(self.list_widget, 1)

        buttons = QtWidgets.QGridLayout()

        self.btn_new = QtWidgets.QPushButton(localization.get_text("projects_panel.buttons.new"))
        self.btn_dup = QtWidgets.QPushButton(localization.get_text("projects_panel.buttons.duplicate"))
        self.btn_rename = QtWidgets.QPushButton(localization.get_text("projects_panel.buttons.rename"))
        self.btn_del = QtWidgets.QPushButton(localization.get_text("projects_panel.buttons.delete"))
        style_danger_button(self.btn_del)
        
        buttons.addWidget(self.btn_new, 0, 0)
        buttons.addWidget(self.btn_dup, 0, 1)
        buttons.addWidget(self.btn_rename, 1, 0)
        buttons.addWidget(self.btn_del, 1, 1)
        
        layout.addLayout(buttons)


        self.btn_new.clicked.connect(self._on_new)
        self.btn_dup.clicked.connect(self._on_duplicate)
        self.btn_rename.clicked.connect(self._on_rename)
        self.btn_del.clicked.connect(self._on_delete)

        controller.tree_list_changed.connect(self.reload)
        controller.tree_loaded.connect(self._on_tree_loaded)

        self.reload()

    # ------------------------------------------------------------------
    # Список
    # ------------------------------------------------------------------
    #region
    def reload(self) -> None:
        current_id = self.controller.current_tree_id
        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for tree in self.controller.list_trees():
            item = QtWidgets.QListWidgetItem(
                f"{tree['name']}  ({tree['node_count']})"
            )
            item.setData(Qt.UserRole, tree["id"])
            self.list_widget.addItem(item)
            if tree["id"] == current_id:
                self.list_widget.setCurrentItem(item)
        self.list_widget.blockSignals(False)

    def selected_tree_id(self) -> Optional[int]:
        item = self.list_widget.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _on_current_row(self, row: int) -> None:
        if row < 0:
            return
        item = self.list_widget.item(row)
        if item is None:
            return
        self.controller.switch_tree(item.data(Qt.UserRole))

    def _on_tree_loaded(self, tree_id: int) -> None:
        # Открытие могло произойти извне панели — подсветим актуальный файл
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.data(Qt.UserRole) == tree_id:
                self.list_widget.blockSignals(True)
                self.list_widget.setCurrentItem(item)
                self.list_widget.blockSignals(False)
                break

    #endregion
    # ------------------------------------------------------------------
    # Кнопки
    # ------------------------------------------------------------------
    #region
    def _on_new(self) -> None:
        name, ok = QtWidgets.QInputDialog.getText(self, localization.get_text("projects_panel.on_new.title"), 
                                                  localization.get_text("projects_panel.on_new.text"))
        if not ok or not name.strip():
            return
        if self._is_existing_tree_name(name.strip()):
            QtWidgets.QMessageBox.warning(
                self, localization.get_text("projects_panel.on_new.title"), 
                localization.get_text("projects_panel.on_new.warning.existing_name.text")
            )
            return
        tree_id = self.controller.create_tree(name.strip())
        self.controller.switch_tree(tree_id)

    def _on_duplicate(self) -> None:
        tree_id = self.selected_tree_id()
        if tree_id is None:
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self, localization.get_text("projects_panel.on_duplicate.title"), 
            localization.get_text("projects_panel.on_duplicate.text")
        )
        if not ok or not name.strip():
            return
        new_id = self.controller.duplicate_tree(tree_id, name.strip())
        self.controller.switch_tree(new_id)

    def _on_rename(self) -> None:
        tree_id = self.selected_tree_id()
        item = self.list_widget.currentItem()
        if tree_id is None or item is None:
            return
        name, ok = QtWidgets.QInputDialog.getText(
            self, localization.get_text("projects_panel.on_rename.title"), 
            localization.get_text("projects_panel.on_rename.text"), text=item.text().rsplit("  (", 1)[0]
        )
        if ok and name.strip():
            self.controller.rename_tree(tree_id, name.strip())

    def _on_delete(self) -> None:
        tree_id = self.selected_tree_id()
        item = self.list_widget.currentItem()
        if tree_id is None or item is None:
            return
        answer = QtWidgets.QMessageBox.question(
            self, localization.get_text("projects_panel.on_delete.title"),
            localization.get_text("projects_panel.on_delete.text", name=item.text())
        )
        if answer == QtWidgets.QMessageBox.Yes:
            self.controller.delete_tree(tree_id)

    def _is_existing_tree_name(self, name: str) -> bool:
        for tree in self.controller.list_trees():
            if tree["name"] == name:
                return True
        return False

    #endregion