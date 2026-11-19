"""
Реализация SDF-рендерера SDForge поверх модульного ядра pipeline_base.py.

Полностью повторяет внешний контракт и поведение GPURenderer из renderer_mgl.py
(чанки превью, кеширование текстур и TBP, сигнатуры сцены, траектории,
экспорт с HDR-накоплением и ACES-тонмаппингом), открывая доступ к динамическому
редактированию этапов пайплайна ("preview", "export"), дебаг-режиму и
опциональному запуску в отдельном потоке (use_thread=True).
"""

import math
import struct
from array import array
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

import moderngl
from PyQt5.QtCore import QRect, Qt
from PyQt5.QtGui import QImage, QPainter

from ... import localization
from ...config import DEBUG_LOG_ENABLED, DEFAULT_TEXTURE_MAX_SIZE, log_debug
from ..data_classes import Figure, Material, Node, Operation, RGBA
from .pipeline_base import (
    BasePipelineRenderer,
    CPUStage,
    FragmentStage,
    PipelineContext,
    RenderDebugConfig,
    RenderPipeline,
    RepeatStage,
    UniformAccessor,
)


GOLDEN_ANGLE = 2.39996322972865332

_MATERIAL_FIELDS = (
    "roughness",
    "metallic",
    "subsurface",
    "sssColor",
    "transmission",
    "ior",
    "emission",
)

_NODE_FIELDS = ("operationType", "figureType", "params")

_EXPECTED_UNIFORMS = [
    "u_resolution", "u_fovYDeg", "u_camPos", "u_camRot", "u_lightCount",
    "u_lights", "u_LightPowerScale", "u_ambientLightColor", "u_ambientLightIntensity",
    "u_showFloor", "u_transparentBackground", "u_maxReflectCount",
    "u_physGlass", "u_areaRayCount", "u_topLightCount", "u_ngonPhase",
    "u_fastMode", "u_TBP", "u_TBPWidth", "u_materialsCount", "u_nodesCount",
    "u_time", "u_textures",
]


#region Иммутабельные снимки сцены для безопасной передачи между потоками

@dataclass(frozen=True)
class _FrozenTextureInput:
    """Обёртка QImage с сохранённым исходным cacheKey() до пересечения границы потока."""
    cache_key: int
    image: QImage

    def cacheKey(self) -> int:
        return self.cache_key

    def width(self) -> int:
        return self.image.width()

    def height(self) -> int:
        return self.image.height()


@dataclass(frozen=True)
class _FrozenTexSlot:
    id: int
    offset: tuple[float, float]
    rotation: float
    original_size: tuple[float, float]
    tiling_scale: float
    depth_scale: float = 0.0
    is_heightmap: bool = False


@dataclass(frozen=True)
class _FrozenMaterial:
    roughness: float
    metallic: float
    transmission: float
    subsurface: float
    sss_color: tuple[float, float, float]
    ior: float
    emission: float

    @classmethod
    def from_material(cls, mat: Material) -> "_FrozenMaterial":
        sc = mat.sss_color or (1.0, 0.5, 0.25)
        return cls(
            roughness=float(mat.roughness),
            metallic=float(mat.metallic),
            transmission=float(mat.transmission),
            subsurface=float(mat.subsurface),
            sss_color=(float(sc[0]), float(sc[1]), float(sc[2])),
            ior=float(mat.ior),
            emission=float(mat.emission),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "roughness": self.roughness,
            "metallic": self.metallic,
            "transmission": self.transmission,
            "subsurface": self.subsurface,
            "sssColor": self.sss_color,
            "ior": self.ior,
            "emission": self.emission,
        }


@dataclass(frozen=True)
class _FrozenFigure:
    type: int
    albedo: tuple[float, float, float]
    material_id: int
    translation: tuple[float, float, float]
    register_id: int
    rotation: tuple[float, float, float]
    elongation: tuple[float, float, float]
    scale: float
    node_textures: tuple[_FrozenTexSlot, _FrozenTexSlot, _FrozenTexSlot]
    extra_params: tuple[tuple[float, ...], ...]
    trajectory_fn_id: str
    trajectory_params: tuple[tuple[str, Any], ...]
    trajectory_fn: Any

    @classmethod
    def from_figure(cls, fig: Figure) -> "_FrozenFigure":
        slots = []
        for idx, tex in enumerate(fig.node_textures[:3]):
            has_depth = hasattr(tex, "depth_scale")
            slots.append(
                _FrozenTexSlot(
                    id=int(tex.id),
                    offset=(float(tex.offset[0]), float(tex.offset[1])),
                    rotation=float(tex.rotation),
                    original_size=(float(tex.original_size[0]), float(tex.original_size[1])),
                    tiling_scale=float(tex.tiling_scale),
                    depth_scale=float(getattr(tex, "depth_scale", 0.0)),
                    is_heightmap=(idx == 2 or has_depth),
                )
            )
        t_params = tuple(sorted((str(k), v) for k, v in (fig.trajectory_params or {}).items()))
        return cls(
            type=int(fig.type),
            albedo=(float(fig.albedo[0]), float(fig.albedo[1]), float(fig.albedo[2])),
            material_id=int(fig.material_id),
            translation=(float(fig.translation[0]), float(fig.translation[1]), float(fig.translation[2])),
            register_id=int(fig.register_id),
            rotation=(float(fig.rotation[0]), float(fig.rotation[1]), float(fig.rotation[2])),
            elongation=(float(fig.elongation[0]), float(fig.elongation[1]), float(fig.elongation[2])),
            scale=float(fig.scale),
            node_textures=(slots[0], slots[1], slots[2]),
            extra_params=tuple(tuple(float(x) for x in row) for row in (fig.extra_params or ())),
            trajectory_fn_id=str(fig.trajectory_fn_id or ""),
            trajectory_params=t_params,
            trajectory_fn=fig.trajectory_fn,
        )

    def to_node(self, t: float | None = None) -> Node:
        pos = list(self.translation)
        rot = list(self.rotation)

        if t is not None and self.trajectory_fn is not None and callable(self.trajectory_fn):
            try:
                pos, rot = self.trajectory_fn(
                    t, list(self.translation), list(self.rotation), **dict(self.trajectory_params)
                )
            except Exception as exc:
                log_debug(f"Error in frozen Figure trajectory_fn: {exc}")

        albedo_tex, normal_tex, height_tex = self.node_textures
        shift = 1
        albedo_shift = normal_shift = height_shift = 0
        if albedo_tex.id >= 0:
            albedo_shift = shift
            shift += 1
        if normal_tex.id >= 0:
            normal_shift = shift
            shift += 1
        if height_tex.id >= 0:
            height_shift = shift

        node_textures_id = [-1, -1, -1]
        texture_params = []
        if albedo_tex.id >= 0:
            node_textures_id[0] = albedo_shift * 100 + albedo_tex.id
            texture_params.append((albedo_tex.offset[0], albedo_tex.offset[1], albedo_tex.rotation, 0.0))
            texture_params.append((albedo_tex.original_size[0], albedo_tex.original_size[1], albedo_tex.tiling_scale, 0.0))
        if normal_tex.id >= 0:
            node_textures_id[1] = normal_shift * 100 + normal_tex.id
            texture_params.append((normal_tex.offset[0], normal_tex.offset[1], normal_tex.rotation, 0.0))
            texture_params.append((normal_tex.original_size[0], normal_tex.original_size[1], normal_tex.tiling_scale, 0.0))
        if height_tex.id >= 0:
            node_textures_id[2] = height_shift * 100 + height_tex.id
            texture_params.append((height_tex.offset[0], height_tex.offset[1], height_tex.rotation, 0.0))
            texture_params.append((height_tex.original_size[0], height_tex.original_size[1], height_tex.tiling_scale, height_tex.depth_scale))

        params = [
            RGBA(self.albedo[0], self.albedo[1], self.albedo[2], float(self.material_id)),
            RGBA(float(pos[0]), float(pos[1]), float(pos[2]), float(self.register_id)),
            RGBA(float(rot[0]), float(rot[1]), float(rot[2]), 0.0),
            RGBA(self.elongation[0], self.elongation[1], self.elongation[2], self.scale),
            RGBA(float(node_textures_id[0]), float(node_textures_id[1]), float(node_textures_id[2]), 0.0),
        ]
        for values in texture_params:
            params.append(RGBA(values[0], values[1], values[2], values[3]))
        for values in self.extra_params:
            if len(values) == 4:
                params.append(RGBA(values[0], values[1], values[2], values[3]))
        return Node(0, self.type, params)


@dataclass(frozen=True)
class _FrozenOperation:
    op_type: int
    operands: tuple[float, float, float, float]
    figure: _FrozenFigure | None

    @classmethod
    def from_operation(cls, op: Operation) -> "_FrozenOperation":
        ops = op.operands
        return cls(
            op_type=int(op.op_type),
            operands=(float(ops[0]), float(ops[1]), float(ops[2]), float(ops[3])),
            figure=_FrozenFigure.from_figure(op.figure) if op.figure is not None else None,
        )

    def to_node(self, t: float | None = None) -> Node:
        params = [RGBA(self.operands[0], self.operands[1], self.operands[2], self.operands[3])]
        fig_type = -1
        if self.figure is not None:
            fig_type = self.figure.type if self.figure.type >= 0 else -1
            params.extend(self.figure.to_node(t).params)
        return Node(self.op_type, fig_type, params)

#endregion


#region Кеш текстур и меж-кадровое состояние SDF-сцены

class _TextureFitCache:
    """LRU-кеш приведённых к размеру слоя QImage с бюджетом по байтам."""

    def __init__(self, max_bytes: int = 256 * 1024 * 1024):
        self._items: OrderedDict[tuple, QImage] = OrderedDict()
        self._bytes: int = 0
        self._max_bytes: int = max_bytes

    def get(self, key: tuple) -> QImage | None:
        img = self._items.get(key)
        if img is not None:
            self._items.move_to_end(key)
        return img

    def put(self, key: tuple, img: QImage) -> None:
        size = img.byteCount()
        if size > self._max_bytes:
            return
        old = self._items.pop(key, None)
        if old is not None:
            self._bytes -= old.byteCount()
        while self._bytes + size > self._max_bytes and self._items:
            _, evicted = self._items.popitem(last=False)
            self._bytes -= evicted.byteCount()
        self._items[key] = img
        self._bytes += size

    def clear(self) -> None:
        self._items.clear()
        self._bytes = 0


class _SDFPersistentState:
    """Состояние шейдеров, кешей текстур и ревизий сцены внутри потока контекста."""

    LIGHT_MAX_COUNT = 16
    MATERIALS_LIST_MAX_LENGTH = 64
    NODES_MAX_LENGTH = 128
    GLASS_MAX_COUNT = 32

    def __init__(self, dbg_fn):
        self._dbg = dbg_fn
        self.vert_code: str = ""
        self.frag_code: str = ""

        self.fit_cache = _TextureFitCache()
        self.textures_signature: tuple | None = None
        self.texture_array_size: tuple[int, int, int] = (1, 1, 1)

        self.scene_signature: tuple | None = None
        self.has_trajectories: bool = False
        self.last_node_time: float | None = None

        self.materials_dicts: list[dict[str, Any]] = []
        self.nodes_dicts: list[dict[str, Any]] = []
        self.tbp_width: int = 1
        self.materials_rev: int = 0
        self.nodes_rev: int = 0
        self.program_synced_revs: dict[int, tuple[int, int]] = {}

        # Восстановление геометрии превью после экспорта
        self.saved_preview_metrics: tuple[float, float, float, int] | None = None

    def reset_scene_tracking(self) -> None:
        self.scene_signature = None
        self.has_trajectories = False
        self.last_node_time = None
        self.program_synced_revs.clear()

    def clear_all(self) -> None:
        self.fit_cache.clear()
        self.textures_signature = None
        self.reset_scene_tracking()
        self.materials_dicts.clear()
        self.nodes_dicts.clear()

    # --- Подготовка TextureArray ---

    def ensure_default_texture_array(self, pctx: PipelineContext) -> None:
        if "scene_textures" in pctx.pool.textures:
            return
        white = bytes([255, 255, 255, 255])
        pctx.pool.ensure_texture_array(
            "scene_textures",
            size=(1, 1, 1),
            components=4,
            data=white,
            dtype="f1",
            filter_mode=(moderngl.LINEAR, moderngl.LINEAR),
            repeat_x=True,
            repeat_y=True,
        )
        self.texture_array_size = (1, 1, 1)
        self._dbg("renderer_mgl._ensure_default_texture_array.created.log_debug")

    def update_texture_array(self, pctx: PipelineContext, images: list | None) -> None:
        if not images:
            self.textures_signature = None
            self.ensure_default_texture_array(pctx)
            return

        signature = (tuple(img.cacheKey() for img in images), len(images))
        if signature == self.textures_signature and "scene_textures" in pctx.pool.textures:
            return

        cap = int(DEFAULT_TEXTURE_MAX_SIZE)
        tw = min(max(img.width() for img in images), cap)
        th = min(max(img.height() for img in images), cap)

        layers_data = []
        for item in images:
            raw_qimg = item.image if isinstance(item, _FrozenTextureInput) else item
            ckey = item.cacheKey()
            layer = self._normalize_layer(raw_qimg, ckey, tw, th)
            layers_data.append(self._qimage_to_rgba8888_bytes(layer))

        depth = len(layers_data)
        blob = b"".join(layers_data)
        new_size = (tw, th, depth)

        if "scene_textures" in pctx.pool.textures and self.texture_array_size != new_size:
            self._dbg(
                "renderer_mgl.set_texture_array.resized.log_debug",
                old_size=self.texture_array_size,
                new_size=new_size,
            )

        _, created = pctx.pool.ensure_texture_array(
            "scene_textures",
            size=new_size,
            components=4,
            data=blob,
            dtype="f1",
            filter_mode=(moderngl.LINEAR, moderngl.LINEAR),
            repeat_x=True,
            repeat_y=True,
        )
        self.texture_array_size = new_size
        self.textures_signature = signature
        if created:
            self._dbg("renderer_mgl.set_texture_array.created.log_debug", size=new_size)
        else:
            self._dbg("renderer_mgl.set_texture_array.updated.log_debug", size=new_size)

    def _normalize_layer(self, image: QImage, cache_key: int, tw: int, th: int) -> QImage:
        key = (cache_key, tw, th)
        cached = self.fit_cache.get(key)
        if cached is not None:
            return cached
        fitted = self._downscale_oversize(image, tw, th)
        layer = self._pad_layer(fitted.convertToFormat(QImage.Format_RGBA8888), tw, th)
        self.fit_cache.put(key, layer)
        self._dbg(
            "renderer_mgl._normalize_layer.cache_miss.log_debug",
            src_w=image.width(),
            src_h=image.height(),
            tw=tw,
            th=th,
            content_w=fitted.width(),
            content_h=fitted.height(),
        )
        return layer

    @staticmethod
    def _downscale_oversize(img: QImage, tw: int, th: int) -> QImage:
        if img.width() <= tw and img.height() <= th:
            return img
        scale = min(tw / img.width(), th / img.height())
        fw = max(1, int(img.width() * scale + 0.5))
        fh = max(1, int(img.height() * scale + 0.5))
        cur = img.convertToFormat(QImage.Format_RGBA8888)
        while cur.width() // 2 >= fw and cur.height() // 2 >= fh:
            cur = cur.scaled(
                cur.width() // 2, cur.height() // 2, Qt.KeepAspectRatio, Qt.SmoothTransformation
            )
        if cur.width() == fw and cur.height() == fh:
            return cur
        return cur.scaled(fw, fh, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    @staticmethod
    def _pad_layer(img: QImage, tw: int, th: int) -> QImage:
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
            p.drawImage(QRect(w, h, tw - w, th - h), img, QRect(w - 1, h - 1, 1, 1))
        p.end()
        return canvas

    @staticmethod
    def _qimage_to_rgba8888_bytes(image: QImage) -> bytes:
        img = image.convertToFormat(QImage.Format_RGBA8888)
        bits = img.constBits()
        bits.setsize(img.byteCount())
        return bytes(bits)

    # --- Упаковка TBP и сигнатуры сцены ---

    def upload_tbp_and_nodes(self, pctx: PipelineContext, operations: list, time_val: float) -> None:
        nodes = [op.to_node(t=time_val) for op in operations]

        max_tex_w = 1000
        tbp_flat_data: list[float] = []
        total_pixels = 0
        for node in nodes:
            for rgba in node.params:
                tbp_flat_data.extend([rgba.r, rgba.g, rgba.b, rgba.a])
            total_pixels += len(node.params)

        if total_pixels == 0:
            tex_w, tex_h = 1, 1
            tbp_flat_data = [0.0, 0.0, 0.0, 0.0]
            padding_pixels = 0
        else:
            tex_w = min(total_pixels, max_tex_w)
            tex_h = math.ceil(total_pixels / max_tex_w)
            padding_pixels = (tex_w * tex_h) - total_pixels

        tbp_flat_data.extend([0.0] * (padding_pixels * 4))
        blob = struct.pack(f"{len(tbp_flat_data)}f", *tbp_flat_data)

        _, created = pctx.pool.ensure_texture_2d(
            "tbp",
            size=(tex_w, tex_h),
            components=4,
            data=blob,
            dtype="f4",
            filter_mode=(moderngl.NEAREST, moderngl.NEAREST),
            repeat_x=False,
            repeat_y=False,
        )
        if not created:
            self._dbg("renderer_mgl.compress_nodes_params_to_texture.updated.log_debug")

        nodes_data = []
        offset = 0
        for node in nodes:
            nodes_data.append({
                "operationType": int(node.operation_type),
                "figureType": int(node.figure_type),
                "params": (float(offset), float(offset + len(node.params))),
            })
            offset += len(node.params)

        self.tbp_width = int(tex_w)
        self.nodes_dicts = nodes_data
        self.last_node_time = time_val
        self.nodes_rev += 1

    def sync_scene_and_prepare_uniforms(self, pctx: PipelineContext, params: dict[str, Any]) -> None:
        materials = params["materials"]
        operations = params["operations"]
        time_val = float(params["time_val"])

        lights = []
        glasses = []
        for i, op in enumerate(operations):
            if op.figure is not None and 0 <= op.figure.material_id < len(materials):
                mat = materials[op.figure.material_id]
                if mat.emission > 0.0:
                    lights.append(i)
                if mat.transmission > 0.0:
                    glasses.append(i)

        if len(lights) > self.LIGHT_MAX_COUNT:
            self._dbg(
                "renderer_mgl._pass_all_parameters.lights_limited.log_debug",
                limit=self.LIGHT_MAX_COUNT,
                count=len(lights),
            )
            lights = lights[: self.LIGHT_MAX_COUNT]

        if len(glasses) > self.GLASS_MAX_COUNT:
            glasses = glasses[: self.GLASS_MAX_COUNT]

        scene_sig = self._build_scene_signature(materials, operations)
        if scene_sig != self.scene_signature:
            self.scene_signature = scene_sig
            self.has_trajectories = any(
                op.figure is not None and op.figure.trajectory_fn is not None
                for op in operations
            )
            self.materials_dicts = [mat.to_dict() for mat in materials]
            self.materials_rev += 1
            self.upload_tbp_and_nodes(pctx, operations, time_val)
        elif self.has_trajectories and time_val != self.last_node_time:
            self.upload_tbp_and_nodes(pctx, operations, time_val)

        cam_pos = params["cam_pos"]
        cam_rot = params["cam_rot"]
        ambient_color = params["ambient_color"]

        pctx.blackboard.uniforms.update({
            "u_resolution": (float(pctx.render_w), float(pctx.render_h)),
            "u_fovYDeg": float(params["camera_fov_deg"]),
            "u_camPos": (float(cam_pos[0]), float(cam_pos[1]), float(cam_pos[2])),
            "u_camRot": (float(cam_rot[0]), float(cam_rot[1]), float(cam_rot[2])),
            "u_ambientLightColor": (float(ambient_color[0]), float(ambient_color[1]), float(ambient_color[2])),
            "u_ambientLightIntensity": float(params["ambient_intensity"]),
            "u_skySunAzimuthDeg": float(params["sky_sun_azimuth_deg"]),
            "u_skySunElevationDeg": float(params["sky_sun_elevation_deg"]),
            "u_skySunAngRadius": float(params["sky_sun_ang_radius"]),
            "u_skyAirDensity": float(params["sky_air_density"]),
            "u_showFloor": int(params["show_floor"]),
            "u_transparentBackground": int(params["transparent_bg"]),
            "u_maxReflectCount": int(params["max_reflect_count"]),
            "u_physGlass": bool(params["phys_glass"]),
            "u_areaRayCount": int(params["area_ray_count"]),
            "u_topLightCount": int(params["top_light_count"]),
            "u_ngonPhase": float(params["ngon_phase"]),
            "u_sampleSeed": int(params["sample_seed"]),
            "u_dispersion": float(params["dispersion"]),
            "u_sampleCount": int(params["sample_count"]),
            "u_sampleId": int(params["sample_id"]),
            "u_outputLinear": bool(params["output_linear"]),
            "u_fastMode": bool(params["fast_mode"]),
            "u_time": time_val,
            "u_LightPowerScale": float(params["light_power_scale"]),
            "u_lightCount": int(len(lights)),
            "u_glassCount": int(len(glasses)),
            "u_TBPWidth": int(self.tbp_width),
        })
        pctx.blackboard.data["lights"] = lights
        pctx.blackboard.data["glasses"] = glasses

    def bind_uniforms_to_program(self, pctx: PipelineContext, uacc: UniformAccessor) -> None:
        for k, v in pctx.blackboard.uniforms.items():
            uacc.set_value(k, v)

        uacc.set_int_array(
            "u_lights",
            pctx.blackboard.data.get("lights", ()),
            fixed_size=self.LIGHT_MAX_COUNT,
            fill_value=0,
        )
        uacc.set_int_array(
            "u_glasses",
            pctx.blackboard.data.get("glasses", ()),
            fixed_size=self.GLASS_MAX_COUNT,
            fill_value=0,
        )

        # Загружаем тяжёлые массивы структур только если ревизия данных изменилась для этой программы
        prog_id = uacc.program.glo
        synced_mat_rev, synced_node_rev = self.program_synced_revs.get(prog_id, (-1, -1))

        if synced_mat_rev != self.materials_rev:
            uacc.set_value("u_materialsCount", int(len(self.materials_dicts)))
            uacc.set_struct_array(
                "u_materialsList",
                self.materials_dicts,
                self.MATERIALS_LIST_MAX_LENGTH,
                _MATERIAL_FIELDS,
            )

        if synced_node_rev != self.nodes_rev:
            uacc.set_value("u_nodesCount", int(len(self.nodes_dicts)))
            uacc.set_struct_array(
                "u_nodes",
                self.nodes_dicts,
                self.NODES_MAX_LENGTH,
                _NODE_FIELDS,
            )

        self.program_synced_revs[prog_id] = (self.materials_rev, self.nodes_rev)

    @staticmethod
    def _material_signature(materials: list) -> tuple:
        return tuple(
            (
                float(mat.roughness),
                float(mat.metallic),
                float(mat.transmission),
                float(mat.ior),
                float(mat.emission),
                float(mat.subsurface),
                tuple(float(v) for v in mat.sss_color),
            )
            for mat in materials
        )

    @staticmethod
    def _figure_signature(figure) -> tuple | None:
        if figure is None:
            return None

        def tex_sig(tex) -> tuple:
            return (
                int(tex.id),
                tuple(float(v) for v in tex.offset),
                float(tex.rotation),
                tuple(float(v) for v in tex.original_size),
                float(tex.tiling_scale),
                float(getattr(tex, "depth_scale", 0.0)),
            )

        t_params = (
            figure.trajectory_params
            if isinstance(figure.trajectory_params, tuple)
            else tuple(sorted(figure.trajectory_params.items()))
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
            t_params,
            tuple(tex_sig(tex) for tex in figure.node_textures),
        )

    @classmethod
    def _build_scene_signature(cls, materials: list, operations: list) -> tuple:
        return (
            cls._material_signature(materials),
            tuple(
                (
                    int(op.op_type),
                    tuple(float(v) for v in op.operands),
                    cls._figure_signature(op.figure),
                )
                for op in operations
            ),
        )

#endregion


#region Шейдеры GPU-накопления и тональной компрессии ACES (для экспорта)

_EXPORT_SANITIZE_ACCUM_FRAG = """#version 330
precision highp float;
uniform sampler2D u_sampleTex;
out vec4 fragColor;

float cleanChannel(float v) {
    return (isnan(v) || isinf(v)) ? 0.0 : v;
}

void main() {
    vec4 c = texelFetch(u_sampleTex, ivec2(gl_FragCoord.xy), 0);
    fragColor = vec4(cleanChannel(c.r), cleanChannel(c.g), cleanChannel(c.b), cleanChannel(c.a));
}
"""

_EXPORT_ACES_TONEMAP_FRAG = """#version 330
precision highp float;
uniform sampler2D u_accumTex;
uniform float u_invSampleCount;
out vec4 fragColor;

float acesFilm(float x) {
    return clamp((x * (2.51 * x + 0.03)) / (x * (2.43 * x + 0.59) + 0.14), 0.0, 1.0);
}

vec3 acesTonemap(vec3 c) {
    float peak = max(c.r, max(c.g, c.b));
    if (peak <= 1e-5) {
        return vec3(acesFilm(c.r), acesFilm(c.g), acesFilm(c.b));
    }
    float mapped = acesFilm(peak);
    float t = clamp((peak - 1.0) / 7.0, 0.0, 1.0);
    float desat = t * t * (3.0 - 2.0 * t) * 0.85;
    vec3 scaled = (c / peak) * mapped;
    return clamp(mix(scaled, vec3(mapped), desat), 0.0, 1.0);
}

void main() {
    vec4 avgColor = texelFetch(u_accumTex, ivec2(gl_FragCoord.xy), 0) * u_invSampleCount;
    vec3 ldr = acesTonemap(avgColor.rgb);
    fragColor = vec4(ldr, clamp(avgColor.a, 0.0, 1.0));
}
"""

#endregion


#region Реализация SDFPipelineRenderer (совместимая с GPURenderer)

class SDFPipelineRenderer(BasePipelineRenderer):
    """
    Рендерер SDF-сцены на базе модульного пайплайна.
    По умолчанию работает в основном потоке (use_thread=False); для проверки
    выноса рендера в отдельный QThread достаточно передать use_thread=True.
    """

    def __init__(
        self,
        parent=None,
        *,
        use_thread: bool = False,
        debug_config: RenderDebugConfig | None = None,
    ):
        super().__init__(parent, use_thread=use_thread, debug_config=debug_config)
        self._dbg("renderer_mgl.init.complete.log_debug")

    def _dbg(self, localization_key: str, **values) -> None:
        if not (self.debug_config.enabled or DEBUG_LOG_ENABLED):
            return
        log_debug(localization.get_text(localization_key, **values))

    @staticmethod
    def _state(pctx: PipelineContext) -> _SDFPersistentState:
        return pctx.custom_state["sdf"]

    #region --- Изоляция входных данных для безопасного межпоточного обмена ---

    def prepare_frame_payload(self, payload: dict, is_export: bool = False) -> dict:
        out = dict(payload)
        raw_textures = payload.get("textures")
        if raw_textures is not None:
            out["textures"] = [
                _FrozenTextureInput(cache_key=int(img.cacheKey()), image=QImage(img))
                for img in raw_textures
            ]

        raw_params = payload.get("params")
        if raw_params is not None:
            p = dict(raw_params)
            p["cam_pos"] = tuple(float(v) for v in p["cam_pos"][:3])
            p["cam_rot"] = tuple(float(v) for v in p["cam_rot"][:3])
            p["ambient_color"] = tuple(float(v) for v in p["ambient_color"][:3])
            p["materials"] = [_FrozenMaterial.from_material(m) for m in p["materials"]]
            p["operations"] = [_FrozenOperation.from_operation(op) for op in p["operations"]]
            out["params"] = p
        return out

    #endregion

    #region --- Построение пайплайнов "preview" и "export" ---

    def setup_pipelines(self, pctx: PipelineContext) -> dict[str, RenderPipeline]:
        state = _SDFPersistentState(self._dbg)
        pctx.custom_state["sdf"] = state

        samplers_map = {
            "u_TBP": "tbp",
            "u_textures": "scene_textures",
        }

        # 1. Пайплайн интерактивного превью
        preview_pipe = RenderPipeline(
            "preview",
            stages=[
                CPUStage("sdf_preview_sync", self._stage_preview_sync),
                FragmentStage(
                    "sdf_preview_raymarch",
                    fragment_shader=lambda c: self._state(c).frag_code,
                    vertex_shader=lambda c: self._state(c).vert_code,
                    target="preview_rt",
                    target_dtype="f1",
                    samplers=samplers_map,
                    uniform_binder=lambda c, uacc: self._state(c).bind_uniforms_to_program(c, uacc),
                    chunked=True,
                ),
                CPUStage("sdf_preview_readback", self._stage_preview_readback),
            ],
        )

        # 2. Пайплайн экспорта (GPU-накопление HDR + GPU ACES без блокировки Python GIL и TDR)
        is_multi_sample = lambda c: max(1, int(c.blackboard.payload.get("accum_frames", 1))) > 1
        export_pipe = RenderPipeline(
            "export",
            stages=[
                CPUStage("sdf_export_init", self._stage_export_init),
                RepeatStage(
                    "sdf_export_samples",
                    iterations=lambda c: max(1, int(c.blackboard.payload.get("accum_frames", 1))),
                    stages=[
                        CPUStage("sdf_export_sample_setup", self._stage_export_sample_setup),
                        FragmentStage(
                            "sdf_export_raymarch",
                            fragment_shader=lambda c: self._state(c).frag_code,
                            vertex_shader=lambda c: self._state(c).vert_code,
                            target="export_rt",
                            target_dtype=lambda c: "f4" if is_multi_sample(c) else "f1",
                            target_filter=(moderngl.NEAREST, moderngl.NEAREST),
                            samplers=samplers_map,
                            uniform_binder=lambda c, uacc: self._state(c).bind_uniforms_to_program(c, uacc),
                            chunked=True,
                        ),
                        FragmentStage(
                            "sdf_export_sample_accum",
                            fragment_shader=_EXPORT_SANITIZE_ACCUM_FRAG,
                            target="export_accum_rt",
                            target_dtype="f4",
                            target_filter=(moderngl.NEAREST, moderngl.NEAREST),
                            samplers={"u_sampleTex": "export_rt"},
                            uniform_binder=lambda _c, _uacc: None,
                            clear_color=None,
                            enable_flags=moderngl.BLEND,
                            blend_func=(moderngl.ONE, moderngl.ONE),
                            condition=is_multi_sample,
                        ),
                    ],
                ),
                FragmentStage(
                    "sdf_export_tonemap",
                    fragment_shader=_EXPORT_ACES_TONEMAP_FRAG,
                    target="export_ldr_rt",
                    target_dtype="f1",
                    samplers={"u_accumTex": "export_accum_rt"},
                    uniform_binder=lambda c, uacc: uacc.set_value(
                        "u_invSampleCount",
                        1.0 / float(max(1, int(c.blackboard.data.get("accum_total", 1)))),
                    ),
                    condition=is_multi_sample,
                ),
                CPUStage("sdf_export_finalize", self._stage_export_finalize),
            ],
        )

        return {
            "preview": preview_pipe,
            "export": export_pipe,
        }

    #endregion

    #region --- Этапы пайплайна превью ---

    def _stage_preview_sync(self, pctx: PipelineContext) -> None:
        state = self._state(pctx)
        payload = pctx.blackboard.payload
        if payload.get("textures") is not None:
            state.update_texture_array(pctx, payload["textures"])
        else:
            state.ensure_default_texture_array(pctx)
        state.sync_scene_and_prepare_uniforms(pctx, payload["params"])

    def _stage_preview_readback(self, pctx: PipelineContext) -> None:
        pctx.blackboard.output_image = pctx.pool.read_target_to_qimage(
            "preview_rt",
            flush_gpu=pctx.in_worker_thread,
            highlight_nan_inf=pctx.debug.highlight_nan_inf,
        )

    #endregion

    #region --- Этапы пайплайна экспорта ---

    def _stage_export_init(self, pctx: PipelineContext) -> None:
        state = self._state(pctx)
        state.ensure_default_texture_array(pctx)

        payload = pctx.blackboard.payload
        accum_total = max(1, int(payload.get("accum_frames", 1)))
        base_params = dict(payload["params"])

        pctx.blackboard.data["accum_total"] = accum_total
        pctx.blackboard.data["export_base_params"] = base_params
        pctx.blackboard.data["accum_base_phase"] = float(base_params.get("ngon_phase", 0.0))

        if accum_total > 1:
            _, uacc, _ = pctx.pool.get_program(
                vert_src=state.vert_code,
                frag_src=state.frag_code,
                dump_on_error=pctx.debug.dump_failed_shaders,
            )
            if not uacc.has("u_outputLinear"):
                self._dbg("renderer_mgl.start_export.linear_uniform_missing.log_debug")
            accum_rt = pctx.pool.ensure_target(
                "export_accum_rt",
                size=(pctx.render_w, pctx.render_h),
                components=4,
                dtype="f4",
                filter_mode=(moderngl.NEAREST, moderngl.NEAREST),
            )
            accum_rt.fbo.use()
            pctx.ctx.scissor = None
            pctx.ctx.clear(0.0, 0.0, 0.0, 0.0)

    def _stage_export_sample_setup(self, pctx: PipelineContext) -> None:
        state = self._state(pctx)
        idx = int(pctx.blackboard.loop_indices.get("sdf_export_samples", 0))
        accum_total = int(pctx.blackboard.data["accum_total"])
        sample_params = dict(pctx.blackboard.data["export_base_params"])

        if accum_total > 1:
            base_phase = float(pctx.blackboard.data["accum_base_phase"])
            sample_params["output_linear"] = True
            sample_params["sample_count"] = accum_total
            sample_params["sample_seed"] = idx
            sample_params["sample_id"] = idx
            sample_params["ngon_phase"] = base_phase + GOLDEN_ANGLE * idx

        state.sync_scene_and_prepare_uniforms(pctx, sample_params)

    def _stage_export_finalize(self, pctx: PipelineContext) -> None:
        accum_total = int(pctx.blackboard.data["accum_total"])
        final_target = "export_ldr_rt" if accum_total > 1 else "export_rt"
        pctx.blackboard.output_image = pctx.pool.read_target_to_qimage(
            final_target,
            flush_gpu=True,
            highlight_nan_inf=pctx.debug.highlight_nan_inf,
        )

    #endregion

    #region --- Хуки жизненного цикла контекста, инициализации и экспорта ---

    def _on_context_created(self, pctx: PipelineContext) -> None:
        self._dbg("renderer_mgl._ensure_context.created.log_debug", version_code=pctx.ctx.version_code)

    def handle_initialize(self, pctx: PipelineContext, payload: dict) -> None:
        self._dbg("renderer_mgl.initialize_gl.begin.log_debug")
        self.set_test_shader_mode(False)

        state = self._state(pctx)
        state.frag_code = str(payload["frag_code"])
        state.vert_code = str(payload["vert_code"])
        state.reset_scene_tracking()
        self._store_last_image(None)

        # Компилируем шейдер сразу на этапе initialize, чтобы вернуть точный init_done(True/False)
        _, uacc, hit = pctx.pool.get_program(
            vert_src=state.vert_code,
            frag_src=state.frag_code,
            dump_on_error=pctx.debug.dump_failed_shaders,
        )
        if hit:
            self._dbg("renderer_mgl.initialize_gl.program_cache_hit.log_debug")
        else:
            self._dbg("renderer_mgl.initialize_gl.program_cache_miss.log_debug")
        self._dbg("renderer_mgl.initialize_gl.program_compiled.log_debug", members=len(uacc.names))

        if pctx.debug.enabled or DEBUG_LOG_ENABLED:
            presence = {name: uacc.has(name) for name in _EXPECTED_UNIFORMS}
            self._dbg(
                "renderer_mgl._dbg_uniform_presence.dump.log_debug",
                tag="initializeGL.uniform_presence",
                data=presence,
            )

        pctx.pool.ensure_quad_vbo()
        state.ensure_default_texture_array(pctx)
        self.handle_resize(pctx, payload)
        pctx.pool.ensure_target("preview_rt", size=(pctx.render_w, pctx.render_h), components=4, dtype="f1")
        self._dbg("renderer_mgl.initialize_gl.done.log_debug")

    def handle_resize(self, pctx: PipelineContext, payload: dict) -> None:
        super().handle_resize(pctx, payload)
        self._dbg(
            "renderer_mgl.resize_gl.requested.log_debug",
            src_w=pctx.widget_w,
            src_h=pctx.widget_h,
            render_w=pctx.render_w,
            render_h=pctx.render_h,
            preview_scale=pctx.preview_scale,
        )
        pctx.pool.ensure_target("preview_rt", size=(pctx.render_w, pctx.render_h), components=4, dtype="f1")
        self._store_last_image(None)
        gw = math.ceil(pctx.render_w / pctx.chunk_size)
        gh = math.ceil(pctx.render_h / pctx.chunk_size)
        self._dbg("renderer_mgl.resize_gl.done.log_debug", grid_w=gw, grid_h=gh)

    def before_export_start(self, pctx: PipelineContext, payload: dict) -> None:
        self.set_test_shader_mode(False)
        state = self._state(pctx)
        state.saved_preview_metrics = (
            pctx.widget_w,
            pctx.widget_h,
            pctx.preview_scale,
            pctx.chunk_size,
        )
        pctx.set_viewport_metrics(
            w=payload["w"],
            h=payload["h"],
            preview_scale=1.0,
            chunk_size=int(payload["chunk_size"]),
        )

    def after_export_finish(self, pctx: PipelineContext) -> None:
        # Освобождаем крупные экспортные буферы сразу после выгрузки кадра
        for rt_name in ("export_rt", "export_accum_rt", "export_ldr_rt"):
            pctx.pool.release_resource(rt_name)
        state = self._state(pctx)
        if state.saved_preview_metrics is not None:
            w, h, scale, cs = state.saved_preview_metrics
            pctx.set_viewport_metrics(w=w, h=h, preview_scale=scale, chunk_size=cs)
            state.saved_preview_metrics = None

    def on_release_custom_state(self, pctx: PipelineContext) -> None:
        state = pctx.custom_state.get("sdf")
        if state is not None:
            state.clear_all()
        super().on_release_custom_state(pctx)

    def _on_initialize_error(self, exc: Exception) -> None:
        super()._on_initialize_error(exc)
        self._dbg("renderer_mgl.initialize.failed.error.log_debug", error=exc)

    def _on_export_error(self, exc: Exception) -> None:
        super()._on_export_error(exc)
        self._dbg("renderer_mgl.start_export.failed.error.log_debug", error=exc)

    def _on_release_done(self) -> None:
        self._dbg("renderer_mgl.release_all.done.log_debug")

    #endregion

# Прямой алиас для прозрачной подмены renderer_mgl.GPURenderer
GPURenderer = SDFPipelineRenderer

#endregion
