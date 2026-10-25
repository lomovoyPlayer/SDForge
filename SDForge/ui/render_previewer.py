"""
SDForge: состояние сцены, история правок и вьюпорт.

Три класса:
- RenderPreviewParams — общее состояние: сцена (scene_root, materials,
  texture_images), камера, настройки рендера и управления, выделение.
  Настройки хранятся двумя словарями: render_settings (ключи совпадают с
  DEFAULT_TREE_SETTINGS, сохраняются в дерево) и control_settings
  (глобальные, в meta). Сигналы changed / scene_changed / shader_changed /
  history_restored управляют перерисовкой и обновлением панелей;
- SceneHistory — снимки GroupOperation для undo/redo, отдельно по id
  дерева, с заморозкой составных правок;
- SDFRenderPreviewer — виджет вьюпорта: ввод мыши (режимы камера/объект),
  таймер кадров, сплеш загрузки и ошибки шейдера, экспорт кадра.

Поток данных: панели и контроллер пишут в params -> сигналы params ->
рендерер (GPURenderer) перечитывает params и перерисовывает кадр.
"""

import math
import time
import copy

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import pyqtSignal
from PyQt5.QtGui import QColor, QImage, QPainter, QFont, QFontMetrics, QKeySequence

from ..core.math_utils import *
from PyQt5.QtGui import QVector3D, QVector2D
from ..core.render.shader_builder import SDFShaderManager
from ..core.data_classes import Figure, Operation, TreeObject, GroupOperation, Material, UNARY_OP_TYPE_NAMES, BINARY_OP_TYPE_NAMES
from ..core.render.renderer_mgl import GPURenderer
from ..config import SDF_FRAG_PATH, SDF_VERT_PATH, SDF_FRAG_FAST_PATH, log_debug, DEBUG_LOG_ENABLED
from .. import localization

# Индексы фигур дефолтного словаря — единственное место,
# где допустимо «знать» числа. Требует, чтобы get_default_sdfs
# не зависел от экземпляра — он объявлен @staticmethod.
DEFAULT_FIGURE_INDEX = {
    name: i for i, name in enumerate(SDFShaderManager.get_default_sdfs().keys())
}
# Id'ы операций выводятся из списков имён, чтобы не зависеть от нумерации
OP_UNION = BINARY_OP_TYPE_NAMES.index("Union")
OP_ROUND = len(BINARY_OP_TYPE_NAMES) + UNARY_OP_TYPE_NAMES.index("Round")
OP_SHELL = len(BINARY_OP_TYPE_NAMES) + UNARY_OP_TYPE_NAMES.index("Shell")


def _read_text_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

class SceneHistory:
    """Снимки GroupOperation в памяти, отдельно для каждого ID проекта."""

    def __init__(self, max_states: int | None = 100):
        if max_states is not None and max_states < 2:
            raise ValueError(localization.get_text("render_previewer.scene_history.init.error.max_states"))

        self.history: dict[object, list[GroupOperation]] = {}
        self.index: dict[object, int] = {}
        self._signatures: dict[object, list[object]] = {}

        self.project_id = None
        self._active = False
        self._freeze_depth = 0
        self._dirty = False
        self.max_states = max_states

    @property
    def frozen(self) -> bool:
        return self._freeze_depth > 0

    @staticmethod
    def _signature(value, ancestors=None):
        """Сравнить содержимое дерева, не обходя циклические parent-ссылки."""
        if value is None or isinstance(value, (str, int, float, bool, bytes)):
            return value

        if ancestors is None:
            ancestors = set()

        marker = id(value)
        if marker in ancestors:
            return ("cycle",)

        ancestors.add(marker)
        try:
            if isinstance(value, (list, tuple)):
                return (
                    type(value).__name__,
                    tuple(SceneHistory._signature(v, ancestors) for v in value),
                )

            if isinstance(value, dict):
                return (
                    "dict",
                    tuple(sorted(
                        (
                            (repr(key), SceneHistory._signature(item, ancestors))
                            for key, item in value.items()
                        ),
                        key=lambda pair: pair[0],
                    )),
                )

            if isinstance(value, set):
                return (
                    "set",
                    tuple(sorted(
                        (SceneHistory._signature(v, ancestors) for v in value),
                        key=repr,
                    )),
                )

            if hasattr(value, "__dict__"):
                return (
                    type(value).__name__,
                    tuple(sorted(
                        (
                            name,
                            SceneHistory._signature(item, ancestors),
                        )
                        for name, item in vars(value).items()
                        if name not in ("parent", "trajectory_fn")
                    )),
                )

            return (type(value).__name__, repr(value))
        finally:
            ancestors.remove(marker)

    def activate(self, project_id, root: GroupOperation | None = None):
        """Выбрать проект; вернуть его текущий снимок, если история уже есть."""
        if self.frozen:
            raise RuntimeError(localization.get_text("render_previewer.scene_history.activate.error.frozen"))

        self.project_id = project_id
        self._active = True

        if project_id in self.history:
            return self.current()

        if root is not None:
            self.record(root)
        return root

    def current(self):
        if not self._active or self.project_id not in self.index:
            return None
        return copy.deepcopy(self.history[self.project_id][self.index[self.project_id]])

    def record(self, root: GroupOperation | None) -> bool:
        """Сохранить снимок; после undo удалить состояния правее курсора."""
        if not self._active or root is None:
            return False

        if self.frozen:
            self._dirty = True
            return False

        key = self.project_id
        signature = self._signature(root)

        if key not in self.history:
            self.history[key] = [copy.deepcopy(root)]
            self._signatures[key] = [signature]
            self.index[key] = 0
            return True

        position = self.index[key]
        if signature == self._signatures[key][position]:
            return False

        del self.history[key][position + 1:]
        del self._signatures[key][position + 1:]

        self.history[key].append(copy.deepcopy(root))
        self._signatures[key].append(signature)

        if self.max_states is not None and len(self.history[key]) > self.max_states:
            self.history[key].pop(0)
            self._signatures[key].pop(0)

        self.index[key] = len(self.history[key]) - 1
        return True

    def begin_batch(self, root: GroupOperation | None):
        """Начать объединение множества обновлений в один шаг."""
        if self._freeze_depth == 0 and root is not None:
            self.record(root)
        self._freeze_depth += 1

    def end_batch(self, root: GroupOperation | None) -> bool:
        """Снять заморозку и записать итог, если была правка."""
        if self._freeze_depth == 0:
            return False

        self._freeze_depth -= 1
        if self._freeze_depth:
            return False

        dirty = self._dirty
        self._dirty = False
        return self.record(root) if dirty else False

    def undo(self):
        if (
            not self._active
            or self.frozen
            or self.index.get(self.project_id, 0) == 0
        ):
            return None

        self.index[self.project_id] -= 1
        return self.current()

    def redo(self):
        key = self.project_id
        if (
            not self._active
            or self.frozen
            or key not in self.history
            or self.index[key] >= len(self.history[key]) - 1
        ):
            return None

        self.index[key] += 1
        return self.current()

    def forget(self, project_id):
        """Удалить историю удалённого проекта."""
        self.history.pop(project_id, None)
        self.index.pop(project_id, None)
        self._signatures.pop(project_id, None)

        if self._active and self.project_id == project_id:
            self.project_id = None
            self._active = False
            self._freeze_depth = 0
            self._dirty = False

    def clear(self):
        self.history.clear()
        self.index.clear()
        self._signatures.clear()
        self.project_id = None
        self._active = False
        self._freeze_depth = 0
        self._dirty = False
        
class RenderPreviewParams(QtCore.QObject):

    changed = pyqtSignal()

    scene_changed = pyqtSignal()

    history_restored = pyqtSignal()

    shader_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)

        self.render_settings: dict[str, object] = {}
        self.control_settings: dict[str, object] = {}

        def extract_name(raw_name: str) -> str:
            return raw_name.split("=")[0].rsplit(".", 1)[-1]

        #region внутренние настройки
        self.frame_interval_ms = 33
        self.quality_chunk_interval_ms = 10
        self.fast_chunk_interval_ms = 1

        self.quality_chunk_size = 64
        self.fast_chunk_size = 128

        self.quality_preview_scale = 1.0
        self.fast_preview_scale = 2.0

        self.fast_mode_hold_ms = 180

        self.time_scale = 1.0
        self.auto_time = True
        self.time_value = 0.0
        #endregion

        #region Настройки камеры и запись в render_settings
        self.camera_pos = [0.0, 2.0, 6.0]
        self.camera_rot = [0.0, 0.0, 0.0]
        self.camera_fov_deg = 45.0

        self.render_settings[f"{self.camera_pos=}".split("=")[0].rsplit(".", 1)[-1]] = self.camera_pos
        self.render_settings[f"{self.camera_rot=}".split("=")[0].rsplit(".", 1)[-1]] = self.camera_rot
        self.render_settings[f"{self.camera_fov_deg=}".split("=")[0].rsplit(".", 1)[-1]] = self.camera_fov_deg
        #endregion

        #region Настройки сцены и рендера и запись в render_settings
        self.ambient_color = [0.18, 0.20, 0.24]
        self.ambient_intensity = 1.0
        self.sky_sun_azimuth_deg = 210.0
        self.sky_sun_elevation_deg = 35.0
        self.sky_sun_ang_radius = 0.009*3
        self.sky_air_density = 0.06
        self.light_power_scale = 1.0
        self.show_floor = True
        self.transparent_background = False
        self.max_reflect_count = 8 / 2
        self.phys_glass = True
        self.area_ray_count = 0
        self.top_light_count = 3
        self.ngon_phase = 0

        self.frame_export_resolution_scale = 1
        self.frame_export_show_floor = copy.copy(self.show_floor)
        self.frame_export_transparent_background = copy.copy(self.transparent_background)
        self.frame_export_max_reflect_count = copy.copy(self.max_reflect_count)
        self.frame_export_phys_glass = copy.copy(self.phys_glass)
        self.frame_export_area_ray_count = copy.copy(self.area_ray_count)
        self.frame_export_top_light_count = copy.copy(self.top_light_count)
        self.frame_export_ngon_phase = copy.copy(self.ngon_phase)
        self.frame_export_dispersion = 1.0

        self.frame_export_use_viewport_base = True
        self.frame_export_base_width = 1280
        self.frame_export_base_height = 720

        self.frame_export_accum_frames = 1  # кадры накопления при экспорте; 1 = выкл

        self.render_settings[extract_name(f"{self.ambient_color=}")] = self.ambient_color
        self.render_settings[extract_name(f"{self.ambient_intensity=}")] = self.ambient_intensity
        self.render_settings[extract_name(f"{self.sky_sun_azimuth_deg=}")] = self.sky_sun_azimuth_deg
        self.render_settings[extract_name(f"{self.sky_sun_elevation_deg=}")] = self.sky_sun_elevation_deg
        self.render_settings[extract_name(f"{self.sky_sun_ang_radius=}")] = self.sky_sun_ang_radius
        self.render_settings[extract_name(f"{self.sky_air_density=}")] = self.sky_air_density
        self.render_settings[extract_name(f"{self.light_power_scale=}")] = self.light_power_scale
        self.render_settings[extract_name(f"{self.show_floor=}")] = self.show_floor
        self.render_settings[extract_name(f"{self.transparent_background=}")] = self.transparent_background
        self.render_settings[extract_name(f"{self.max_reflect_count=}")] = self.max_reflect_count
        self.render_settings[extract_name(f"{self.phys_glass=}")] = self.phys_glass
        self.render_settings[extract_name(f"{self.area_ray_count=}")] = self.area_ray_count
        self.render_settings[extract_name(f"{self.top_light_count=}")] = self.top_light_count
        self.render_settings[extract_name(f"{self.ngon_phase=}")] = self.ngon_phase

        self.render_settings[extract_name(f"{self.frame_export_resolution_scale=}")] = self.frame_export_resolution_scale
        self.render_settings[extract_name(f"{self.frame_export_show_floor=}")] = self.frame_export_show_floor
        self.render_settings[extract_name(f"{self.frame_export_transparent_background=}")] = self.frame_export_transparent_background
        self.render_settings[extract_name(f"{self.frame_export_max_reflect_count=}")] = self.frame_export_max_reflect_count
        self.render_settings[extract_name(f"{self.frame_export_phys_glass=}")] = self.frame_export_phys_glass
        self.render_settings[extract_name(f"{self.frame_export_area_ray_count=}")] = self.frame_export_area_ray_count
        self.render_settings[extract_name(f"{self.frame_export_top_light_count=}")] = self.frame_export_top_light_count
        self.render_settings[extract_name(f"{self.frame_export_ngon_phase=}")] = self.frame_export_ngon_phase
        self.render_settings[extract_name(f"{self.frame_export_dispersion=}")] = self.frame_export_dispersion
        self.render_settings[extract_name(f"{self.frame_export_use_viewport_base=}")] = self.frame_export_use_viewport_base
        self.render_settings[extract_name(f"{self.frame_export_base_width=}")] = self.frame_export_base_width
        self.render_settings[extract_name(f"{self.frame_export_base_height=}")] = self.frame_export_base_height
        self.render_settings[extract_name(f"{self.frame_export_accum_frames=}")] = self.frame_export_accum_frames

        #endregion

        #region Настройки управления
        self.camera_rotate_sensitivity = 0.005
        self.object_rotate_sensitivity = 0.005
        self.camera_pan_sensitivity = 1.0
        self.object_translate_sensitivity = 1.0
        self.camera_dolly_speed = 0.35

        self.control_settings[extract_name(f"{self.camera_rotate_sensitivity=}")] = self.camera_rotate_sensitivity
        self.control_settings[extract_name(f"{self.object_rotate_sensitivity=}")] = self.object_rotate_sensitivity
        self.control_settings[extract_name(f"{self.camera_pan_sensitivity=}")] = self.camera_pan_sensitivity
        self.control_settings[extract_name(f"{self.object_translate_sensitivity=}")] = self.object_translate_sensitivity
        self.control_settings[extract_name(f"{self.camera_dolly_speed=}")] = self.camera_dolly_speed
        #endregion

        #region Выделение операций
        # Выделение хранится как набор индексов операций.
        # selected_operation_index оставлен для обратной совместимости.
        self.selected_operation_index = 0
        self.selected_operation_indices: list[int] = []
        #endregion

        #region Fast mode
        self.base_fast_mode = False
        self.interaction_fast_mode = False
        #endregion

        self.sdf_dict: dict[str,str] = None
        self.materials: list[Material] = []
        self.scene_root: GroupOperation = None

        self.scene_history = SceneHistory()
        self.scene_history.activate(None)  # сцена без открытого проекта

        self.operations: list[Operation] = []
        self.texture_images: list[QImage] = []

    #region Свойства
    @property
    def fast_mode(self) -> bool:
        return bool(self.base_fast_mode or self.interaction_fast_mode)

    #endregion

    #region Эмит сигналов
    def emit_changed(self):
        self.changed.emit()

    def emit_scene_changed(self, *, record_history=True):
        if self.scene_root is not None:
            self.rebuild_operations()
            if record_history:
                self.scene_history.record(self.scene_root)
        self.scene_changed.emit()
        self.changed.emit()

    def emit_shader_changed(self):
        self.shader_changed.emit()
        self.changed.emit()

    #endregion

    #region Сеттеры
    def set_frame_interval_ms(self, value: int):
        self.frame_interval_ms = max(1, int(value))
        self.changed.emit()

    def set_chunk_interval_ms(self, value: int):
        self.quality_chunk_interval_ms = max(1, int(value))
        self.changed.emit()

    def set_preview_scale(self, value: float):
        self.quality_preview_scale = max(1.0, float(value))
        self.fast_preview_scale = max(1.0, float(value * 2.5))
        self.changed.emit()

    def set_camera_fov_deg(self, value: float):
        self.camera_fov_deg = max(10.0, min(120.0, float(value)))
        self.changed.emit()

    def set_camera_pose(self, pos: QVector3D, rot: QVector3D):
        self.camera_pos = [float(pos.x), float(pos.y), float(pos.z)]
        self.camera_rot = [float(rot.x), float(rot.y), float(rot.z)]
        self.changed.emit()

    def set_time_value(self, t: float):
        self.time_value = float(t)
        self.changed.emit()

    #endregion

    #region Выделение операций
    def _normalize_selected_operation_indices(self):
        if not self.operations:
            self.selected_operation_indices = []
            self.selected_operation_index = 0
            return

        max_index = len(self.operations) - 1
        indices = []
        seen = set()
        for index in self.selected_operation_indices:
            index = int(index)
            if 0 <= index <= max_index and index not in seen:
                seen.add(index)
                indices.append(index)

        if not indices:
            fallback = max(0, min(int(self.selected_operation_index), max_index))
            indices = [fallback]

        self.selected_operation_indices = indices
        self.selected_operation_index = indices[0]

    def set_selected_operation_indices(self, indices, emit=True):
        self.selected_operation_indices = list(indices or [])
        self._normalize_selected_operation_indices()
        if emit:
            self.emit_changed()

    def get_selected_operation_indices(self):
        self._normalize_selected_operation_indices()
        return list(self.selected_operation_indices)

    def get_selected_figures(self):
        figures = []
        seen = set()
        for index in self.get_selected_operation_indices():
            op = self.operations[index]
            figure = op.figure if op is not None else None
            if figure is not None and id(figure) not in seen:
                seen.add(id(figure))
                figures.append(figure)
        return figures

    def get_selected_figure(self):
        figures = self.get_selected_figures()
        return figures[0] if figures else None

    #endregion

    #region Сцена
    def get_operations(self):
        return self.operations

    def rebuild_operations(self, registers_count: int = 8):
        if self.scene_root is None:
            self.operations = []
        else:
            self.operations = self.scene_root.to_operation_sequence(
                registers_count=registers_count
            )
        self._normalize_selected_operation_indices()
        return self.operations

        #region История сцены
    def begin_scene_change(self):
        """Начать составную правку, например движение мышью или слайдера."""
        self.scene_history.begin_batch(self.scene_root)

    def end_scene_change(self):
        """Записать итог составной правки одним шагом undo."""
        return self.scene_history.end_batch(self.scene_root)

    def undo_scene(self):
        root = self.scene_history.undo()
        if root is None:
            return False

        self.set_scene_root(root, rebuild=True, emit=False)
        self.emit_scene_changed(record_history=False)
        self.history_restored.emit()
        return True

    def redo_scene(self):
        root = self.scene_history.redo()
        if root is None:
            return False

        self.set_scene_root(root, rebuild=True, emit=False)
        self.emit_scene_changed(record_history=False)
        self.history_restored.emit()
        return True
        #endregion

    def set_scene_root(self, scene_root, materials=None, texture_images=None, rebuild=True, emit=True):
        self.scene_root = scene_root

        if materials is not None:
            self.materials = list(materials)

        if texture_images is not None:
            self.texture_images = list(texture_images)

        if rebuild:
            self.rebuild_operations()

        if emit:
            self.emit_scene_changed()

    def set_operations(self, operations, materials=None, texture_images=None, emit=True):
        self.scene_root = None
        self.operations = list(operations or [])

        if materials is not None:
            self.materials = list(materials)

        if texture_images is not None:
            self.texture_images = list(texture_images)

        self._normalize_selected_operation_indices()

        if emit:
            self.emit_scene_changed()

    #endregion

    #region Дефолтная сцена
    @staticmethod
    def get_default_materials():
        materials: dict[str, Material] = {}
        #region Plastic
        materials["plastic gloss"] = Material(roughness=0.06, metallic=0.0)
        materials["plastic rough"] = Material(roughness=0.72, metallic=0.0)
        #endregion
        materials["latex"] = Material(roughness=0.20, metallic=0.0)
        #region Metal
        materials["metal polished"] = Material(roughness=0.05, metallic=1.0)
        materials["metal rough"] = Material(roughness=0.55, metallic=1.0)
        #endregion
        #region Glass
        materials["glass clear"] = Material(roughness=0.0, metallic=0.0, transmission=1.0)
        materials["glass blury"] = Material(roughness=0.0, metallic=0.0, transmission=0.5)
        materials["glass rough"] = Material(roughness=0.5, metallic=0.0, transmission=1.0)
        #endregion
        #region SSS
            #region Skin
        materials["healthy skin"] = Material(roughness=0.55, metallic=0.0, sss_color=[1, 0.45, 0.30], subsurface=0.5)
        materials["deadly purple skin"] = Material(roughness=0.55, metallic=0.0, sss_color=[0.45, 0.30, 1], subsurface=0.5)
        materials["deadly blue skin"] = Material(roughness=0.55, metallic=0.0, sss_color=[0.30, 0.45, 1], subsurface=0.5)
        materials["deadly green skin"] = Material(roughness=0.55, metallic=0.0, sss_color=[0.30, 1, 0.45], subsurface=0.5)
            #endregion
        materials["green leaf"] = Material(roughness=0.55, metallic=0.0, sss_color=[0.45, 1, 0.30], subsurface=0.5)
        materials["wax"] = Material(roughness=0.40, metallic=0.0, sss_color=[1, 0.65, 0.40], subsurface=0.9/2)
        materials["jade"] = Material(roughness=0.28, metallic=0.0, sss_color=[0.45, 0.90, 0.60], subsurface=0.60/3)
        materials["marble"] = Material(roughness=0.28, metallic=0.0, sss_color=[0.90, 0.85, 0.80], subsurface=0.25/3)
        #endregion
        materials["stone rough"] = Material(roughness=0.85, metallic=0.0)
        #region Light
        materials["light power 1"] = Material(emission=1.0)
        materials["light power 2"] = Material(emission=2.0)
        materials["light power 3"] = Material(emission=3.0)
        materials["light power 4"] = Material(emission=4.0)
        materials["light power 5"] = Material(emission=5.0)
        materials["light power 6"] = Material(emission=6.0)
        materials["light power 10"] = Material(emission=10.0)
        materials["light power 20"] = Material(emission=20.0)
        #endregion
        return materials

    @staticmethod
    def get_default_operations(figure_index=None) -> tuple[list[Material], list]:
        fi = figure_index or DEFAULT_FIGURE_INDEX.__getitem__

        materials_named = RenderPreviewParams.get_default_materials()
        materials = list(materials_named.values())

        def material_index(name: str) -> int:
            return materials.index(materials_named[name])

        def make_figure(shape, albedo, material_name, position,
                        elongation=None, scale=1.0,
                        trajectory_fn_id="", trajectory_params=None) -> Figure:
            return Figure(
                figure_type=fi(shape),
                albedo=albedo,
                material_id=material_index(material_name),
                translation=position,
                rotation=[0.0, 0.0, 0.0],
                elongation=elongation or [1.0, 1.0, 1.0],
                scale=scale,
                extra_params=[],
                trajectory_fn_id=trajectory_fn_id,
                trajectory_params=trajectory_params or [],
            )

        def make_leaf(figure: Figure, op_type: int = OP_UNION,
                      unary_op_type: int = -1, unary_param: float = 0.0) -> TreeObject:
            # Лист: бинарная операция наложения + фигура; унарная операция
            # над SDF листа (round/shell) — второй аргумент TreeObject
            unary_op = None
            if unary_op_type != -1:
                unary_op = Operation(
                    op_type=unary_op_type,
                    operands=[0.0, 0.0, unary_param, 0.0],
                )
            return TreeObject(Operation(op_type=op_type, figure=figure), unary_op)

        def make_rounded_cube(position, albedo, material_name, scale=0.38, round_radius=0.08):
            # Скругление — унарная операция самого листа:
            # итоговая дистанция = box(d) - round_radius
            return make_leaf(
                make_figure("box", albedo, material_name, position, scale=scale),
                unary_op_type=OP_ROUND, unary_param=round_radius,
            )

        def make_capsule(position, albedo, material_name, scale=0.38):
            return make_leaf(make_figure(
                "sphere", albedo, material_name, position,
                elongation=[1.0, 2.0, 1.0], scale=scale,
            ))

        scene_childs = [
            make_leaf(make_figure(
                "box", [0.5, 0.5, 0.5], "stone rough", [0.0, -0.5, 0.0],
                elongation=[10.0, 0.1, 10.0],
            )),
            make_rounded_cube([-2.7, 1.15, 0.95 * 3], [0.92, 0.664, 0.496], "healthy skin"),
            make_rounded_cube([-0.9, 1.15, 0.95 * 3], [0.92, 0.664, 0.496], "deadly purple skin"),
            make_rounded_cube([0.9, 1.15, 0.95 * 3],  [0.92, 0.664, 0.496], "deadly blue skin"),
            make_rounded_cube([2.7, 1.15, 0.95 * 3],  [0.92, 0.664, 0.496], "deadly green skin"),

            make_rounded_cube([-2.7, 1.15, 0.95], [0.92, 0.92, 0.92], "plastic gloss"),
            make_rounded_cube([-0.9, 1.15, 0.95], [0.92, 0.92, 0.92], "plastic rough"),
            make_rounded_cube([0.9, 1.15, 0.95], [0.82, 0.84, 0.88], "metal polished"),
            make_rounded_cube([2.7, 1.15, 0.95], [0.82, 0.84, 0.88], "metal rough"),
            make_rounded_cube([-2.7, 1.15, -0.95], [1.00, 0.25, 0.20], "plastic gloss"),
            make_rounded_cube([-0.9, 1.15, -0.95], [1.00, 0.82, 0.22], "metal polished"),
            make_rounded_cube([0.9, 1.15, -0.95], [1.00, 0.82, 0.22], "metal rough"),
            make_capsule([2.7, 1.15, -0.95], [0.032, 0.032, 0.032], "latex"),
            make_capsule([-4.5, 1.15, 0.00], [0.96, 0.98, 1.00], "glass clear"),
            make_capsule([+4.5, 1.15, 0.00], [0.96, 0.98, 1.00], "glass rough"),
            make_leaf(make_figure(
                "sphere", [1.00, 0.82, 0.55], "light power 6", [0.0, 6.0, 0.0],
                scale=0.22 * 3, trajectory_fn_id="ellipse",
                trajectory_params={"radius_a": 3.0 * 1.5, "radius_b": 1.5 * 1.5,
                                   "speed": 2.0 / 6, "rotate_figure": True},
            )),
            make_leaf(make_figure(
                "sphere", [0.55, 0.78, 1.00], "light power 2", [0.0, 5.0, 0.0],
                scale=0.28 * 3, trajectory_fn_id="ellipse",
                trajectory_params={"radius_a": 3.0 * 1.5 * 2, "radius_b": 1.5 * 1.5 * 2,
                                   "speed": -2.0 / 3, "rotate_figure": True},
            )),
        ]

        return materials, scene_childs

    #endregion

    #region Fast mode
    def begin_interaction(self):
        self.interaction_fast_mode = True
        self.changed.emit()

    def end_interaction(self):
        self.interaction_fast_mode = False
        self.changed.emit()

    #endregion

class SDFRenderPreviewer(QtWidgets.QWidget):

    image_updated = pyqtSignal(QImage)

    export_progress = pyqtSignal(int, int)  # (готово чанков, всего чанков)

    MODE_NONE = 0

    MODE_OBJECT_ROTATE = 1

    MODE_CAMERA_ROTATE = 2

    MODE_OBJECT_TRANSLATE = 3

    MODE_CAMERA_PAN = 4

    def __init__(self, params: RenderPreviewParams | None = None, parent=None):
        #region Qt-виджет
        super().__init__(parent)

        self.setFocusPolicy(QtCore.Qt.StrongFocus)
        self.setMouseTracking(True)
        self.setMinimumSize(240, 180)
        #endregion

        #region Ядро
        self.params = params or RenderPreviewParams(self)
        self.renderer = None
        #endregion

        #region Шейдеры
        self._base_frag_code = _read_text_file(SDF_FRAG_PATH)
        self._vert_code = _read_text_file(SDF_VERT_PATH)
        self._fast_render_code = self._load_fast_render_code()

        self.shader_manager = SDFShaderManager(
            base_shader=self._base_frag_code,
            fast_render_code=self._fast_render_code,
        )
        #endregion

        #region Состояние рендера
        self._current_image = None
        self._start_time = time.perf_counter()
        self._renderer_initialized = False
        self._renderer_start_scheduled = False
        self._render_in_progress = False
        self._rerender_requested = False
        self._rerender_reason = ""
        self._last_resize_key = None
        self._request_seq = 0
        self._chunk_size_current = 16
        self._renderer_init_failed = False
        #endregion

        #region Экспорт кадра
        self._export_state = None
        self._export_total_chunks = 0
        self._export_tick_connected = False
        #endregion

        #region Замер времени рендера
        self._render_started_at = None
        self._last_frame_render_ms = 0.0
        self._avg_frame_render_ms = 0.0
        self._render_time_samples = []
        self._render_time_window = 16
        #endregion

        #region Состояние мыши и драга
        self._mouse_history_batch = False
        self._wheel_history_batch = False

        self._mouse_mode = self.MODE_NONE
        self._last_mouse_pos = QtCore.QPoint()
        self._drag_start_mouse_pos = QtCore.QPoint()
        self._drag_group_screen_center = QtCore.QPointF()
        self._drag_start_camera_rot = None
        self._drag_start_camera_pos = None
        self._drag_camera_rot_for_object = None
        self._drag_selected_objects = []
        self._drag_start_object_rots = []
        self._drag_start_object_positions = []
        #endregion

        #region Таймеры
        self._frame_timer = QtCore.QTimer(self)
        self._frame_timer.timeout.connect(self._on_frame_tick)

        self._interaction_timer = QtCore.QTimer(self)
        self._interaction_timer.setSingleShot(True)
        self._interaction_timer.timeout.connect(self._on_interaction_timeout)

        # Сплэш загрузки: фаза анимации и её таймер
        self._splash_phase = 0.0
        self._splash_timer = QtCore.QTimer(self)
        self._splash_timer.setInterval(50)
        self._splash_timer.timeout.connect(self._on_splash_tick)
        self._splash_timer.start()
        #endregion

        #region Подписки на сигналы
        # Хоткеи активны, когда фокус у виджета превью.
        self._history_shortcuts = []
        for sequence, callback in (
            ("Ctrl+Z", self.params.undo_scene),
            ("Ctrl+Shift+Z", self.params.redo_scene),
            ("Ctrl+Y", self.params.redo_scene),
        ):
            shortcut = QtWidgets.QShortcut(QKeySequence(sequence), self)
            shortcut.setContext(QtCore.Qt.WidgetShortcut)
            shortcut.activated.connect(callback)
            self._history_shortcuts.append(shortcut)

        self.params.changed.connect(self._on_params_changed)
        self.params.shader_changed.connect(self._rebuild_shader)
        #endregion

        self._dbg("render_previewer.init.complete.log_debug")

    #region Запуск рендерера и шейдеры
    def _schedule_renderer_start(self):
        if self._renderer_initialized or self._renderer_start_scheduled:
            return
        if self._renderer_init_failed:
            return  # упавшую инициализацию перезапускаем только через пересборку шейдера
        self._renderer_start_scheduled = True
        QtCore.QTimer.singleShot(0, self._start_renderer)

    def _start_renderer(self):
        self._renderer_start_scheduled = False
        if self._renderer_initialized:
            return

        # GL живёт в GUI-потоке: standalone-контекст moderngl в рабочем
        # потоке на этом драйвере читает FBO как мусор (диагностировано).
        # Методы рендерера принимают dict-payload — вызываем напрямую.
        self.renderer = GPURenderer()
        self.renderer.init_done.connect(self._on_renderer_ready)
        self.renderer.frame_ready.connect(self._on_frame_ready)

        _, frag_code = self.shader_manager.update_shader(self.params.sdf_dict)
        profile = self._get_active_render_profile()
        self._chunk_size_current = int(profile["chunk_size"])
        self.renderer.initialize({
            "frag_code": frag_code,
            "vert_code": self._vert_code,
            "chunk_size": int(profile["chunk_size"]),
            "w": max(1, self.width()),
            "h": max(1, self.height()),
            "preview_scale": float(profile["preview_scale"]),
        })


    def _rebuild_shader(self):
        if self.renderer is None or not self._renderer_initialized:
            return
        self.shader_manager.fast_render_code = self._load_fast_render_code()
        _, frag_code = self.shader_manager.update_shader(self.params.sdf_dict)

        self._render_in_progress = False
        self._rerender_requested = False
        self._rerender_reason = ""
        self._renderer_initialized = False   # на время пересборки снова сплэш
        self._splash_timer.start()
        self.update()

        profile = self._get_active_render_profile()
        self._renderer_init_failed = False  # шейдер изменился — даём инициализации второй шанс
        self.renderer.initialize({
            "frag_code": frag_code,
            "vert_code": self._vert_code,
            "chunk_size": int(profile["chunk_size"]),
            "w": max(1, self.width()),
            "h": max(1, self.height()),
            "preview_scale": float(profile["preview_scale"]),
        })

    def _load_fast_render_code(self) -> str:
        try:
            return _read_text_file(SDF_FRAG_FAST_PATH)
        except Exception as e:
            self._dbg("render_previewer._load_fast_render_code.load_failed.error.log_debug", error=e)
            return """
                    accumulatedColor = vec3(0.0);
                    vec3 lightDir = normalize(vec3(0.4, 0.7, -0.5));
                    float diff = max(dot(normal, lightDir), 0.0);
                    vec3 baseColor = vec3(0.75, 0.82, 0.9) * (0.15 + 0.85 * diff);
                    fragColor = vec4(ACESFilm(baseColor), 1.0);
                    """

    #endregion

    #region Цикл рендера
    def request_render(self):
        if not self._renderer_initialized or self.renderer is None:
            return
        if self._export_state is not None:
            self._rerender_requested = True
            self._rerender_reason = "export_in_progress"
            return
        if self._render_in_progress:
            self._rerender_requested = True
            self._rerender_reason = "request_during_active_render"
            return

        self._request_seq += 1
        profile = self._get_active_render_profile()
        self._sync_renderer_size()

        self._render_in_progress = True
        self._render_started_at = time.perf_counter()
        self.renderer.start_frame({
            "textures": list(self.params.texture_images) or None,
            "chunk_interval_ms": int(profile["chunk_interval_ms"]),
            "params": {
                "cam_pos": list(self.params.camera_pos),
                "cam_rot": list(self.params.camera_rot),
                "camera_fov_deg": float(self.params.camera_fov_deg),
                "ambient_color": list(self.params.ambient_color),
                "ambient_intensity": float(self.params.ambient_intensity),
                "sky_sun_azimuth_deg": float(self.params.sky_sun_azimuth_deg),
                "sky_sun_elevation_deg": float(self.params.sky_sun_elevation_deg),
                "sky_sun_ang_radius": float(self.params.sky_sun_ang_radius),
                "sky_air_density": float(self.params.sky_air_density),
                "light_power_scale": float(self.params.light_power_scale),
                "show_floor": bool(self.params.show_floor),
                "transparent_bg": bool(self.params.transparent_background),
                "max_reflect_count": int(self.params.max_reflect_count),
                "phys_glass": bool(self.params.phys_glass),
                "area_ray_count": int(self.params.area_ray_count),
                "top_light_count": int(self.params.top_light_count),
                "ngon_phase": float(self.params.ngon_phase),

                "sample_seed": int(0), "dispersion": float(0), "sample_count": int(1), "sample_id": int(0), # заглушки

                "output_linear": bool(False),
                "fast_mode": bool(self.params.fast_mode),
                "materials": list(self.params.materials),
                "operations": list(self.params.operations),
                "time_val": float(self.params.time_value),
            },
        })

    def _on_renderer_ready(self, ok: bool):
        if not ok:
            self._renderer_init_failed = True
            self._dbg("render_previewer._on_renderer_ready.init_failed.error.log_debug")
            return
        self._renderer_initialized = True
        self._splash_timer.stop()
        self._apply_timer_settings()
        QtCore.QTimer.singleShot(50, self.request_render)

    def _on_frame_ready(self, image: QImage):
        if self._render_started_at is not None:
            ms = (time.perf_counter() - self._render_started_at) * 1000.0
            self._last_frame_render_ms = ms
            self._render_time_samples.append(ms)
            if len(self._render_time_samples) > self._render_time_window:
                self._render_time_samples.pop(0)
            self._avg_frame_render_ms = sum(self._render_time_samples) / len(self._render_time_samples)
            self._render_started_at = None
        self._current_image = image
        self._render_in_progress = False
        self.image_updated.emit(self._current_image)
        self.update()
        if self._rerender_requested:
            self._rerender_requested = False
            self._rerender_reason = ""
            QtCore.QTimer.singleShot(0, self.request_render)

    def _on_frame_tick(self):
        if self.params.auto_time and self._renderer_initialized:
            elapsed = time.perf_counter() - self._start_time
            self.params.time_value = elapsed * float(self.params.time_scale)

        if self._render_in_progress:
            self._rerender_requested = True
            self._rerender_reason = "frame_tick"
            return
        self.request_render()

    def _on_params_changed(self):
        self._apply_timer_settings()
        if not self._renderer_initialized:
            return
        if self._render_in_progress:
            self._rerender_requested = True
            self._rerender_reason = "params_changed"
            return
        QtCore.QTimer.singleShot(0, self.request_render)


    def _abort_active_render(self, reason: str):
        if self.renderer is None or not self._render_in_progress:
            return
        self._render_started_at = None
        self.renderer.stop_render()
        self._render_in_progress = False

    def _apply_timer_settings(self):
        target_ms = max(1, int(self.params.frame_interval_ms))
        avg_ms = int(round(self._avg_frame_render_ms))
        self._frame_timer.setInterval(max(1, target_ms - avg_ms))
        if self.params.auto_time:
            if not self._frame_timer.isActive():
                self._frame_timer.start()
        else:
            self._frame_timer.stop()

    def _sync_renderer_size(self):
        if self.renderer is None:
            return
        profile = self._get_active_render_profile()
        preview_scale = float(profile["preview_scale"])
        chunk_size = int(profile["chunk_size"])

        if self._chunk_size_current != chunk_size:
            self._chunk_size_current = chunk_size
            self._last_resize_key = None

        resize_key = (self.width(), self.height(), preview_scale, chunk_size)
        if resize_key == self._last_resize_key:
            return
        self._last_resize_key = resize_key
        self.renderer.resize({
            "chunk_size": chunk_size,
            "w": max(1, self.width()),
            "h": max(1, self.height()),
            "preview_scale": preview_scale,
        })

    def _get_active_render_profile(self):
        if self.params.fast_mode:
            return {
                "preview_scale": float(self.params.fast_preview_scale),
                "chunk_size": int(self.params.fast_chunk_size),
                "chunk_interval_ms": int(self.params.fast_chunk_interval_ms),
            }
        return {
            "preview_scale": float(self.params.quality_preview_scale),
            "chunk_size": int(self.params.quality_chunk_size),
            "chunk_interval_ms": int(self.params.quality_chunk_interval_ms),
        }

    #endregion

    #region Экспорт кадра
    def render_frame_export(self, on_finished=None, base_size=None):
        if not self._renderer_initialized or self.renderer is None:
            if on_finished is not None:
                on_finished(None)
            return None

        self._abort_active_render("frame_export")
        self._rerender_requested = False
        self._rerender_reason = ""

        params = self.params
        scale = max(0.01, float(params.frame_export_resolution_scale))
        if base_size is None:
            base_size = (self.width(), self.height())
        target_w = max(1, int(round(float(base_size[0]) * scale)))
        target_h = max(1, int(round(float(base_size[1]) * scale)))

        def _on_done(image):
            # отписка от одноразовых подключений и возврат превью
            try:
                self.renderer.export_done.disconnect(_on_done)
                self.renderer.export_progress_changed.disconnect(self._on_export_progress)
            except TypeError:
                pass
            self._export_state = None
            self._restore_preview_after_export()
            if on_finished is not None:
                on_finished(image)

        self.renderer.export_done.connect(_on_done)
        self.renderer.export_progress_changed.connect(self._on_export_progress)
        self._export_state = {"active": True}
        self.export_progress.emit(0, 1)  # точный тотал придёт из воркера

        def round_nearest(x, k):
            return k * round(x / k)

        if self.params.frame_export_dispersion > 0:
            self.params.frame_export_accum_frames = round_nearest(self.params.frame_export_accum_frames, 3)

        self.renderer.start_export({
            "w": target_w,
            "h": target_h,
            "chunk_size": int(params.quality_chunk_size),
            "chunk_interval_ms": int(params.quality_chunk_interval_ms),
            "params": {
                "cam_pos": list(params.camera_pos),
                "cam_rot": list(params.camera_rot),
                "camera_fov_deg": float(params.camera_fov_deg),
                "ambient_color": list(params.ambient_color),
                "ambient_intensity": float(params.ambient_intensity),
                "light_power_scale": float(params.light_power_scale),
                "sky_sun_azimuth_deg": float(params.sky_sun_azimuth_deg),
                "sky_sun_elevation_deg": float(params.sky_sun_elevation_deg),
                "sky_sun_ang_radius": float(params.sky_sun_ang_radius),
                "sky_air_density": float(params.sky_air_density),
                "show_floor": bool(params.frame_export_show_floor),
                "transparent_bg": bool(params.frame_export_transparent_background),
                "max_reflect_count": int(params.frame_export_max_reflect_count),
                "phys_glass": bool(params.frame_export_phys_glass),
                "area_ray_count": int(params.frame_export_area_ray_count),
                "top_light_count": int(params.frame_export_top_light_count),
                "ngon_phase": float(params.frame_export_ngon_phase),
                "sample_seed": int(0), # Заглушка - внутри функции будет переопределено
                "dispersion": float(params.frame_export_dispersion),
                "sample_count": int(params.frame_export_accum_frames),
                "sample_id": int(0), # Заглушка - внутри функции будет переопределено
                "output_linear": bool(False), # Заглушка - внутри функции будет переопределено
                "fast_mode": False,
                "materials": list(params.materials),
                "operations": list(params.operations),
                "time_val": float(params.time_value),
            },
            "accum_frames": int(params.frame_export_accum_frames),
        })
        return None  # результат теперь всегда асинхронный, через on_finished

    def _on_export_progress(self, done_chunks, total_chunks):
        self.export_progress.emit(done_chunks, total_chunks)

    def _restore_preview_after_export(self):
        # размеры и параметры уедут в воркер со следующим кадром
        self._last_resize_key = None
        QtCore.QTimer.singleShot(0, self.request_render)

    #endregion

    #region Fast mode и взаимодействие
    def begin_external_fast_mode(self):
        if not self.params.base_fast_mode:
            self.params.base_fast_mode = True
            self.params.emit_changed()

    def end_external_fast_mode(self):
        if self.params.base_fast_mode:
            self.params.base_fast_mode = False
            self.params.emit_changed()

    def _begin_interaction(self):
        self.params.begin_interaction()
        self._interaction_timer.start(max(1, int(self.params.fast_mode_hold_ms)))

    def _on_interaction_timeout(self):
        self.params.end_interaction()
        if self._wheel_history_batch:
            self.params.end_scene_change()
            self._wheel_history_batch = False
        if self._renderer_initialized:
            self.request_render()

    #endregion

    #region События Qt
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._export_state is not None:
            # экспорт идёт — ресайз превью откладываем,
            # _restore_preview_after_export сам подхватит актуальный размер
            return
        if self._renderer_initialized:
            self._sync_renderer_size()
            if self._render_in_progress:
                self._rerender_requested = True
                self._rerender_reason = "resize"
            self._abort_active_render("resize")
            QtCore.QTimer.singleShot(0, self.request_render)

    def paintEvent(self, event):
        painter = QPainter(self)
        if not self._renderer_initialized:
            # сплэш загрузки вместо пустого фона
            self._paint_loading_splash(painter)
            painter.end()
            return
        painter.fillRect(self.rect(), QColor(20, 20, 24))
        if self._current_image is not None and not self._current_image.isNull():
            painter.setRenderHint(QPainter.SmoothPixmapTransform, False)
            painter.drawImage(self.rect(), self._current_image)
        painter.end()

    def mousePressEvent(self, event):
        buttons = event.buttons()
        self._begin_mouse_drag_session(event.pos(), buttons)
        if self._mouse_mode != self.MODE_NONE:
            self.setFocus(QtCore.Qt.MouseFocusReason)
            if not self._mouse_history_batch:
                self.params.begin_scene_change()
                self._mouse_history_batch = True
            self.begin_external_fast_mode()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        buttons = event.buttons()
        new_mode = self._resolve_mouse_mode(buttons)

        if new_mode == self.MODE_NONE:
            self._mouse_mode = self.MODE_NONE
            super().mouseMoveEvent(event)
            return

        if new_mode != self._mouse_mode:
            self._begin_mouse_drag_session(event.pos(), buttons)
            self.begin_external_fast_mode()
            super().mouseMoveEvent(event)
            return

        if self._drag_start_mouse_pos is None:
            self._begin_mouse_drag_session(event.pos(), buttons)

        total_dx = event.pos().x() - self._drag_start_mouse_pos.x()
        total_dy = event.pos().y() - self._drag_start_mouse_pos.y()
        self._last_mouse_pos = QtCore.QPoint(event.pos())
        self.begin_external_fast_mode()

        if self._mouse_mode == self.MODE_CAMERA_ROTATE:
            if self._drag_start_camera_rot is None:
                super().mouseMoveEvent(event)
                return
            new_cam_rot = drag_to_camera_rotation(
                total_dx=total_dx,
                total_dy=total_dy,
                start_camera_rotation=self._drag_start_camera_rot,
                sensitivity=self.params.camera_rotate_sensitivity,
            )
            self.params.camera_rot = [new_cam_rot.x(), new_cam_rot.y(), new_cam_rot.z()]
            self.params.emit_changed()

        elif self._mouse_mode == self.MODE_CAMERA_PAN:
            if self._drag_start_camera_pos is None or self._drag_start_camera_rot is None:
                super().mouseMoveEvent(event)
                return
            pan_depth = max(1.0, abs(self._drag_start_camera_pos[2]))
            new_cam_pos = mouse_to_camera_pan(
                dx=total_dx,
                dy=total_dy,
                camera_rotation=self._drag_start_camera_rot,
                camera_position=self._drag_start_camera_pos,
                depth=pan_depth,
                viewport_height=max(1, self.height()),
                fov_deg=self.params.camera_fov_deg,
                sensitivity=self.params.camera_pan_sensitivity,
            )
            self.params.camera_pos = [new_cam_pos.x(), new_cam_pos.y(), new_cam_pos.z()]
            self.params.emit_changed()

        elif self._mouse_mode == self.MODE_OBJECT_ROTATE:
            figures = self._drag_selected_objects
            if not figures or not self._drag_start_object_rots:
                super().mouseMoveEvent(event)
                return
            new_positions, new_rotations = drag_to_figures_rotation(
                total_dx=total_dx,
                total_dy=total_dy,
                drag_camera_rotation=self._drag_camera_rot_for_object,
                start_positions=self._drag_start_object_positions,
                start_rotations=self._drag_start_object_rots,
                sensitivity=self.params.object_rotate_sensitivity,
            )
            self._apply_group_transform(figures, new_positions, new_rotations)
            self.params.emit_scene_changed()

        elif self._mouse_mode == self.MODE_OBJECT_TRANSLATE:
            figures = self._drag_selected_objects
            if not figures or not self._drag_start_object_positions:
                super().mouseMoveEvent(event)
                return
            new_positions = drag_to_figures_translation(
                total_dx=total_dx,
                total_dy=total_dy,
                drag_camera_rotation=self._drag_camera_rot_for_object,
                start_positions=self._drag_start_object_positions,
                camera_position=self._drag_start_camera_pos,
                viewport_width=max(1, self.width()),
                viewport_height=max(1, self.height()),
                fov_deg=self.params.camera_fov_deg,
                sensitivity=self.params.object_translate_sensitivity,
            )
            self._apply_group_transform(figures, new_positions)
            self.params.emit_scene_changed()

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        buttons = event.buttons()
        new_mode = self._resolve_mouse_mode(buttons)

        if new_mode == self.MODE_NONE:
            self._mouse_mode = self.MODE_NONE
            self._interaction_timer.stop()
            self.params.end_interaction()

            if self._wheel_history_batch:
                self.params.end_scene_change()
                self._wheel_history_batch = False
            if self._mouse_history_batch:
                self.params.end_scene_change()
                self._mouse_history_batch = False

            self.end_external_fast_mode()

            self._drag_start_mouse_pos = QtCore.QPoint()
            self._drag_start_camera_rot = None
            self._drag_start_camera_pos = None
            self._drag_camera_rot_for_object = None
            self._drag_selected_objects = []
            self._drag_start_object_rots = []
            self._drag_start_object_positions = []
            self._drag_group_screen_center = QtCore.QPointF()
        else:
            self._begin_mouse_drag_session(event.pos(), buttons)
            self.begin_external_fast_mode()

        super().mouseReleaseEvent(event)

    def wheelEvent(self, event):
        if not self._wheel_history_batch:
            self.params.begin_scene_change()
            self._wheel_history_batch = True
        self._begin_interaction()
        new_cam_pos = wheel_to_camera_dolly(
            wheel_delta=event.angleDelta().y(),
            camera_position=self.params.camera_pos,
            camera_rotation=self.params.camera_rot,
            speed=self.params.camera_dolly_speed,
        )
        self.params.camera_pos = [new_cam_pos.x(), new_cam_pos.y(), new_cam_pos.z()]
        self.params.emit_changed()
        super().wheelEvent(event)

    #endregion

    #region Сплэш загрузки
    def _on_splash_tick(self):
        self._splash_phase += 0.05
        self.update()

    def _paint_loading_splash(self, painter: QPainter):

        ### Заметка - в текущей версии сплеш работает частично из-за того, что запустить рендер в отдельном потоке не получилось. 
        ### Поэтому сплеш провисвет вместе с всей программой, пока не завершится инициализация рендера. В будущем нужно будет исправить это.

        rect = self.rect()
        painter.fillRect(rect, QColor(13, 15, 21))

        phase = self._splash_phase

        # Название: SDF — холодный циан (математика), Forge — янтарь (кузница)
        font = painter.font()
        font.setPointSize(max(20, min(44, rect.height() // 7)))
        font.setBold(True)
        painter.setFont(font)
        fm = painter.fontMetrics()

        left, right = "SDF", "orge"
        c_left = QColor(96, 205, 255)
        c_right = QColor(255, 178, 96)

        w_left = fm.horizontalAdvance(left)
        total = w_left + fm.horizontalAdvance(right)
        x = rect.center().x() - total / 2
        baseline = rect.center().y() - fm.height() // 2 + fm.ascent() // 2

        # Пульсирующее свечение под текстом: тот же текст крупнее и прозрачнее
        glow_alpha = int(26 + 22 * (0.5 + 0.5 * math.sin(phase * 1.7)))
        glow_font = QFont(font)
        glow_font.setPointSize(font.pointSize() + 2)
        painter.setFont(glow_font)
        gm = QFontMetrics(glow_font)
        gx = rect.center().x() - (gm.horizontalAdvance(left) + gm.horizontalAdvance(right)) / 2
        c_gl = QColor(c_left); c_gl.setAlpha(glow_alpha)
        c_gr = QColor(c_right); c_gr.setAlpha(glow_alpha)
        painter.setPen(c_gl)
        painter.drawText(int(gx), baseline, left)
        gx += gm.horizontalAdvance(left)
        painter.setPen(c_gr)
        painter.drawText(int(gx), baseline, right)

        painter.setFont(font)
        painter.setPen(c_left)
        painter.drawText(int(x), baseline, left)
        x += w_left
        painter.setPen(c_right)
        painter.drawText(int(x), baseline, right)

        if self._renderer_init_failed:
            # инициализация упала: вместо «Загрузка…» и блика — причина остановки
            sub_font = QFont(font)
            sub_font.setPointSize(max(10, font.pointSize() // 2 - 2))
            sub_font.setBold(False)
            painter.setFont(sub_font)
            sm = painter.fontMetrics()

            painter.setPen(QColor(255, 120, 110))
            message = localization.get_text("render_previewer._paint_loading_splash.shader_failed.title")
            painter.drawText(
                int(rect.center().x() - sm.horizontalAdvance(message) / 2),
                baseline + max(18, font.pointSize()),
                message,
            )

            painter.setPen(QColor(150, 158, 172))
            hint = localization.get_text("render_previewer._paint_loading_splash.shader_failed.hint")
            painter.drawText(
                int(rect.center().x() - sm.horizontalAdvance(hint) / 2),
                baseline + max(18, font.pointSize()) + sm.height() + 6,
                hint,
            )
            return  # подпись с точками и полосу прогресса не рисуем

        # Подпись с бегущими точками
        sub_font = QFont(font)
        sub_font.setPointSize(max(10, font.pointSize() // 2 - 2))
        sub_font.setBold(False)
        painter.setFont(sub_font)
        dots = "." * (1 + int(phase * 2.0) % 3)
        subtitle = localization.get_text("render_previewer._paint_loading_splash.loading", dots=dots)
        sm = painter.fontMetrics()
        painter.setPen(QColor(150, 158, 172))
        painter.drawText(
            int(rect.center().x() - sm.horizontalAdvance(subtitle) / 2),
            baseline + max(18, font.pointSize()),
            subtitle,
        )

        # Индикатор без прогресса: бегущий блик пинг-понгом
        bar_w = min(rect.width() - 48, max(120, total))
        bar_x = rect.center().x() - bar_w / 2
        bar_y = baseline + max(30, int(font.pointSize() * 1.8))
        painter.setPen(QtCore.Qt.NoPen)
        painter.setBrush(QColor(38, 43, 54))
        painter.drawRect(int(bar_x), int(bar_y), int(bar_w), 3)
        sweep = 0.5 - 0.5 * math.cos(phase * 1.3)
        head_w = max(24, bar_w / 4)
        head_x = bar_x + sweep * (bar_w - head_w)
        painter.setBrush(QColor(96, 205, 255, 200))
        painter.drawRect(int(head_x), int(bar_y), int(head_w), 3)
    #endregion

    #region Навигация мышью
    def _resolve_mouse_mode(self, buttons):
        if (buttons & QtCore.Qt.LeftButton) and (buttons & QtCore.Qt.RightButton):
            return self.MODE_CAMERA_PAN
        if buttons & QtCore.Qt.RightButton:
            return self.MODE_OBJECT_ROTATE
        if buttons & QtCore.Qt.LeftButton:
            return self.MODE_CAMERA_ROTATE
        if buttons & QtCore.Qt.MiddleButton:
            return self.MODE_OBJECT_TRANSLATE
        return self.MODE_NONE

    def _begin_mouse_drag_session(self, pos, buttons):
        self._mouse_mode = self._resolve_mouse_mode(buttons)
        self._drag_start_mouse_pos = QtCore.QPoint(pos)
        self._last_mouse_pos = QtCore.QPoint(pos)

        self._drag_start_camera_rot = list(self.params.camera_rot)
        self._drag_start_camera_pos = list(self.params.camera_pos)
        self._drag_camera_rot_for_object = list(self.params.camera_rot)
        self._drag_selected_objects = []
        self._drag_start_object_rots = []
        self._drag_start_object_positions = []
        self._drag_group_screen_center = QtCore.QPointF(
            0.5 * self.width(),
            0.5 * self.height(),
        )

        if self._mouse_mode in (self.MODE_OBJECT_ROTATE, self.MODE_OBJECT_TRANSLATE):
            self._drag_selected_objects = self.params.get_selected_figures()
            self._drag_start_object_rots = [list(figure.rotation) for figure in self._drag_selected_objects]
            self._drag_start_object_positions = [list(figure.translation) for figure in self._drag_selected_objects]

    @staticmethod
    def _apply_group_transform(figures, positions, rotations=None):
        for index, figure in enumerate(figures):
            position = positions[index]
            figure.translation = [position.x(), position.y(), position.z()]
            if rotations is not None:
                rotation = rotations[index]
                figure.rotation = [rotation.x(), rotation.y(), rotation.z()]

    #endregion

    #region Жизненный цикл и фон
    def showEvent(self, event):
        super().showEvent(event)
        if self._renderer_initialized:
            self._resume_background_work()
        else:
            self._schedule_renderer_start()

    def hideEvent(self, event):
        super().hideEvent(event)
        # док спрятан или закрыт — рендер в фоне не нужен,
        # ресурсы GPU и CPU принадлежат Крите
        self._suspend_background_work("hidden")

    def closeEvent(self, event):
        self._shutdown_background_work()
        super().closeEvent(event)

    def _suspend_background_work(self, reason: str):
        self._dbg("render_previewer._suspend_background_work.suspend.log_debug", reason=reason)
        self._frame_timer.stop()
        self._interaction_timer.stop()
        self._splash_timer.stop()
        self._abort_active_render(reason)
        if self.renderer is not None:
            self.renderer.stop_render()
        self._render_in_progress = False
        self._rerender_requested = False

    def _resume_background_work(self):
        self._apply_timer_settings()
        QtCore.QTimer.singleShot(0, self.request_render)

    def _shutdown_background_work(self):
        self._suspend_background_work("close")
        if self.renderer is not None:
            # контекст в GUI-потоке — освобождаем прямо здесь
            self.renderer.release_all()
            self.renderer = None


    #endregion

    #region Публичное API
    def set_scene(self, materials, operations, texture_images=None):
        self.params.set_operations(
            operations=operations,
            materials=materials,
            texture_images=texture_images,
            emit=True,
        )

    def set_scene_root(self, scene_root, materials=None, texture_images=None, rebuild=True):
        self.params.set_scene_root(
            scene_root=scene_root,
            materials=materials,
            texture_images=texture_images,
            rebuild=rebuild,
            emit=True,
        )

    def set_texture_images(self, images):
        self.params.texture_images = list(images or [])
        self.params.emit_scene_changed()

    def set_sdf_dict(self, sdf_dict):
        self.params.sdf_dict = sdf_dict
        self.params.emit_shader_changed()

    def get_figure_index(self, name: str) -> int:
        return self.shader_manager.get_sdf_index(name)

    def get_figure_names(self) -> list[str]:
        return self.shader_manager.get_sdf_names()

    def set_preview_scale(self, scale: float):
        self.params.set_preview_scale(scale)

    def snapshot(self):
        return self._current_image.copy() if self._current_image is not None else None

    #endregion

    #region Отладка
    def _dbg(self, localization_key: str, **values):
        if DEBUG_LOG_ENABLED:
            log_debug(localization.get_text(localization_key, **values))

    #endregion

