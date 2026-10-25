import math
import struct

import moderngl
from PyQt5 import QtCore
from PyQt5.QtGui import QImage, QPainter
from PyQt5.QtCore import pyqtSignal, Qt, QRect, pyqtSlot

from ...config import log_debug, DEBUG_LOG_ENABLED
from ...config import DEFAULT_TEXTURE_MAX_SIZE
from ... import localization
from ..data_classes import Material, Operation, Node, Figure, RGBA
from collections import OrderedDict
from array import array
import math

GOLDEN_ANGLE = 2.39996322972865332

_MATERIAL_FIELDS = ["roughness", "metallic", "subsurface", "sssColor", "transmission", "ior", "emission"] 

_NODE_FIELDS = ["operationType", "figureType", "params"]

_SHADER_PROGRAM_CACHE = {}


class _TextureFitCache:
    """LRU-кеш приведённых к целевому размеру QImage, с бюджетом по байтам.

    Ключ: (QImage.cacheKey(), target_w, target_h). cacheKey() дёшев и
    инвалидируется Qt при любом изменении пикселей изображения.
    """

    def __init__(self, max_bytes: int = 256 * 1024 * 1024):
        """Создать LRU-кеш с указанным лимитом байтов."""
        self._items = OrderedDict()
        self._bytes = 0
        self._max_bytes = max_bytes

    def get(self, key):
        """Получить QImage и отметить запись как недавно использованную."""
        img = self._items.get(key)
        if img is not None:
            self._items.move_to_end(key)  # отметить как недавно использованный
        return img

    def put(self, key, img: QImage):
        """Сохранить QImage, вытеснив старые записи при необходимости."""
        size = img.byteCount()
        if size > self._max_bytes:
            return  # слишком большой объект в кеш не кладём
        old = self._items.pop(key, None)
        if old is not None:
            self._bytes -= old.byteCount()
        while self._bytes + size > self._max_bytes and self._items:
            _, evicted = self._items.popitem(last=False)
            self._bytes -= evicted.byteCount()
        self._items[key] = img
        self._bytes += size

    def clear(self):
        """Очистить сохранённые изображения и счётчик байтов."""
        self._items.clear()
        self._bytes = 0

class GPURenderer(QtCore.QObject):

    frame_ready = pyqtSignal(QImage)            # кадр готов
    init_done = pyqtSignal(bool)                # компиляция завершена (True) или упала (False)
    export_progress_changed = pyqtSignal(int, int)
    export_done = pyqtSignal(object)            # QImage или None


    def __init__(self, parent=None):
        """Инициализировать состояние рендера, кеши и таймер."""
        super().__init__(parent)

        #region Размер кадра и чанки
        self.chunk_size = 16

        self.render_w = 1
        self.render_h = 1
        self.grid_w = 1
        self.grid_h = 1
        self.curr_cx = 0
        self.curr_cy = 0
        
        #endregion
        
        # region constants
        self.LIGHT_MAX_COUNT = 16
        self.MATERIALS_LIST_MAX_LENGTH = 64
        self.NODES_MAX_LENGTH = 128
        self.GLASS_MAX_COUNT = 32
        # endregion

        #region Контекст и GL-объекты
        self.previewScale = 1.0
        self.ctx = None
        self.shader_program = None
        self.vao = None
        self.vbo = None
        self.fbo = None
        self.tbp_texture = None
        self.texture_array = None
        self._texture_array_size = (1, 1, 1)

        #endregion
        
        #region Кеш текстур
        self._fit_cache = _TextureFitCache()
        self._textures_signature = None  # подпись входного набора текстур

        #endregion
        
        #region Состояние кадра
        self._last_image = None
        self._scene_signature = None
        self._frame_seq = 0
        self.test_shader_mode = False
        self._uniform_names = set()
        self._uniforms = {}
        self._initialized = False

        self._has_trajectories = False 
        self._last_node_time = None

        #endregion
        
        #region Таймер
        self.fps_timer = QtCore.QTimer(self)
        self.fps_timer.timeout.connect(self.render_chunk)

        #endregion
        
        self._dbg("renderer_mgl.init.complete.log_debug")


#region test mode

    def set_test_shader_mode(self, enabled: bool):
        """Переключить тестовый шейдер."""
        self.test_shader_mode = bool(enabled)
        self._dbg("renderer_mgl.set_test_shader_mode.changed.log_debug", enabled=self.test_shader_mode)

    def _get_test_fragment_shader(self) -> str:
        """Вернуть тестовый фрагментный шейдер."""
        return """#version 330
precision highp float;
out vec4 fragColor;
void main() {
    fragColor = vec4(0.2, 0.4, 0.8, 1.0);
}
"""

    def _prepare_fragment_shader(self, frag_shader_str: str) -> str:
        """Выбрать тестовый либо исходный шейдер."""
        if self.test_shader_mode:
            test_shader = self._get_test_fragment_shader()
            self._dbg("renderer_mgl._prepare_fragment_shader.using_test_shader.log_debug", orig_len=len(frag_shader_str), test_len=len(test_shader))
            return test_shader
        return frag_shader_str

#endregion

#region Контекст и GL-ресурсы

    def _ensure_context(self):
        """Лениво создать контекст ModernGL."""
        if self.ctx is None:
            self.ctx = moderngl.create_context(standalone=True)
            self._dbg("renderer_mgl._ensure_context.created.log_debug", version_code=self.ctx.version_code)

    def initializeGL(self, frag_shader_str: str, vert_shader_str: str):
        """Создать GL-программу, геометрию и текстуры."""
        self._dbg("renderer_mgl.initialize_gl.begin.log_debug")
        self._ensure_context()

        frag_shader_to_compile = self._prepare_fragment_shader(frag_shader_str)

        self._dbg_state(
            "initializeGL.before",
            vert_len=len(vert_shader_str),
            frag_len=len(frag_shader_to_compile),
            test_shader=self.test_shader_mode,
        )


        # region Кеш программ: 
        # компиляция+линковка — самое дорогое место,
        # выполняем её один раз на уникальный исходник шейдера
        cache_key = (vert_shader_str, frag_shader_to_compile)
        cached = _SHADER_PROGRAM_CACHE.get(cache_key)
        if cached is not None and cached["ctx"] is self.ctx:
            self.shader_program = cached["program"]
            self._dbg("renderer_mgl.initialize_gl.program_cache_hit.log_debug")
        else:
            self.shader_program = self.ctx.program(
                vertex_shader=vert_shader_str,
                fragment_shader=frag_shader_to_compile,
            )
            _SHADER_PROGRAM_CACHE[cache_key] = {"ctx": self.ctx, "program": self.shader_program}
            self._dbg("renderer_mgl.initialize_gl.program_cache_miss.log_debug")
        # endregion

        #region Униформы
        self._uniform_names = set(self.shader_program)
        self._dbg("renderer_mgl.initialize_gl.program_compiled.log_debug", members=len(self._uniform_names))
        self._cache_uniforms()

        self._dbg_uniform_presence([
            "u_resolution", "u_fovYDeg", "u_camPos", "u_camRot", "u_lightCount",
            "u_lights", "u_LightPowerScale", "u_ambientLightColor", "u_ambientLightIntensity",
            "u_showFloor", "u_transparentBackground", "u_maxReflectCount",
            "u_physGlass", "u_areaRayCount", "u_topLightCount", "u_ngonPhase", "u_fastMode", "u_TBP", "u_TBPWidth",
            "u_materialsCount", "u_nodesCount", "u_time", "u_textures",
        ], tag="initializeGL.uniform_presence")

        #endregion
        #region Геометрия
        vertices = struct.pack(
            "8f",
            -1.0, -1.0,
             1.0, -1.0,
            -1.0,  1.0,
             1.0,  1.0,
        )
        self.vbo = self.ctx.buffer(vertices)
        self.vao = self.ctx.vertex_array(
            self.shader_program,
            [(self.vbo, "2f", "in_pos")]
        )

        #endregion
        #region Текстуры
        self._ensure_default_texture_array()
        self._bind_textures()

        #endregion
        #region Сброс состояния
        self._scene_signature = None
        self._last_image = None
        self._has_trajectories = False
        self._last_node_time = None
        self._initialized = True
        #endregion
        self._dbg_state("initializeGL.after")
        self._dbg("renderer_mgl.initialize_gl.done.log_debug")

    def resizeGL(self, w, h, previewScale):
        """Пересоздать FBO и сетку чанков под размер кадра."""
        #region Размер буфера
        self.previewScale = previewScale or 1
        src_w = float(w)
        src_h = float(h)
        w = w / self.previewScale
        h = h / self.previewScale
        self.render_w = max(1, int(w))
        self.render_h = max(1, int(h))

        self._dbg("renderer_mgl.resize_gl.requested.log_debug", src_w=src_w, src_h=src_h, render_w=self.render_w, render_h=self.render_h, preview_scale=self.previewScale)

        #endregion
        #region Пересоздание FBO
        self._ensure_context()

        if self.fbo is not None:
            self.fbo.release()
            self.fbo = None

        self.fbo = self.ctx.simple_framebuffer((self.render_w, self.render_h), components=4, dtype="f1")
        #endregion
        #region Сетка чанков и состояние
        self.grid_w = math.ceil(self.render_w / self.chunk_size)
        self.grid_h = math.ceil(self.render_h / self.chunk_size)
        self.curr_cx = 0
        self.curr_cy = 0
        self._last_image = None
        #endregion
        self._dbg_state(
            "resizeGL.after",
            src_w=src_w,
            src_h=src_h,
            new_render=(self.render_w, self.render_h),
            new_grid=(self.grid_w, self.grid_h),
        )
        self._dbg("renderer_mgl.resize_gl.done.log_debug", grid_w=self.grid_w, grid_h=self.grid_h)

#endregion

#region кеширование

    def _has_uniform(self, name: str) -> bool:
        """Проверить наличие униформы в программе."""
        return name in self._uniform_names

    def _cache_uniform(self, name: str):
        """Получить униформу из программы, если она доступна."""
        if not self._has_uniform(name):
            self._dbg("renderer_mgl._cache_uniform.missing.log_debug", name=name)
            return None
        try:
            uniform = self.shader_program[name]
            self._dbg("renderer_mgl._cache_uniform.cached.log_debug", name=name)
            return uniform
        except Exception as e:
            self._dbg("renderer_mgl._cache_uniform.cache_failed.error.log_debug", name=name, error=e)
            return None

    def _cache_array_uniforms(self, base_name: str, count: int):
        """Закешировать элементы массива униформ."""
        return [self._cache_uniform(f"{base_name}[{i}]") for i in range(count)]

    def _cache_struct_array_uniforms(self, base_name: str, count: int, fields: list[str]):
        """Закешировать поля массива структурных униформ."""
        cached = []
        for i in range(count):
            item = {}
            for field in fields:
                item[field] = self._cache_uniform(f"{base_name}[{i}].{field}")
            cached.append(item)
        return cached

    def _cache_uniforms(self):
        """
        Список кешированных униформ
        """
        self._uniforms = {
            "u_resolution": self._cache_uniform("u_resolution"),
            "u_fovYDeg": self._cache_uniform("u_fovYDeg"),
            "u_camPos": self._cache_uniform("u_camPos"),
            "u_camRot": self._cache_uniform("u_camRot"),
            "u_lightCount": self._cache_uniform("u_lightCount"),
            "u_lights_array": self._cache_uniform("u_lights"),
            "u_lights": self._cache_array_uniforms("u_lights", self.LIGHT_MAX_COUNT),
            "u_LightPowerScale": self._cache_uniform("u_LightPowerScale"),
            "u_ambientLightColor": self._cache_uniform("u_ambientLightColor"),
            "u_ambientLightIntensity": self._cache_uniform("u_ambientLightIntensity"),
            "u_skySunAzimuthDeg": self._cache_uniform("u_skySunAzimuthDeg"),
            "u_skySunElevationDeg": self._cache_uniform("u_skySunElevationDeg"),
            "u_skySunAngRadius": self._cache_uniform("u_skySunAngRadius"),
            "u_skyAirDensity": self._cache_uniform("u_skyAirDensity"),
            "u_showFloor": self._cache_uniform("u_showFloor"),
            "u_transparentBackground": self._cache_uniform("u_transparentBackground"),
            "u_maxReflectCount": self._cache_uniform("u_maxReflectCount"),
            "u_physGlass": self._cache_uniform("u_physGlass"),
            "u_areaRayCount": self._cache_uniform("u_areaRayCount"),
            "u_topLightCount": self._cache_uniform("u_topLightCount"),
            "u_ngonPhase": self._cache_uniform("u_ngonPhase"),
            "u_sampleSeed": self._cache_uniform("u_sampleSeed"),
            "u_dispersion": self._cache_uniform("u_dispersion"),
            "u_sampleCount": self._cache_uniform("u_sampleCount"),
            "u_sampleId": self._cache_uniform("u_sampleId"),
            "u_outputLinear": self._cache_uniform("u_outputLinear"),
            "u_glassCount": self._cache_uniform("u_glassCount"),
            "u_glasses": self._cache_array_uniforms("u_glasses", self.GLASS_MAX_COUNT),
            "u_fastMode": self._cache_uniform("u_fastMode"),
            "u_TBP": self._cache_uniform("u_TBP"),
            "u_TBPWidth": self._cache_uniform("u_TBPWidth"),
            "u_materialsCount": self._cache_uniform("u_materialsCount"),

            "u_materialsList": self._cache_struct_array_uniforms(
                "u_materialsList", self.MATERIALS_LIST_MAX_LENGTH,
                _MATERIAL_FIELDS,
            ),

            "u_textures": self._cache_uniform("u_textures"),
            "u_nodesCount": self._cache_uniform("u_nodesCount"),

            "u_nodes": self._cache_struct_array_uniforms(
                "u_nodes",
                self.NODES_MAX_LENGTH,
                _NODE_FIELDS,
            ),

            "u_time": self._cache_uniform("u_time"),
        }

#endregion

#region запись значений в кешированные униформы

    def _set_cached_uniform(self, uniform_obj, value, debug_name: str):
        """Записать униформу и залогировать ошибку."""
        if uniform_obj is None:
            return
        try:
            uniform_obj.value = value
        except Exception as e:
            self._dbg("renderer_mgl._set_cached_uniform.set_failed.error.log_debug", name=debug_name, value=value, error=e)

    def _set_cached_int_array_values(
        self,
        whole_uniform,
        indexed_uniforms: list,
        data_list: list[int],
        fixed_size: int | None = None,
        fill_value: int = 0,
        debug_name: str = "",
    ):
        """Записать массив целых значений с дополнением."""
        values = [int(v) for v in data_list]

        if fixed_size is not None:
            values = values[:fixed_size]
            if len(values) < fixed_size:
                values.extend([int(fill_value)] * (fixed_size - len(values)))

        if not values:
            values = [int(fill_value)]

        if whole_uniform is not None:
            try:
                whole_uniform.value = tuple(values)
                return
            except Exception as e:
                self._dbg("renderer_mgl._set_cached_int_array_values.whole_array_failed.error.log_debug", name=debug_name, error=e)

        for i, value in enumerate(values):
            if i >= len(indexed_uniforms):
                break
            self._set_cached_uniform(indexed_uniforms[i], value, f"{debug_name}[{i}]")

    def _set_cached_struct_array_values(self, cached_array: list[dict], data_list: list, fields: list[str], array_name: str):
        """Записать поля структурных униформ из словарей."""
        limit = min(len(cached_array), len(data_list))
        for i in range(limit):
            data_dict = data_list[i]
            cached_fields = cached_array[i]
            for field in fields:
                if field not in data_dict:
                    continue
                value = data_dict[field]
                if isinstance(value, list):
                    value = tuple(value)
                self._set_cached_uniform(cached_fields.get(field), value, f"{array_name}[{i}].{field}")

#endregion

# region set_texture_array

    def _ensure_default_texture_array(self):
        """Создать белый слой по умолчанию при отсутствии массива."""
        if self.texture_array is not None:
            return

        data = bytes([255, 255, 255, 255])
        self.texture_array = self.ctx.texture_array((1, 1, 1), 4, data, dtype="f1")
        self.texture_array.filter = (moderngl.LINEAR, moderngl.LINEAR)
        self.texture_array.repeat_x = True
        self.texture_array.repeat_y = True
        self._texture_array_size = (1, 1, 1)
        self._dbg("renderer_mgl._ensure_default_texture_array.created.log_debug")

    def _bind_textures(self):
        """Привязать текстуры и их униформы к GL-слотам."""
        if self.tbp_texture is not None:
            self.tbp_texture.use(location=0)
            self._set_cached_uniform(self._uniforms.get("u_TBP"), 0, "u_TBP")

        if self.texture_array is not None:
            self.texture_array.use(location=1)
            self._set_cached_uniform(self._uniforms.get("u_textures"), 1, "u_textures")

    def _compute_fit_size(self, images: list[QImage]) -> tuple[int, int]:
        """Размер слоя: максимум по списку, вписанный в DEFAULT_TEXTURE_MAX_SIZE."""
        cap = int(DEFAULT_TEXTURE_MAX_SIZE)
        max_w = max(img.width() for img in images)
        max_h = max(img.height() for img in images)
        return min(max_w, cap), min(max_h, cap)

    def _downscale_oversize(self, img: QImage, tw: int, th: int) -> QImage:
        """Только уменьшение текстур, не влезающих в слой. Mip-подобная
        цепочка пополовнений + финальный smooth (KeepAspectRatio)."""
        if img.width() <= tw and img.height() <= th:
            return img
        scale = min(tw / img.width(), th / img.height())
        fw = max(1, int(img.width() * scale + 0.5))
        fh = max(1, int(img.height() * scale + 0.5))
        cur = img.convertToFormat(QImage.Format_RGBA8888)
        while cur.width() // 2 >= fw and cur.height() // 2 >= fh:
            cur = cur.scaled(cur.width() // 2, cur.height() // 2,
                             Qt.KeepAspectRatio, Qt.SmoothTransformation)
        if cur.width() == fw and cur.height() == fh:
            return cur
        return cur.scaled(fw, fh, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def _pad_layer(self, img: QImage, tw: int, th: int) -> QImage:
        """Слой tw x th: контент в (0,0), остаток — репликация края
        (иначе LINEAR-фильтр даст шов, для height — бороздку в геометрии)."""
        w, h = img.width(), img.height()
        if w == tw and h == th:
            return img
        canvas = QImage(tw, th, QImage.Format_RGBA8888)
        canvas.fill(0)
        p = QPainter(canvas)
        p.drawImage(0, 0, img)
        if w < tw:
            p.drawImage(QRect(w, 0, tw - w, h), img, QRect(w - 1, 0, 1, h))
        if h < th:
            p.drawImage(QRect(0, h, w, th - h), img, QRect(0, h - 1, w, 1))
        if w < tw and h < th:
            p.drawImage(QRect(w, h, tw - w, th - h), img,
                        QRect(w - 1, h - 1, 1, 1))
        p.end()
        return canvas

    def _normalize_layer(self, image: QImage, tw: int, th: int) -> QImage:
        """Контент с учётом капа + паддинг до слоя, с кешированием."""
        key = (image.cacheKey(), tw, th)
        cached = self._fit_cache.get(key)
        if cached is not None:
            return cached
        fitted = self._downscale_oversize(image, tw, th)
        layer = self._pad_layer(
            fitted.convertToFormat(QImage.Format_RGBA8888), tw, th)
        self._fit_cache.put(key, layer)
        self._dbg("renderer_mgl._normalize_layer.cache_miss.log_debug", 
                  src_w=image.width(), src_h=image.height(), 
                  tw=tw, th=th,
                  content_w=fitted.width(), content_h=fitted.height())
        return layer

    def _qimage_to_rgba8888_bytes(self, image: QImage):
        """Вернуть размеры QImage и байты в формате RGBA8888."""
        img = image.convertToFormat(QImage.Format_RGBA8888)
        bits = img.constBits()
        bits.setsize(img.byteCount())
        return img.width(), img.height(), bytes(bits)

    def set_texture_array(self, images: list[QImage]):
        """Нормализовать слои с кешем и загрузить их в GL-массив."""
        self._ensure_context()

        #region Пустой список
        if not images:
            self._textures_signature = None
            self._ensure_default_texture_array()
            self._bind_textures()
            return

        #endregion
        #region Подпись набора
        signature = (tuple(img.cacheKey() for img in images), len(images))
        if signature == self._textures_signature and self.texture_array is not None:
            self._bind_textures()
            return

        #endregion
        #region Подготовка слоёв
        tw, th = self._compute_fit_size(images)

        layers_data = []
        for idx, image in enumerate(images):
            layer = self._normalize_layer(image, tw, th)
            w, h, raw = self._qimage_to_rgba8888_bytes(layer)
            layers_data.append(raw)

        #endregion
        #region Загрузка в GL
        depth = len(layers_data)
        blob = b"".join(layers_data)
        new_size = (tw, th, depth)

        if self.texture_array is not None and self._texture_array_size != new_size:
            self.texture_array.release()
            self.texture_array = None
            self._dbg("renderer_mgl.set_texture_array.resized.log_debug", old_size=self._texture_array_size, new_size=new_size)

        if self.texture_array is None:
            self.texture_array = self.ctx.texture_array(
                (tw, th, depth), 4, blob, dtype="f1"
            )
            self.texture_array.filter = (moderngl.LINEAR, moderngl.LINEAR)
            self.texture_array.repeat_x = True
            self.texture_array.repeat_y = True
            self._texture_array_size = new_size
            self._dbg("renderer_mgl.set_texture_array.created.log_debug", size=new_size)
        else:
            self.texture_array.write(blob)
            self._dbg("renderer_mgl.set_texture_array.updated.log_debug", size=new_size)

        #endregion
        #region Фиксация состояния
        self._textures_signature = signature
        self._bind_textures()
        #endregion

# endregion

#region Параметры узлов

    def compress_nodes_params_to_texture(self, nodes: list[Node]):
        """Упаковать параметры узлов в текстуру TBP."""
        #region Упаковка параметров
        MAX_TEXTURE_WIDTH = 1000
        tbp_flat_data = []
        current_offset = 0

        for node in nodes:
            for rgba in node.params:
                tbp_flat_data.extend([rgba.r, rgba.g, rgba.b, rgba.a])
            current_offset += len(node.params)

        total_pixels = current_offset

        if total_pixels == 0:
            tex_width = 1
            tex_height = 1
            tbp_flat_data = [0.0, 0.0, 0.0, 0.0]
            padding_pixels = 0
        else:
            tex_width = min(total_pixels, MAX_TEXTURE_WIDTH)
            tex_height = math.ceil(total_pixels / MAX_TEXTURE_WIDTH)
            padding_pixels = (tex_width * tex_height) - total_pixels

        tbp_flat_data.extend([0.0] * (padding_pixels * 4))

        #endregion
        #region Загрузка TBP
        blob = struct.pack(f"{len(tbp_flat_data)}f", *tbp_flat_data)

        if self.tbp_texture is not None:
            old_size = self.tbp_texture.size
            if old_size != (tex_width, tex_height):
                self.tbp_texture.release()
                self.tbp_texture = None

        if self.tbp_texture is None:
            self.tbp_texture = self.ctx.texture((tex_width, tex_height), 4, blob, dtype="f4")
            self.tbp_texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
            self.tbp_texture.repeat_x = False
            self.tbp_texture.repeat_y = False
        else:
            self.tbp_texture.write(blob)
            self._dbg("renderer_mgl.compress_nodes_params_to_texture.updated.log_debug")

        #endregion
        #region Привязка и ширина
        self._bind_textures()
        self._set_cached_uniform(self._uniforms.get("u_TBPWidth"), int(tex_width), "u_TBPWidth")
        #endregion

#endregion

# region signatures

    def _material_signature(self, materials: list[Material]):
        """Собрать неизменяемую сигнатуру материалов."""
        return tuple(
            (float(mat.roughness), float(mat.metallic), float(mat.transmission),
             float(mat.ior), float(mat.emission),
             float(mat.subsurface),
             tuple(float(v) for v in mat.sss_color))
            for mat in materials
        )

    def _figure_signature(self, figure: Figure):
        """Собрать сигнатуру фигуры с её текстурами."""
        if figure is None:
            return None

        def tex_signature(tex) -> tuple:
            """Вернуть параметры текстуры для сигнатуры фигуры."""
            return (
                int(tex.id),
                tuple(float(v) for v in tex.offset),
                float(tex.rotation),
                tuple(float(v) for v in tex.original_size),
                float(tex.tiling_scale),
                float(getattr(tex, "depth_scale", 0.0)),
            )

        return (
            int(figure.type),
            tuple(float(v) for v in figure.albedo),
            int(figure.material_id),
            tuple(float(v) for v in figure.translation),
            int(figure.register_id),
            tuple(float(v) for v in figure.rotation),
            tuple(float(v) for v in figure.elongation),
            float(figure.scale),
            tuple(tuple(float(x) for x in values) for values in figure.extra_params),
            str(figure.trajectory_fn_id),
            tuple(sorted(figure.trajectory_params.items())),
            tuple(tex_signature(tex) for tex in figure.node_textures),
        )

    def _operation_signature(self, operations: list[Operation]):
        """Собрать сигнатуру списка операций."""
        return tuple(
            (
                int(op.op_type),
                tuple(float(v) for v in op.operands),
                self._figure_signature(op.figure),
            )
            for op in operations
        )

    def _build_scene_signature(self, materials: list[Material], operations: list[Operation]):
        """Собрать общую сигнатуру материалов и операций."""
        return (
            self._material_signature(materials),
            self._operation_signature(operations),
        )

# endregion

# region upload

    def _upload_materials(self, materials):
        """Передать материалы в униформы шейдера."""
        self._set_cached_uniform(self._uniforms.get("u_materialsCount"),
                                 int(len(materials)), "u_materialsCount")
        self._set_cached_struct_array_values(
            self._uniforms["u_materialsList"],
            [mat.to_dict() for mat in materials],
            _MATERIAL_FIELDS, "u_materialsList",
        )

    def _upload_nodes(self, operations, time_val):
        """Пересобрать и загрузить параметры узлов на указанное время."""
        nodes = [op.to_node(t=time_val) for op in operations]
        self.compress_nodes_params_to_texture(nodes)
        self._set_cached_uniform(self._uniforms.get("u_nodesCount"),
                                 int(len(nodes)), "u_nodesCount")
        nodes_data, offset = [], 0
        for node in nodes:
            nodes_data.append({
                "operationType": int(node.operation_type),
                "figureType": int(node.figure_type),
                "params": [float(offset), float(offset + len(node.params))],
            })
            offset += len(node.params)
        self._set_cached_struct_array_values(
            self._uniforms["u_nodes"], nodes_data, _NODE_FIELDS, "u_nodes"
        )
        self._last_node_time = time_val

    def _pass_all_parameters(
        self,
        cam_pos: list[float],
        cam_rot: list[float],
        camera_fov_deg: float,
        ambient_color: list[float],
        ambient_intensity: float,
        sky_sun_azimuth_deg: float,
        sky_sun_elevation_deg: float,
        sky_sun_ang_radius: float,
        sky_air_density: float,
        light_power_scale: float,
        show_floor: bool,
        transparent_bg: bool,
        max_reflect_count: int,
        phys_glass: bool,
        area_ray_count: int,
        top_light_count: int,
        ngon_phase: float,
        sample_seed: int,
        dispersion: float,
        sample_count: int,
        sample_id: int,
        output_linear: bool,
        fast_mode: bool,
        materials: list[Material],
        operations: list[Operation],
        time_val: float,
    ):

        """Передать параметры кадра и обновить изменённые данные сцены."""
        self._bind_textures()

        #region render params
        self._set_cached_uniform(self._uniforms.get("u_resolution"), (float(self.render_w), float(self.render_h)), "u_resolution")
        self._set_cached_uniform(self._uniforms.get("u_fovYDeg"), float(camera_fov_deg), "u_fovYDeg")
        self._set_cached_uniform(self._uniforms.get("u_camPos"), tuple(float(v) for v in cam_pos[:3]), "u_camPos")
        self._set_cached_uniform(self._uniforms.get("u_camRot"), tuple(float(v) for v in cam_rot[:3]), "u_camRot")
        self._set_cached_uniform(self._uniforms.get("u_ambientLightColor"), tuple(float(v) for v in ambient_color[:3]), "u_ambientLightColor")
        self._set_cached_uniform(self._uniforms.get("u_ambientLightIntensity"), float(ambient_intensity), "u_ambientLightIntensity")
        self._set_cached_uniform(self._uniforms.get("u_skySunAzimuthDeg"), float(sky_sun_azimuth_deg), "u_skySunAzimuthDeg")
        self._set_cached_uniform(self._uniforms.get("u_skySunElevationDeg"), float(sky_sun_elevation_deg), "u_skySunElevationDeg")
        self._set_cached_uniform(self._uniforms.get("u_skySunAngRadius"), float(sky_sun_ang_radius), "u_skySunAngRadius")
        self._set_cached_uniform(self._uniforms.get("u_skyAirDensity"), float(sky_air_density), "u_skyAirDensity")
        self._set_cached_uniform(self._uniforms.get("u_showFloor"), int(show_floor), "u_showFloor")
        self._set_cached_uniform(self._uniforms.get("u_transparentBackground"), int(transparent_bg), "u_transparentBackground")
        self._set_cached_uniform(self._uniforms.get("u_maxReflectCount"), int(max_reflect_count), "u_maxReflectCount")
        self._set_cached_uniform(self._uniforms.get("u_physGlass"), bool(phys_glass), "u_physGlass")
        self._set_cached_uniform(self._uniforms.get("u_areaRayCount"), int(area_ray_count), "u_areaRayCount")
        self._set_cached_uniform(self._uniforms.get("u_topLightCount"), int(top_light_count), "u_topLightCount")
        self._set_cached_uniform(self._uniforms.get("u_ngonPhase"), float(ngon_phase), "u_ngonPhase")
        self._set_cached_uniform(self._uniforms.get("u_sampleSeed"), int(sample_seed), "u_sampleSeed")
        self._set_cached_uniform(self._uniforms.get("u_dispersion"), float(dispersion), "u_dispersion")
        self._set_cached_uniform(self._uniforms.get("u_sampleCount"), int(sample_count), "u_sampleCount")
        self._set_cached_uniform(self._uniforms.get("u_sampleId"), int(sample_id), "u_sampleId")
        self._set_cached_uniform(self._uniforms.get("u_outputLinear"), bool(output_linear), "u_outputLinear")
        self._set_cached_uniform(self._uniforms.get("u_fastMode"), bool(fast_mode), "u_fastMode")
        self._set_cached_uniform(self._uniforms.get("u_time"), float(time_val), "u_time")
        self._set_cached_uniform(self._uniforms.get("u_LightPowerScale"), float(light_power_scale), "u_LightPowerScale")
        #endregion

        #region lights array
        lights = []
        for i, op in enumerate(operations):
            if op.figure is not None and op.figure.material_id < len(materials):
                material = materials[op.figure.material_id]
                if material.emission > 0.0:
                    lights.append(i)

        if len(lights) > self.LIGHT_MAX_COUNT:
            self._dbg("renderer_mgl._pass_all_parameters.lights_limited.log_debug", limit=self.LIGHT_MAX_COUNT, count=len(lights))
            lights = lights[:self.LIGHT_MAX_COUNT]

        self._set_cached_uniform(
            self._uniforms.get("u_lightCount"),
            int(len(lights)),
            "u_lightCount",
        )
        self._set_cached_int_array_values(
            whole_uniform=self._uniforms.get("u_lights_array"),
            indexed_uniforms=self._uniforms.get("u_lights", []),
            data_list=lights,
            fixed_size=self.LIGHT_MAX_COUNT,
            fill_value=0,
            debug_name="u_lights",
        )
        #endregion

        #region glasses array
        glasses = []
        for i, mat in enumerate(operations):
            if mat.figure is not None and mat.figure.material_id < len(materials):
                material = materials[mat.figure.material_id]
                if material.transmission > 0.0:
                    glasses.append(i)

        if len(glasses) > self.GLASS_MAX_COUNT:
            glasses = glasses[:self.GLASS_MAX_COUNT]

        self._set_cached_uniform(
            self._uniforms.get("u_glassCount"),
            int(len(glasses)),
            "u_glassCount",
        )

        self._set_cached_int_array_values(
            whole_uniform=self._uniforms.get("u_glasses"),
            indexed_uniforms=self._uniforms.get("u_glasses", []),
            data_list=glasses,
            fixed_size=self.GLASS_MAX_COUNT,
            fill_value=0,
            debug_name="u_glasses",
        )

        #endregion


        #region Обновление сцены
        scene_signature = self._build_scene_signature(materials, operations)
        if scene_signature != self._scene_signature:
            self._scene_signature = scene_signature
            self._has_trajectories = any(
                op.figure is not None and op.figure.trajectory_fn is not None
                for op in operations
            )
            self._upload_materials(materials)
            self._upload_nodes(operations, time_val)
        elif self._has_trajectories and time_val != self._last_node_time:
            # Траектории — функция времени: узлы пересобираем каждый кадр,
            # материалы и юниформы не трогаем. На паузе time_val заморожен — пропуск.
            self._upload_nodes(operations, time_val)
        else:
            self._bind_textures()
        #endregion

# endregion

#region Цикл рендера

    def paintGL(self):
        """Нарисовать один чанк и сообщить о завершении кадра."""
        #region Проверка состояния
        if self.fbo is None or self.shader_program is None or self.vao is None:
            self._dbg("renderer_mgl.paint_gl.early_exit.log_debug")
            return True

        if self.curr_cy >= self.grid_h:
            return True


        #endregion
        #region Подготовка FBO
        self.fbo.use()
        self.ctx.viewport = (0, 0, self.render_w, self.render_h)

        if self.curr_cx == 0 and self.curr_cy == 0:
            self.ctx.scissor = None
            self.ctx.clear(0.0, 0.0, 0.0, 0.0)

        #endregion
        #region Отрисовка чанка
        x = self.curr_cx * self.chunk_size
        y = self.curr_cy * self.chunk_size
        w = min(self.chunk_size, self.render_w - x)
        h = min(self.chunk_size, self.render_h - y)

        self._bind_textures()

        self.ctx.scissor = (x, y, w, h)
        self.vao.render(mode=moderngl.TRIANGLE_STRIP)
        self.ctx.scissor = None

        #endregion
        #region Продвижение по сетке
        self.curr_cx += 1
        if self.curr_cx >= self.grid_w:
            self.curr_cx = 0
            self.curr_cy += 1

        done = self.curr_cy >= self.grid_h
        return done
        #endregion

    def _capture_current_image(self):
        """Считать и скопировать кадр из FBO в QImage."""
        if self.fbo is None:
            self._last_image = None
            self._dbg("renderer_mgl._capture_current_image.fbo_missing.log_debug")
            return

        raw = self.fbo.read(components=4, alignment=1, dtype="f1")
        img = QImage(raw, self.render_w, self.render_h, QImage.Format_RGBA8888).mirrored()
        self._last_image = img.copy()

    def start_render(self):
        """Сбросить позицию чанков и запустить таймер рендера."""
        self.curr_cx = 0
        self.curr_cy = 0
        self._last_image = None
        self._frame_seq += 1
        self.fps_timer.start(0)

    def render_chunk(self):
        """Отрисовать чанк и отправить готовый кадр сигналом."""
        done = self.paintGL()
        if done:
            self._capture_current_image()
            self.fps_timer.stop()
            if self._last_image is not None:
                self.frame_ready.emit(self._last_image.copy())

    def get_image(self):
        """Вернуть копию последнего готового кадра или None."""
        if self._last_image is None:
            self._dbg("renderer_mgl.get_image.no_image.log_debug")
            return None
        self._dbg("renderer_mgl.get_image.returning.log_debug", 
                  width=self._last_image.width(), height=self._last_image.height())
        return self._last_image.copy()

#endregion

#region Публичные слоты

    @pyqtSlot(dict)
    def initialize(self, payload: dict):
        """Инициализировать рендерер из payload и отправить результат."""
        try:
            self.set_test_shader_mode(False)
            self.initializeGL(payload["frag_code"], payload["vert_code"])
            self.chunk_size = int(payload["chunk_size"])
            self.resizeGL(payload["w"], payload["h"], float(payload["preview_scale"]))
            self.init_done.emit(True)
        except Exception as e:
            self._dbg("renderer_mgl.initialize.failed.error.log_debug", error=e)
            self.init_done.emit(False)

    @pyqtSlot(dict)
    def resize(self, payload: dict):
        """Применить размер и настройки чанков из payload."""
        self.chunk_size = int(payload["chunk_size"])
        self.resizeGL(payload["w"], payload["h"], float(payload["preview_scale"]))

    @pyqtSlot(dict)
    def start_frame(self, payload: dict):
        # снапшот всех параметров кадра; работаем только в своём потоке
        """Загрузить текстуры и параметры, затем начать кадр."""
        if payload.get("textures") is not None:
            self.set_texture_array(payload["textures"])
        self._pass_all_parameters(**payload["params"])
        self.curr_cx = 0
        self.curr_cy = 0
        self.fps_timer.start(max(1, int(payload["chunk_interval_ms"])))

    @pyqtSlot()
    def stop_render(self):
        """Остановить таймер отрисовки."""
        self.fps_timer.stop()

#endregion

#region Экспорт кадра

    @pyqtSlot(dict)
    def start_export(self, payload: dict):
        """Запустить экспорт с возможным накоплением сэмплов."""
        try:
            #region Настройка размера
            self.set_test_shader_mode(False)
            self.chunk_size = int(payload["chunk_size"])
            self.resizeGL(payload["w"], payload["h"], 1.0)

            #endregion
            #region Накопление сэмплов
            self._accum_total = int(payload.get("accum_frames", 1))
            self._accum_index = 0
            self._accum_buf = None
            self._export_params = dict(payload["params"])

            if self._accum_total > 1:
                # float-FBO: линейный HDR поток для накопления.
                # resizeGL выше уже создал f1 — пересоздаём ПОСЛЕ него
                if not self._has_uniform("u_outputLinear"):
                    self._dbg("renderer_mgl.start_export.linear_uniform_missing.log_debug")
                if self.fbo is not None:
                    self.fbo.release()
                self.fbo = self.ctx.simple_framebuffer(
                    (self.render_w, self.render_h), components=4, dtype="f4")
                self._export_params["output_linear"] = True
                self._export_params.setdefault("sample_seed", 0)
                self._accum_base_phase = float(
                    self._export_params.get("ngon_phase", 0.0))

            #endregion
            #region Параметры и прогресс
            self._pass_all_parameters(**payload["params"])
            self.curr_cx = 0
            self.curr_cy = 0
            self._grid_total = self.grid_w * self.grid_h
            self._export_total = self._grid_total * self._accum_total

            #endregion
            #region Запуск таймера
            self.export_progress_changed.emit(0, self._export_total)
            try:
                self.fps_timer.timeout.disconnect(self.render_chunk)
            except TypeError:
                pass
            self.fps_timer.timeout.connect(self._export_tick)
            self.fps_timer.start(max(1, int(payload["chunk_interval_ms"])))
            #endregion
        except Exception as e:
            self._dbg("renderer_mgl.start_export.failed.error.log_debug", error=e)
            try:
                self.fps_timer.timeout.disconnect(self._export_tick)
            except TypeError:
                pass
            self.fps_timer.timeout.connect(self.render_chunk)
            self.fps_timer.stop()
            self.export_done.emit(None)  # превьювер отпустит _export_state

    def _export_tick(self):
        """Отрисовать чанк экспорта и обновить прогресс."""
        #region Чанк и прогресс
        done = self.paintGL()
        done_chunks = min(
            self._accum_index * self._grid_total
            + self.grid_w * self.curr_cy + self.curr_cx,
            self._export_total)
        self.export_progress_changed.emit(done_chunks, self._export_total)
        if not done:
            return

        #endregion
        #region Накопление и сборка кадра
        image = None
        if self._accum_total > 1:
            self._accum_frame()
            if self._accum_index < self._accum_total - 1:
                # следующий сэмпл: новый сид + золотой угол фазы,
                # time_val не трогаем — время заморожено
                self._accum_index += 1
                self._export_params["sample_count"] = self._accum_total
                self._export_params["sample_seed"] = self._accum_index
                self._export_params["sample_id"] = self._accum_index
                self._export_params["ngon_phase"] = (
                    self._accum_base_phase + GOLDEN_ANGLE * self._accum_index)
                self._pass_all_parameters(**self._export_params)
                self.curr_cx = 0
                self.curr_cy = 0  # paintGL сам очистит FBO на первом чанке
                return
            image = self._accum_finish()
            # FBO обратно в f1 — превью читает восьмибитно
            if self.fbo is not None:
                self.fbo.release()
            self.fbo = self.ctx.simple_framebuffer(
                (self.render_w, self.render_h), components=4, dtype="f1")
        else:
            self._capture_current_image()
            if self._last_image is not None:
                image = self._last_image.copy()

        #endregion
        #region Завершение экспорта
        self.fps_timer.stop()
        try:
            self.fps_timer.timeout.disconnect(self._export_tick)
        except TypeError:
            pass
        self.fps_timer.timeout.connect(self.render_chunk)
        self.export_done.emit(image)
        #endregion

    def _accum_frame(self):
        """Добавить значения float-FBO к накопленному кадру."""
        raw = self.fbo.read(components=4, alignment=1, dtype='f4')
        frame = array('f', raw)
        bad = 0
        for i, v in enumerate(frame):
            if not math.isfinite(v):
                frame[i] = 0.0
                bad += 1
        if bad:
            self._dbg("renderer_mgl._accum_frame.non_finite.log_debug", bad=bad, index=self._accum_index)
        if self._accum_buf is None:
            self._accum_buf = frame
        else:
            self._accum_buf = array(
                "f", (a + b for a, b in zip(self._accum_buf, frame)))

    def _accum_finish(self):
        """Усреднить кадры и применить тональную кривую ACES."""
        #region Подготовка буфера
        inv = 1.0 / float(self._accum_total)
        buf = self._accum_buf
        w, h = self.render_w, self.render_h
        out = bytearray(w * h * 4)
        #endregion
        #region ACES и запись пикселей
        for y in range(h):
            src = y * w * 4
            dst = (h - 1 - y) * w * 4  # fbo.read снизу вверх — зеркалим
            for x in range(w):
                i = src + x * 4
                r, g, b = _aces_tonemap_py(
                    buf[i] * inv, buf[i + 1] * inv, buf[i + 2] * inv)
                a = buf[i + 3] * inv
                a = 0.0 if a < 0.0 else (1.0 if a > 1.0 else a)
                o = dst + x * 4
                out[o] = int(r * 255.0 + 0.5)
                out[o + 1] = int(g * 255.0 + 0.5)
                out[o + 2] = int(b * 255.0 + 0.5)
                out[o + 3] = int(a * 255.0 + 0.5)
        #endregion
        return QImage(bytes(out), w, h, QImage.Format_RGBA8888).copy()

#endregion

# --------------------------------------------------------------------------
# --- GPURenderer: helpers ---
# --------------------------------------------------------------------------
#region
    #region Освобождение ресурсов
    @pyqtSlot()
    def release_all(self):
        """Освободить все GL-объекты. Вызывать из потока, владеющего контекстом."""
        #region GL-объекты
        for name in ("fbo", "texture_array", "tbp_texture", "vbo", "vao"):
            obj = getattr(self, name, None)
            if obj is not None:
                try:
                    obj.release()
                except Exception as e:
                    self._dbg("renderer_mgl.release_all.object_failed.error.log_debug", name=name, error=e)
                setattr(self, name, None)
        if self.shader_program is not None:
            try:
                self.shader_program.release()
            except Exception as e:
                self._dbg("renderer_mgl.release_all.program_failed.error.log_debug", error=e)
            self.shader_program = None
        #endregion
        # кэш программ ссылается на мёртвые объекты — чистим
        #region Кеши
        for key in [k for k, v in _SHADER_PROGRAM_CACHE.items() if v["ctx"] is self.ctx]:
            _SHADER_PROGRAM_CACHE.pop(key, None)

        self._fit_cache.clear()
        self._textures_signature = None

        #endregion
        self._initialized = False
        self._dbg("renderer_mgl.release_all.done.log_debug")

    #endregion

    #region Отладка

    def _dbg(self, localization_key: str, **values):
        if not DEBUG_LOG_ENABLED:
            return
        log_debug(localization.get_text(localization_key, **values))

    def _state_dict(self):
        """Вернуть диагностический снимок состояния рендерера."""
        if not DEBUG_LOG_ENABLED:
            return None
        return {
            "initialized": self._initialized,
            "frame_seq": self._frame_seq,
            "timer_active": bool(self.fps_timer.isActive()),
            "render_w": int(self.render_w),
            "render_h": int(self.render_h),
            "grid_w": int(self.grid_w),
            "grid_h": int(self.grid_h),
            "curr_cx": int(self.curr_cx),
            "curr_cy": int(self.curr_cy),
            "chunk_size": int(self.chunk_size),
            "previewScale": float(self.previewScale),
            "has_ctx": self.ctx is not None,
            "has_program": self.shader_program is not None,
            "has_vao": self.vao is not None,
            "has_fbo": self.fbo is not None,
            "has_tbp": self.tbp_texture is not None,
            "has_tex_array": self.texture_array is not None,
            "texture_array_size": self._texture_array_size,
            "has_last_image": self._last_image is not None,
        }

    def _dbg_state(self, tag: str, **extra):
        """Вывести снимок состояния с дополнительными полями."""
        if not DEBUG_LOG_ENABLED:
            return
        payload = self._state_dict()
        payload.update(extra)
        self._dbg("renderer_mgl._dbg_state.dump.log_debug", tag=tag, payload=payload)

    def _dbg_uniform_presence(self, names: list[str], tag: str = "uniform_presence"):
        """Проверить и вывести наличие заданных униформ."""
        if not DEBUG_LOG_ENABLED:
            return
        data = {name: self._has_uniform(name) for name in names}
        self._dbg("renderer_mgl._dbg_uniform_presence.dump.log_debug", tag=tag, data=data)

    def _dbg_scene_summary(self, materials, operations, lights):
        """Вывести диагностическое описание сцены."""
        if not DEBUG_LOG_ENABLED:
            return

        material_rows = []
        for i, mat in enumerate(materials):
            material_rows.append({
                "i": i,
                "roughness": float(mat.roughness),
                "metallic": float(mat.metallic),
                "transmission": float(mat.transmission),
                "ior": float(mat.ior),
                "emission": float(mat.emission),
                "sss": tuple(float(v) for v in mat.sss_color),
            })

        op_rows = []
        for i, op in enumerate(operations):
            fig = op.figure
            op_rows.append({
                "i": i,
                "op_type": int(op.type),
                "operands": tuple(float(v) for v in op.operands),
                "has_figure": fig is not None,
                "figure_type": None if fig is None else int(fig.type),
                "material_id": None if fig is None else int(fig.material_id),
                "translation": None if fig is None else tuple(float(v) for v in fig.translation),
                "rotation": None if fig is None else tuple(float(v) for v in fig.rotation),
                "scale": None if fig is None else float(fig.scale),
                "extra_params_count": 0 if fig is None else len(fig.extra_params),
            })

        self._dbg("renderer_mgl._dbg_scene_summary.materials.log_debug", rows=material_rows)
        self._dbg("renderer_mgl._dbg_scene_summary.operations.log_debug", rows=op_rows)
        self._dbg("renderer_mgl._dbg_scene_summary.lights.log_debug", lights=lights)

    #endregion

#endregion




#Цена без numpy: накопление — порядка секунды на кадр 1080p, финальный
# ACES-проход — несколько секунд одним циклом. Для пакетного экспорта
# терпимо; если станет узким местом — оптимизировать финал отдельно.

def _aces_film_py(x):
    """Применить скалярную кривую ACES."""
    v = (x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14)
    return 0.0 if v < 0.0 else (1.0 if v > 1.0 else v)

def _aces_tonemap_py(r, g, b):
    # зеркало ACESFilmToneMap: hue-preserving путь с desat в HDR
    """Тонмапить RGB с сохранением оттенка и HDR-десатурацией."""
    peak = r if r > g else g
    if b > peak:
        peak = b
    if peak <= 1e-5:
        return _aces_film_py(r), _aces_film_py(g), _aces_film_py(b)
    mapped = _aces_film_py(peak)
    t = (peak - 1.0) / 7.0
    t = 0.0 if t < 0.0 else (1.0 if t > 1.0 else t)
    desat = t * t * (3.0 - 2.0 * t) * 0.85  # smoothstep(1, 8, peak) * 0.85
    inv_peak = 1.0 / peak
    res = []
    for ch in (r, g, b):
        v = ch * inv_peak * mapped * (1.0 - desat) + mapped * desat
        res.append(0.0 if v < 0.0 else (1.0 if v > 1.0 else v))
    return res[0], res[1], res[2]