"""
SDForge: главный диалог.

Раскладка:
- слева — вкладки [Дерево сцены | Проекты];
- по центру — рендер (на всю высоту диалога);
- справа — вкладки [Инспектор | Рендер | Материалы | Рендер кадра].

Каждый блок — независимый класс, получает общий RenderPreviewParams и
SceneController (единственная точка доступа к БД).

Встроенные сущности пересоздаются из кода при каждом старте:
sync_builtin_tree пересоздаёт дерево "Default" (встроенное дерево не
переживает сессию), sync_builtin_materials upsert'ит материалы из
DEFAULT_MATERIALS в глобальную библиотеку.
"""

import os

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage

from krita import Krita

from ..config import log_debug
from ..core.render.shader_builder import SDFShaderManager
from .render_previewer import RenderPreviewParams, SDFRenderPreviewer
from .scene_controller import SceneController
from ..defaults import DEFAULT_MATERIALS, DEFAULT_GROUP_OPERATION

from .widgets.projects_panel import ProjectsPanel
from .widgets.scene_tree_panel import SceneTreePanel
from .widgets.inspector_panel import InspectorPanel
from .widgets.materials_panel import MaterialsPanel
from .widgets.render_settings_panel import RenderSettingsPanel
from .widgets.frame_export_panel import FrameExportPanel
from .widgets.texture_picker_panel import load_texture_image, probe_texture_size

from .. import localization


class SDForgeDialog(QtWidgets.QDialog):
    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("SDForge")
        self.setMinimumSize(1200, 500)

        # --- Общее состояние --- #
        self.render_params = RenderPreviewParams(self)
        self.render_params.set_chunk_interval_ms(1)
        self.render_params.set_frame_interval_ms(10)

        self.controller = SceneController(self.render_params, self)
        self.controller.texture_loader = load_texture_image
        self.controller.texture_size_probe = probe_texture_size

        # Встроенные SDF-типы регистрируем в БД. Инвариант:
        #   id типа в sdf_types == позиции имени в get_default_sdfs()
        #   == case-индексу, который сгенерирует билдер.
        # Upsert при каждом старте: builtin предустановлены ВСЕГДА, даже если
        # строки в таблице изменили или удалили вручную. Экземпляр менеджера
        # не нужен — get_default_sdfs() это @staticmethod.
        try:
            self.controller.repo.sync_builtin_sdf_types(
                SDFShaderManager.get_default_sdfs()
            )
        except Exception as e:
            log_debug(localization.get_text("main_dialog.init.sync_builtin_sdf_types.error.log_debug", error=str(e)))

        try:
            self.controller.repo.sync_builtin_materials(DEFAULT_MATERIALS)
            self.controller.repo.sync_builtin_tree("Default", DEFAULT_GROUP_OPERATION)
        except Exception as e:
            log_debug(localization.get_text("main_dialog.init.sync_builtin.error.log_debug", error=str(e)))

        # region --- Блоки основной раскладки --- #

            # region --- Левая колонка: сцена (файлы + дерево) и библиотека материалов --- #
        self.files_panel = ProjectsPanel(self.render_params, self.controller)
        self.tree_panel = SceneTreePanel(self.render_params, self.controller)

        scene_page = QtWidgets.QWidget()
        scene_layout = QtWidgets.QVBoxLayout(scene_page)
        scene_layout.setContentsMargins(0, 0, 0, 0)

        left_tabs = QtWidgets.QTabWidget()
        left_tabs.addTab(self.tree_panel, localization.get_text("main_dialog.init.tabs.scene_tree"))
        left_tabs.addTab(self.files_panel, localization.get_text("main_dialog.init.tabs.projects"))
        left_tabs.setMinimumWidth(250)

            # endregion

            # region --- Центр: превьювер на всю высоту диалога --- #
        self.render_previewer = SDFRenderPreviewer(self.render_params, self)
        self.render_previewer.setMinimumSize(256, 256)
        self.render_previewer.set_sdf_dict(
            self.controller.repo.sdf_dict_for_shader()
        )
            # endregion

            # region --- Правая колонка: инспектор и настройки рендера --- #
        self.inspector_panel = InspectorPanel(self.render_params, self.controller)
        self.render_settings_panel = RenderSettingsPanel(self.render_params,
                                                         self.controller)
        self.materials_panel = MaterialsPanel(self.render_params, self.controller)
        self.frame_export_panel = FrameExportPanel(self.render_params, self.controller, self.render_previewer)

        right_tabs = QtWidgets.QTabWidget()
        right_tabs.addTab(self.inspector_panel, localization.get_text("main_dialog.init.tabs.inspector"))
        # Настройки — вертикальная форма: в узкой колонке ей нужен скролл
        settings_scroll = QtWidgets.QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(self.render_settings_panel)
        right_tabs.addTab(settings_scroll, localization.get_text("main_dialog.init.tabs.render_settings"))
        right_tabs.addTab(self.materials_panel, localization.get_text("main_dialog.init.tabs.materials"))
        right_tabs.addTab(self.frame_export_panel, localization.get_text("main_dialog.init.tabs.frame_export"))
            #endregion

            # region --- колонки | превьювер | колонки --- #
        self.main_splitter = QtWidgets.QSplitter(Qt.Horizontal)
        self.main_splitter.addWidget(left_tabs)
        self.main_splitter.addWidget(self.render_previewer)
        self.main_splitter.addWidget(right_tabs)
        self.main_splitter.setStretchFactor(0, 2)
        self.main_splitter.setStretchFactor(1, 6)   # превьювер — главный приоритет
        self.main_splitter.setStretchFactor(2, 2)
        self.main_splitter.setSizes([250, 1000, 420])  # стартовые пропорции
        try:
            sizes = self.controller.repo.get_misc().get("main_splitter_sizes")
            if sizes:
                self.main_splitter.setSizes([int(v) for v in sizes.split(",")])
        except Exception:
            pass

        layout = QtWidgets.QVBoxLayout(self)

        history_row = QtWidgets.QHBoxLayout()

        self.undo_button = QtWidgets.QPushButton(localization.get_text("main_dialog.init.buttons.undo"))
        self.undo_button.setToolTip(localization.get_text("main_dialog.init.buttons.undo.tooltip"))
        self.undo_button.clicked.connect(self._undo_scene)
        history_row.addWidget(self.undo_button)

        self.redo_button = QtWidgets.QPushButton(localization.get_text("main_dialog.init.buttons.redo"))
        self.redo_button.setToolTip(localization.get_text("main_dialog.init.buttons.redo.tooltip"))
        self.redo_button.clicked.connect(self._redo_scene)
        history_row.addWidget(self.redo_button)

        history_row.addStretch(1)

        layout.addLayout(history_row)
        layout.addWidget(self.main_splitter, 1)

            # endregion

        # endregion

        # --- Открываем последнее дерево, а при пустой БД — сидим демо --- #
        QtCore.QTimer.singleShot(0, self._open_initial_tree)

    def _open_initial_tree(self) -> None:
        trees = self.controller.list_trees()
        if not trees:
            # sync не отработал (сбой БД) — крайний случай, пустое дерево
            tree_id = self.controller.create_tree(localization.get_text("main_dialog._open_initial_tree.default_tree_name"))
            self.controller.switch_tree(tree_id)
            return
        # Встроенная сцена пересоздаётся при старте со свежим updated_at —
        # "последним открытым" считаем только пользовательские деревья;
        # если их нет — откроется дефолтная сцена.
        user_trees = [t for t in trees if not t["is_builtin"]]
        pool = user_trees or trees
        latest = max(pool, key=lambda t: t["updated_at"])
        self.controller.open_tree(latest["id"])

    def _undo_scene(self):
        self.render_params.undo_scene()
        self.render_previewer.setFocus(Qt.OtherFocusReason)

    def _redo_scene(self):
        self.render_params.redo_scene()
        self.render_previewer.setFocus(Qt.OtherFocusReason)

    def closeEvent(self, event):
        self.controller.save_selected_scene()
        self.controller.save_all()
        self.render_params.scene_history.clear()
        try:
            self.controller.repo.update_misc({
                "main_splitter_sizes": ",".join(str(s) for s in self.main_splitter.sizes())
            })
        except Exception:
            pass
        super().closeEvent(event)
