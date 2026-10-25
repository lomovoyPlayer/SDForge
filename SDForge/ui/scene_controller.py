"""
SDForge: контроллер сцены — единственная точка общения UI с Repository.

Паттерн: блоки интерфейса независимы друг от друга, каждый получает
(params, controller). Блоки:
- вызывают методы контроллера для любых изменений данных;
- подписываются на его сигналы и на сигналы RenderPreviewParams.

Потоки данных:
  открытие дерева:  repo -> controller -> params.set_scene_root -> renderer
  правка узла:      инспектор мутирует объект -> controller.commit_node
                    -> БД (1 строка) + params.emit_scene_changed()
  структура:        controller.add/remove/move -> БД + rebuild дерева
                    -> node_structure_changed -> tree panel перечитывает
                    params.scene_root
  материалы:        panels -> controller.add/commit/remove_material
                    -> БД + params.materials перечитан -> materials_changed
  текстуры:         inspector -> controller.register_texture
                    -> реестр + texture_images пересобраны -> textures_changed

Важно: после любого структурного изменения дерево в памяти пересоздаётся
из БД, поэтому все связи между объектами ищутся по db_id, а не по
идентичности объектов.
"""

import os
from typing import Any, Callable, Dict, List, Optional

from PyQt5 import QtCore
from PyQt5.QtCore import pyqtSignal

from .render_previewer import RenderPreviewParams

from ..config import log_debug
from ..core.data_classes import Figure, GroupOperation, Material, Operation, TreeObject
from ..core.database.repository import KIND_FIGURE, KIND_GROUP, Repository
from .. import localization


class SceneController(QtCore.QObject):
    tree_list_changed = pyqtSignal()
    tree_loaded = pyqtSignal(int)            # tree_id
    node_structure_changed = pyqtSignal()    # add/remove/move
    node_updated = pyqtSignal(int)           # db_id
    selection_changed = pyqtSignal(int)      # db_id, -1 = ничего
    selection_list_changed = pyqtSignal(list)
    materials_changed = pyqtSignal()
    textures_changed = pyqtSignal()

    def __init__(self, params: "RenderPreviewParams", parent=None):
        super().__init__(parent)
        self.params = params
        self.repo = Repository()
        self.params.history_restored.connect(self._on_history_restored)
        self.current_tree_id: Optional[int] = None
        self.selected_node_id: int = -1
        self.selected_node_ids: List[int] = []
        # Хук: (filename) -> QImage|None. Инжектируется из UI (знает про Krita).
        self.texture_loader: Optional[Callable[[str], Any]] = None

        # Хук: (filename) -> (w, h)|None — реальный размер контента в слое.
        self.texture_size_probe: Optional[Callable[[str], Any]] = None


    # ------------------------------------------------------------------
    # Загрузка дерева в params
    # ------------------------------------------------------------------
    #region
    def open_tree(self, tree_id: int) -> None:
        root = self.repo.load_tree(tree_id)
        if root is None:
            log_debug(localization.get_text("scene_controller.open_tree.no_root.log_debug", tree_id=tree_id))
            self.repo.add_node(tree_id, None, KIND_GROUP, optype=0)
            root = self.repo.load_tree(tree_id)

        # Текстуры: имена файлов -> runtime id (индекс в texture_images).
        id_by_filename = self._rebuild_texture_images()
        root = self.params.scene_history.activate(tree_id, root)
        self._apply_textures_recursive(root, id_by_filename)

        materials = [m["material"] for m in self.repo.list_materials()]
        self.params.set_scene_root(
            scene_root=root,
            materials=materials,
            texture_images=getattr(self.params, "texture_images", []),
            rebuild=True,
            emit=False,  # сначала применим настройки, потом один emit
        )
        settings = self.repo.get_tree_settings(tree_id)
        self.repo.apply_settings_to_params(self.params, settings)

        self.current_tree_id = tree_id
        self.select_node(getattr(root, "db_id", -1))
        self.params.emit_scene_changed(record_history=False)
        self.tree_loaded.emit(tree_id)

    def _on_history_restored(self) -> None:
        """Обновить панели и восстановить выделение по стабильным db_id."""
        if self.current_tree_id is None:
            return
    
        # Undo/redo подменили дерево в памяти — приводим БД к тому же состоянию.
        # db_id при этом сохраняются, поэтому выделение и панели не ломаются.
        root = self.params.scene_root
        if root is not None:
            self.repo.replace_tree(self.current_tree_id, root)
    
        selected = [
            db_id for db_id in self.selected_node_ids
            if self.find_node(db_id) is not None
        ]
        self.node_structure_changed.emit()

        if selected:
            self.select_nodes(selected)
        else:
            root_id = getattr(self.params.scene_root, "db_id", -1)
            self.select_node(root_id)

    def _apply_textures_recursive(self, node, id_by_filename: Dict[str, int]) -> None:
        if isinstance(node, GroupOperation):
            for child in node.childs:
                self._apply_textures_recursive(child, id_by_filename)
        elif isinstance(node, TreeObject):
            if node.base_object.figure is not None:
                self.repo.apply_resolved_textures(node.base_object.figure, id_by_filename)
        elif isinstance(node, Operation) and node.figure is not None:
            self.repo.apply_resolved_textures(node.figure, id_by_filename)

    def _rebuild_texture_images(self) -> Dict[str, int]:
        """Синхронизирует params.texture_images/texture_filenames с реестром
        текстур. Возвращает {filename: runtime_id}. Файлы, которые
        texture_loader не смог загрузить, пропускаются (слоты фигур с ними
        будут выключены — см. apply_resolved_textures)."""
        images: List[Any] = []
        filenames: List[str] = []
        id_by_filename: Dict[str, int] = {}
        for tex in self.repo.list_textures():
            image = None
            if self.texture_loader is not None:
                try:
                    image = self.texture_loader(tex["filename"])
                except Exception as e:
                    log_debug(localization.get_text("scene_controller._rebuild_texture_images.loader_failed.error.log_debug", filename=tex["filename"], error=e))
            if image is None:
                log_debug(localization.get_text("scene_controller._rebuild_texture_images.texture_unresolved.log_debug", filename=tex["filename"]))
                continue
            id_by_filename[tex["filename"]] = len(images)
            images.append(image)
            filenames.append(tex["filename"])
        self.params.texture_images = images
        # Параллельный список имён — нужен для сохранения слотов по файлу.
        self.params.texture_filenames = filenames
        return id_by_filename

    def close_current_tree(self) -> None:
        """Сохраняет настройки текущего дерева (камера, рендер)."""
        if self.current_tree_id is None:
            return
        self.repo.update_tree_settings(
            self.current_tree_id,
            self.repo.settings_from_params(self.params),
        )

    def switch_tree(self, tree_id: int) -> None:
        if tree_id == self.current_tree_id:
            return
        self.close_current_tree()
        self.open_tree(tree_id)

    #endregion
    # ------------------------------------------------------------------
    # Деревья
    # ------------------------------------------------------------------
    #region
    def list_trees(self) -> List[Dict[str, Any]]:
        return self.repo.list_trees()

    def create_tree(self, name: str) -> int:
        # Настройки нового дерева наследуем от текущего состояния viewport'а
        settings = self.repo.settings_from_params(self.params)
        tree_id = self.repo.create_tree(name, settings=settings)
        self.tree_list_changed.emit()
        return tree_id

    def duplicate_tree(self, tree_id: int, new_name: str) -> int:
        if tree_id == self.current_tree_id:
            self.close_current_tree()
        new_id = self.repo.duplicate_tree(tree_id, new_name)
        self.tree_list_changed.emit()
        return new_id

    def rename_tree(self, tree_id: int, name: str) -> None:
        self.repo.rename_tree(tree_id, name)
        self.tree_list_changed.emit()

    def delete_tree(self, tree_id: int) -> None:
        self.repo.delete_tree(tree_id)
        self.params.scene_history.forget(tree_id)
        if tree_id == self.current_tree_id:
            self.current_tree_id = None
            self.params.set_scene_root(None, rebuild=True, emit=True)
            remaining = self.repo.list_trees()
            if remaining:
                # Открываем следующее дерево вместо удалённого
                self.open_tree(remaining[0]["id"])
        self.tree_list_changed.emit()

    #endregion
    # ------------------------------------------------------------------
    # Материалы (глобальная библиотека)
    # ------------------------------------------------------------------
    #region
    def add_material(self, name: str = "") -> int:
        db_id = self.repo.add_material(Material(), name=name)
        self._reload_materials()
        return db_id

    def commit_material(self, material_db_id: int, *,
                        material: Optional[Material] = None,
                        name: Optional[str] = None) -> None:
        """Правка параметров/имени материала. Порядок и состав библиотеки
        не меняются — runtime-индексы фигур остаются валидны, дерево
        перезагружать не нужно."""
        self.repo.update_material(material_db_id, material=material, name=name)
        self._reload_materials()

    def remove_material(self, material_db_id: int) -> None:
        """Удаление смещает runtime-индексы — дерево перезагружается,
        фигуры получают новые индексы по стабильным id."""
        self.repo.remove_material(material_db_id)
        self._reload_materials()
        if self.current_tree_id is not None:
            self._reload_structure()

    def reorder_material(self, material_db_id: int, index: int) -> None:
        self.repo.reorder_material(material_db_id, index)
        self._reload_materials()
        if self.current_tree_id is not None:
            self._reload_structure()

    def is_material_in_use(self, material_db_id: int) -> bool:
        return self.repo.is_material_in_use(material_db_id)

    def _reload_materials(self) -> None:
        """Перечитывает библиотеку в params.materials (новые объекты —
        панели редактируют свои копии и коммитят через commit_material)."""
        self.params.materials = [m["material"] for m in self.repo.list_materials()]
        self.params.emit_scene_changed()
        self.materials_changed.emit()

    def duplicate_material(self, material_db_id: int) -> int:
        """Копия встаёт в середину списка — runtime-индексы смещаются,
        дерево перезагружается, фигуры получают новые индексы по стабильным id."""
        new_id = self.repo.duplicate_material(material_db_id)
        self._reload_materials()
        if self.current_tree_id is not None:
            self._reload_structure()
        return new_id
    
    #endregion
    # ------------------------------------------------------------------
    # Текстуры (глобальный реестр)
    # ------------------------------------------------------------------
    #region
    def register_texture(self, path: str, name: str = "") -> int:
        """Регистрирует файл/URI в реестре (идемпотентно) и пересобирает
        runtime-таблицу текстур. Возвращает стабильный id записи."""
        tex_id = self.repo.register_texture(path, name=name or os.path.basename(path))
        self.reload_textures()
        return tex_id

    def remove_texture(self, texture_db_id: int) -> None:
        self.repo.remove_texture(texture_db_id)
        self.reload_textures()

    def reload_textures(self) -> None:
        """Runtime-id текстур могли сместиться — пересобираем
        texture_images и переприменяем слоты всех фигур текущего дерева."""
        id_by_filename = self._rebuild_texture_images()
        root = self.params.scene_root
        if root is not None:
            self._apply_textures_recursive(root, id_by_filename)
        self.params.emit_scene_changed()
        self.textures_changed.emit()

    #endregion
    # ------------------------------------------------------------------
    # Узлы: структура
    # ------------------------------------------------------------------
    #region
    def add_figure_node(self, parent_db_id: int, figure_type: int = 0,
                        name: str = "", index: Optional[int] = None,
                        op_type: int = 0) -> int:
        fig = Figure(figure_type=figure_type)
        fig.payload_extra = {}
        node_id = self.repo.add_node(
            self.current_tree_id, parent_db_id, KIND_FIGURE,
            name=name, figure=fig, index=index, optype=op_type,
        )
        self._reload_structure()
        return node_id

    def add_group_node(self, parent_db_id: int, optype: int = 0,
                       name: str = "", index: Optional[int] = None) -> int:
        node_id = self.repo.add_node(
            self.current_tree_id, parent_db_id, KIND_GROUP,
            name=name, optype=optype, index=index,
        )
        self._reload_structure()
        return node_id

    def move_node(self, node_db_id: int, new_parent_db_id: int,
                  index: Optional[int] = None) -> None:
        self.repo.move_node(node_db_id, new_parent_db_id, index)
        self._reload_structure()

    def group_nodes(self, parent_db_id: int, node_ids: List[int],
                    index: Optional[int] = None, name: str = "") -> int:
        """Новая группа в parent_db_id на позиции index; перечисленные узлы
        переносятся в неё в переданном порядке. Перезагрузка — одна, в конце."""
        node_id = self.repo.add_node(
            self.current_tree_id, parent_db_id, KIND_GROUP,
            name=name, optype=0, index=index,
        )
        for nid in node_ids:
            self.repo.move_node(nid, node_id, None)
        self._reload_structure()
        return node_id

    def remove_node(self, node_db_id: int) -> None:
        # Дерево в памяти будет пересоздано (новые объекты), поэтому каскад
        # потомков собираем по db_id снапшота ДО удаления.
        doomed = {node_db_id} | self._collect_db_ids(self.find_node(node_db_id))
        parent_db_id = self.find_parent_db_id(node_db_id)
        self.repo.remove_node(node_db_id)
        if self.selected_node_id in doomed:
            self.selected_node_id = parent_db_id if parent_db_id is not None else -1
        self.selected_node_ids = [i for i in self.selected_node_ids if i not in doomed]
        self._reload_structure()

    def _reload_structure(self) -> None:
        """Структура изменилась — перечитываем дерево из БД (дешёво: один
        SELECT) и уведомляем рендерер и панели. Объекты пересоздаются,
        db_id узлов при этом сохраняются (они из таблицы nodes)."""
        root = self.repo.load_tree(self.current_tree_id)
        id_by_filename = self._filename_to_id_map()
        self._apply_textures_recursive(root, id_by_filename)
        self.params.set_scene_root(root, rebuild=True, emit=True)
        self.node_structure_changed.emit()
        self.selection_changed.emit(self.selected_node_id)
        self.selection_list_changed.emit(list(self.selected_node_ids))

    def _filename_to_id_map(self) -> Dict[str, int]:
        filenames = getattr(self.params, "texture_filenames", []) or []
        return {name: i for i, name in enumerate(filenames)}

    def duplicate_node(self, node_db_id: int) -> int:
        new_id = self.repo.duplicate_node(node_db_id)
        self._reload_structure()
        self.select_node(new_id)
        return new_id

    def duplicate_nodes(self, node_db_ids: List[int]) -> List[int]:
        """Глубокие копии узлов, каждая — сразу после своего источника.
        Перезагрузка — одна, в конце. Возвращает id копий в том же порядке."""
        new_ids = [self.repo.duplicate_node(nid) for nid in node_db_ids]
        self._reload_structure()
        self.select_nodes(new_ids)
        return new_ids

    #endregion
    # ------------------------------------------------------------------
    # Узлы: правка параметров (без перестройки структуры)
    # ------------------------------------------------------------------
    #region
    def commit_node(self, node, *, name: Optional[str] = None) -> None:
        """Сохраняет параметры узла из памяти в БД (1 строка) и просит
        рендерер перерисоваться. Структура не меняется, db_id не трогаем."""
        db_id = getattr(node, "db_id", None)
        if db_id is None:
            log_debug(localization.get_text("scene_controller.commit_node.no_db_id.log_debug"))
            return
        node_name = name if name is not None else getattr(node, "name", None)
        if isinstance(node, GroupOperation):
            self.repo.update_node(
                db_id, name=node_name, optype=int(node.op_type),
                operands=[float(v) for v in node.operands],
                unary_optype=int(node.unary_op_type),
                unary_operands=[float(v) for v in node.unary_operands],
            )
        elif isinstance(node, TreeObject):
            base = node.base_object
            fig = base.figure
            if fig is not None:
                self.repo.apply_resolved_textures(fig, self._filename_to_id_map())
                self.repo.update_node(
                    db_id, name=node_name, figure=fig,
                    optype=int(base.op_type),
                    operands=[float(v) for v in base.operands],
                    unary_optype=int(node.unary_op.op_type) if node.unary_op is not None else -1,
                    unary_operands=([float(v) for v in node.unary_op.operands]
                                    if node.unary_op is not None else [0.0] * 4),
                )
            else:
                self.repo.update_node(db_id, name=node_name)
        elif isinstance(node, Operation):
            # Прямой коммит base_object (текстурная панель):
            # унарные поля узла здесь не затрагиваются
            fig = node.figure
            if fig is not None:
                self.repo.apply_resolved_textures(fig, self._filename_to_id_map())
                self.repo.update_node(
                    db_id, name=node_name, figure=fig,
                    optype=int(node.op_type),
                    operands=[float(v) for v in node.operands],
                )
            else:
                self.repo.update_node(db_id, name=node_name)

        self.params.emit_scene_changed()
        self.node_updated.emit(db_id)

    #endregion
    # ------------------------------------------------------------------
    # Поиск по db_id (единственный надёжный способ — объекты пересоздаются)
    # ------------------------------------------------------------------
    #region
    def find_node(self, db_id: int):
        """Объект узла по db_id в текущем scene_root."""
        root = self.params.scene_root
        if root is None or db_id is None or db_id < 0:
            return None

        def walk(node):
            if getattr(node, "db_id", None) == db_id:
                return node
            if isinstance(node, GroupOperation):
                for child in node.childs:
                    found = walk(child)
                    if found is not None:
                        return found
            return None

        return walk(root)

    def find_parent_db_id(self, db_id: int) -> Optional[int]:
        """db_id родителя узла (None, если узел — корень или не найден)."""
        root = self.params.scene_root
        if root is None:
            return None

        def walk(parent):
            for child in parent.childs:
                if getattr(child, "db_id", None) == db_id:
                    return getattr(parent, "db_id", None)
                if isinstance(child, GroupOperation):
                    found = walk(child)
                    if found is not None:
                        return found
            return None

        if not isinstance(root, GroupOperation):
            return None
        return walk(root)

    @staticmethod
    def _collect_db_ids(node) -> set:
        """db_id узла и всех его потомков (для каскадных операций)."""
        ids = set()
        if node is None:
            return ids
        db_id = getattr(node, "db_id", None)
        if db_id is not None:
            ids.add(db_id)
        if isinstance(node, GroupOperation):
            for child in node.childs:
                ids |= SceneController._collect_db_ids(child)
        return ids

    #endregion
    # ------------------------------------------------------------------
    # Выделение
    # ------------------------------------------------------------------
    #region
    def select_node(self, db_id: int) -> None:
        self.select_nodes([db_id] if db_id is not None and db_id >= 0 else [])

    def select_nodes(self, db_ids: List[int]) -> None:
        """Мультивыделение. Группа разворачивается во все листья-фигуры ниже
        по иерархии включительно — их индексы уходят в
        params.selected_operation_indices, поэтому групповые трансформации
        вьюпорта работают на всё поддерево."""
        seen: set = set()
        unique_ids = [d for d in db_ids if d not in seen and not seen.add(d)]
        figures = []
        for db_id in unique_ids:
            figures.extend(self._iter_leaf_figures(self.find_node(db_id)))
        index_by_fig = {
            id(op.figure): i
            for i, op in enumerate(self.params.operations or [])
            if op is not None and op.figure is not None
        }
        indices = [index_by_fig[id(f)] for f in figures if id(f) in index_by_fig]
        self.params.set_selected_operation_indices(indices, emit=True)
        self.selected_node_ids = unique_ids
        self.selected_node_id = unique_ids[-1] if unique_ids else -1
        self.selection_changed.emit(self.selected_node_id)
        self.selection_list_changed.emit(list(unique_ids))

    @staticmethod
    def _iter_leaf_figures(node) -> list:
        """Все фигуры поддерева узла; для листа — он сам."""
        if node is None:
            return []
        if isinstance(node, TreeObject):
            fig = node.base_object.figure
            return [fig] if fig is not None else []
        if isinstance(node, Operation):
            return [node.figure] if node.figure is not None else []
        if isinstance(node, GroupOperation):
            result = []
            for child in node.childs:
                result.extend(SceneController._iter_leaf_figures(child))
            return result
        return []

    def selected_node(self):
        return self.find_node(self.selected_node_id)

    #endregion
    # ------------------------------------------------------------------
    # Библиотеки (для панелей)
    # ------------------------------------------------------------------
    #region
    def materials(self) -> List[Dict[str, Any]]:
        return self.repo.list_materials()

    def textures(self) -> List[Dict[str, Any]]:
        return self.repo.list_textures()

    def sdf_types(self) -> List[Dict[str, Any]]:
        return self.repo.list_sdf_types()

    def save_selected_scene(self) -> None:
        """Записать выбранную в памяти версию дерева перед завершением сессии."""
        if self.current_tree_id is None or self.params.scene_root is None:
            return
        self.repo.replace_tree(self.current_tree_id, self.params.scene_root)

    def save_all(self) -> None:
        """Вызывать при закрытии диалога."""
        self.close_current_tree()

    #endregion