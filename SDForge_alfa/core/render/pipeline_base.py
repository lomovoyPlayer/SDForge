"""
Базовое ядро модульного рендер-пайплайна SDForge (ModernGL + PyQt5).

Позволяет динамически собирать, менять, включать/отключать и чередовать
CPU-, Vertex- (Transform Feedback) и Fragment-этапы (полноэкранные и
геометрические, однопроходные и тайловые/чанковые), объединять их в циклы
(RepeatStage), профилировать и инспектировать в дебаг-режиме, а также
переключать исполнение между основным потоком и выделенным QThread.
"""

import ctypes
import datetime
import inspect
import json
import math
import os
import shutil
import struct
import sys
import threading
import time
import traceback
from collections import OrderedDict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import moderngl
import numpy as np
from PyQt5 import QtCore
from PyQt5.QtCore import QProcess, QProcessEnvironment, Qt, pyqtSignal, pyqtSlot
from PyQt5.QtGui import QImage

from ...config import DEBUG_LOG_ENABLED, DEBUG_LOG_PATH, log_debug


def _write_pipeline_log(message: str, force: bool = False) -> None:
    if not force and not DEBUG_LOG_ENABLED:
        return
    if DEBUG_LOG_ENABLED:
        log_debug(message)
        return
    try:
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as log_file:
            log_file.write(f"{datetime.datetime.now():%Y-%m-%d %H:%M:%S}: {message}\n")
    except Exception:
        pass


def _current_wgl_context_hex() -> str:
    try:
        ctx_ptr = ctypes.windll.opengl32.wglGetCurrentContext()
        return hex(ctx_ptr or 0)
    except Exception:
        return "n/a"

_SHADER_WARMUP_SCRIPT = r"""
import json
import sys
import moderngl

def main():
    raw = sys.stdin.read()
    specs = json.loads(raw)
    ctx = moderngl.create_context(standalone=True)
    try:
        for spec in specs:
            prog = ctx.program(**spec)
            prog.release()
    finally:
        ctx.release()

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        sys.stderr.write(f"{type(exc).__name__}: {exc}\n")
        sys.exit(1)
"""


def find_piplib_dir(start_path: str | Path | None = None) -> Path | None:
    """
    Ищет каталог piplib автоматически:
    1. Переменная окружения SDFORGE_PIPLIB_DIR (выставляется в init.py).
    2. Поиск вверх от указанного пути (по умолчанию от pipeline_base.py).
    """
    env_dir = os.environ.get("SDFORGE_PIPLIB_DIR")
    if env_dir:
        p = Path(env_dir)
        if p.is_dir():
            return p

    base = Path(start_path).resolve() if start_path is not None else Path(__file__).resolve()
    if base.is_file():
        base = base.parent
    for parent in (base, *base.parents[:6]):
        candidate = parent / "piplib"
        if candidate.is_dir():
            return candidate
    return None


def find_warmup_python(
    explicit_path: str | Path | None = None,
    piplib_dir: str | Path | None = None,
) -> str | None:
    """
    Ищет реальный интерпретатор Python для фонового QProcess-прогрева шейдеров,
    избегая запуска хост-приложения (например, krita.exe).

    Порядок поиска:
    1. Явно переданный explicit_path.
    2. Переменная окружения SDFORGE_WARMUP_PYTHON (выставляется в init.py).
    3. Встроенный Python внутри piplib (piplib/python/python.exe, piplib/python.exe и т.д.).
    4. sys.executable, если имя файла содержит 'python'.
    5. Системный PATH (python, python3, py).
    """
    if explicit_path is not None:
        p = Path(explicit_path)
        if p.is_file():
            return str(p)

    env_py = os.environ.get("SDFORGE_WARMUP_PYTHON")
    if env_py:
        p = Path(env_py)
        if p.is_file():
            return str(p)

    pip_dir = Path(piplib_dir) if piplib_dir is not None else find_piplib_dir()
    if pip_dir is not None and pip_dir.is_dir():
        rel_candidates = (
            Path("python") / "python.exe",
            Path("python.exe"),
            Path("python") / "bin" / "python3",
            Path("python") / "bin" / "python",
            Path("bin") / "python3",
            Path("bin") / "python",
        )
        for rel in rel_candidates:
            candidate = pip_dir / rel
            if candidate.is_file():
                return str(candidate)

    exe_name = os.path.basename(sys.executable).lower()
    if "python" in exe_name and os.path.isfile(sys.executable):
        return sys.executable

    for name in ("python", "python3", "py"):
        found = shutil.which(name)
        if found:
            return found

    return None



# Совпадает с конвенцией sdf_vert.glsl: v_uv в диапазоне [-1..1]
DEFAULT_QUAD_VERT = """#version 330
precision highp float;
in vec2 in_pos;
out vec2 v_uv;
void main() {
    v_uv = in_pos;
    gl_Position = vec4(in_pos, 0.0, 1.0);
}
"""

DEFAULT_TEST_FRAG = """#version 330
precision highp float;
out vec4 fragColor;
void main() {
    fragColor = vec4(0.2, 0.4, 0.8, 1.0);
}
"""


#region Конфигурация дебаг-режима и метрики

@dataclass
class RenderDebugConfig:
    """Настройки диагностики, профилирования и перехвата этапов пайплайна."""
    enabled: bool = False
    check_gl_errors: bool = True          # опрос ctx.error после GPU-шагов
    gpu_timing: bool = False              # замер GPU-времени через ctx.query(time=True) на 1-шаговых этапах
    dump_failed_shaders: bool = True      # вывод исходника шейдера с номерами строк при ошибке компиляции
    strict_isolation: bool = False        # принудительный freeze_payload даже при use_thread=False
    highlight_nan_inf: bool = False       # красить NaN/Inf пиксели в (255, 0, 255) при чтении float-таргета
    log_frame_summary: bool = False       # писать сводку таймингов каждого кадра в debug.log
    inspect_target: str | None = None     # имя промежуточного RenderTarget для вывода вместо финала
    global_test_shader: bool = False      # подменять все фрагментные шейдеры тестовой заливкой
    test_shader_stages: set[str] = field(default_factory=set)


@dataclass
class StageRuntimeStats:
    """Метрики выполнения одного этапа в рамках кадра."""
    name: str
    kind: str
    executions: int = 0
    steps: int = 0
    cpu_ms: float = 0.0
    gpu_ms: float | None = None
    gl_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "kind": self.kind,
            "executions": self.executions,
            "steps": self.steps,
            "cpu_ms": round(self.cpu_ms, 4),
            "gpu_ms": None if self.gpu_ms is None else round(self.gpu_ms, 4),
            "gl_error": self.gl_error,
        }

#endregion


#region Униформы и пул GPU-ресурсов

class UniformAccessor:
    """Кеш униформ одной программы (скаляры, массивы, массивы структур)."""

    def __init__(self, program: moderngl.Program, debug_cb: Callable[[str], None] | None = None):
        self.program = program
        self.names: set[str] = set(program)
        self._scalars: dict[str, Any] = {}
        self._arrays: dict[tuple[str, int], tuple[Any, list[Any]]] = {}
        self._structs: dict[tuple[str, int, tuple[str, ...]], list[dict[str, Any]]] = {}
        self._debug_cb = debug_cb

    def has(self, name: str) -> bool:
        return name in self.names

    def get_raw(self, name: str):
        if name not in self.names:
            return None
        if name not in self._scalars:
            try:
                self._scalars[name] = self.program[name]
            except Exception as exc:
                if self._debug_cb:
                    self._debug_cb(f"UniformAccessor: cache failed for {name}: {exc}")
                self._scalars[name] = None
        return self._scalars[name]

    def set_value(self, name: str, value: Any) -> None:
        u = self.get_raw(name)
        if u is None:
            return
        try:
            u.value = value
        except Exception as exc:
            if self._debug_cb:
                self._debug_cb(f"UniformAccessor: set failed {name}={value!r}: {exc}")

    def set_int_array(
        self,
        base_name: str,
        values: Iterable[int],
        fixed_size: int | None = None,
        fill_value: int = 0,
    ) -> None:
        vals = [int(v) for v in values]
        if fixed_size is not None:
            vals = vals[:fixed_size]
            if len(vals) < fixed_size:
                vals.extend([int(fill_value)] * (fixed_size - len(vals)))
        if not vals:
            vals = [int(fill_value)]

        count = fixed_size if fixed_size is not None else len(vals)
        key = (base_name, count)
        if key not in self._arrays:
            # GLSL-драйверы экспортируют массив либо под именем base_name, либо как base_name[0..N-1];
            # храним оба представления раздельно, чтобы не вызывать .value у списка
            whole = self.get_raw(base_name)
            indexed = [self.get_raw(f"{base_name}[{i}]") for i in range(count)]
            self._arrays[key] = (whole, indexed)

        whole, indexed = self._arrays[key]
        if whole is not None:
            try:
                whole.value = tuple(vals)
                return
            except Exception as exc:
                if self._debug_cb:
                    self._debug_cb(f"UniformAccessor: whole-array set fallback on {base_name}: {exc}")

        for i, v in enumerate(vals):
            if i >= len(indexed):
                break
            if indexed[i] is not None:
                try:
                    indexed[i].value = v
                except Exception as exc:
                    if self._debug_cb:
                        self._debug_cb(f"UniformAccessor: set failed {base_name}[{i}]={v}: {exc}")

    def set_struct_array(
        self,
        base_name: str,
        items: Sequence[dict[str, Any]],
        max_count: int,
        fields: Sequence[str],
    ) -> None:
        fields_tuple = tuple(fields)
        key = (base_name, int(max_count), fields_tuple)
        if key not in self._structs:
            self._structs[key] = [
                {f: self.get_raw(f"{base_name}[{i}].{f}") for f in fields_tuple}
                for i in range(max_count)
            ]
        cached = self._structs[key]
        limit = min(len(cached), len(items))
        for i in range(limit):
            row = items[i]
            u_row = cached[i]
            for f in fields_tuple:
                if f not in row:
                    continue
                u = u_row.get(f)
                if u is None:
                    continue
                val = row[f]
                if isinstance(val, list):
                    val = tuple(val)
                try:
                    u.value = val
                except Exception as exc:
                    if self._debug_cb:
                        self._debug_cb(f"UniformAccessor: set failed {base_name}[{i}].{f}={val!r}: {exc}")


@dataclass
class RenderTarget:
    """FBO поверх Texture: поддерживает и fbo.read() на CPU, и чтение как sampler2D в следующем этапе."""
    name: str
    fbo: moderngl.Framebuffer
    color_tex: moderngl.Texture
    depth_rb: moderngl.Renderbuffer | None
    size: tuple[int, int]
    components: int
    dtype: str

    def release(self) -> None:
        for obj in (self.fbo, self.color_tex, self.depth_rb):
            if obj is not None:
                try:
                    obj.release()
                except Exception:
                    pass


class ResourcePool:
    """Пул именованных GPU-объектов, принадлежащий строго одному контексту ModernGL."""

    def __init__(
        self,
        ctx: moderngl.Context,
        debug_cb: Callable[[str], None] | None = None,
        max_programs: int = 128,
    ):
        self.ctx = ctx
        self._debug_cb = debug_cb
        self._max_programs = max(1, int(max_programs))
        self.targets: dict[str, RenderTarget] = {}
        self.buffers: dict[str, moderngl.Buffer] = {}
        self.textures: dict[str, moderngl.Texture | moderngl.TextureArray] = {}
        self._texture_meta: dict[str, tuple] = {}
        self._programs: OrderedDict[tuple, tuple[moderngl.Program, UniformAccessor]] = OrderedDict()
        self._quad_vbo: moderngl.Buffer | None = None
        self.stats_program_hits: int = 0
        self.stats_program_misses: int = 0

    def ensure_quad_vbo(self) -> moderngl.Buffer:
        if self._quad_vbo is None:
            vertices = struct.pack("8f", -1.0, -1.0, 1.0, -1.0, -1.0, 1.0, 1.0, 1.0)
            self._quad_vbo = self.ctx.buffer(vertices)
            if self.ctx.supports_labels:
                self._quad_vbo.label = "buf:__fullscreen_quad"
        return self._quad_vbo

    def ensure_target(
        self,
        name: str,
        size: tuple[int, int],
        components: int = 4,
        dtype: str = "f1",
        depth: bool = False,
        filter_mode: tuple[int, int] = (moderngl.LINEAR, moderngl.LINEAR),
    ) -> RenderTarget:
        w, h = max(1, int(size[0])), max(1, int(size[1]))
        cur = self.targets.get(name)
        if (
            cur is not None
            and cur.size == (w, h)
            and cur.components == components
            and cur.dtype == dtype
            and (cur.depth_rb is not None) == bool(depth)
        ):
            cur.color_tex.filter = filter_mode
            return cur

        if cur is not None:
            self.targets.pop(name, None)
            cur.release()

        tex = self.ctx.texture((w, h), components=components, dtype=dtype)
        tex.filter = filter_mode
        tex.repeat_x = False
        tex.repeat_y = False
        depth_rb = self.ctx.depth_renderbuffer((w, h)) if depth else None
        fbo = self.ctx.framebuffer(color_attachments=[tex], depth_attachment=depth_rb)
        if self.ctx.supports_labels:
            tex.label = f"rt_tex:{name}"
            fbo.label = f"rt_fbo:{name}"
        rt = RenderTarget(name=name, fbo=fbo, color_tex=tex, depth_rb=depth_rb,
                          size=(w, h), components=components, dtype=dtype)
        self.targets[name] = rt
        return rt

    def ensure_buffer(
        self,
        name: str,
        reserve_bytes: int = 0,
        data: bytes | None = None,
        dynamic: bool = True,
    ) -> moderngl.Buffer:
        needed = len(data) if data is not None else max(4, int(reserve_bytes))
        buf = self.buffers.get(name)
        if buf is not None and buf.size != needed:
            self.buffers.pop(name, None)
            buf.release()
            buf = None
        if buf is None:
            if data is not None:
                buf = self.ctx.buffer(data=data, dynamic=dynamic)
            else:
                buf = self.ctx.buffer(reserve=needed, dynamic=dynamic)
            if self.ctx.supports_labels:
                buf.label = f"buf:{name}"
            self.buffers[name] = buf
        elif data is not None:
            buf.write(data)
        return buf

    def ensure_texture_2d(
        self,
        name: str,
        size: tuple[int, int],
        components: int = 4,
        data: bytes | None = None,
        dtype: str = "f1",
        filter_mode: tuple[int, int] = (moderngl.LINEAR, moderngl.LINEAR),
        repeat_x: bool = False,
        repeat_y: bool = False,
    ) -> tuple[moderngl.Texture, bool]:
        """Создать или обновить 2D-текстуру. Возвращает (texture, created_new)."""
        w, h = max(1, int(size[0])), max(1, int(size[1]))
        meta = ("2d", (w, h), components, dtype)
        cur = self.textures.get(name)
        created = False
        if cur is not None and self._texture_meta.get(name) != meta:
            self.textures.pop(name, None)
            self._texture_meta.pop(name, None)
            cur.release()
            cur = None

        if cur is None:
            cur = self.ctx.texture((w, h), components=components, data=data, dtype=dtype)
            if self.ctx.supports_labels:
                cur.label = f"tex2d:{name}"
            self.textures[name] = cur
            self._texture_meta[name] = meta
            created = True
        elif data is not None:
            cur.write(data)

        cur.filter = filter_mode
        cur.repeat_x = repeat_x
        cur.repeat_y = repeat_y
        return cur, created

    def ensure_texture_array(
        self,
        name: str,
        size: tuple[int, int, int],
        components: int = 4,
        data: bytes | None = None,
        dtype: str = "f1",
        filter_mode: tuple[int, int] = (moderngl.LINEAR, moderngl.LINEAR),
        repeat_x: bool = True,
        repeat_y: bool = True,
    ) -> tuple[moderngl.TextureArray, bool]:
        """Создать или обновить TextureArray. Возвращает (texture_array, created_new)."""
        w, h, d = max(1, int(size[0])), max(1, int(size[1])), max(1, int(size[2]))
        meta = ("array", (w, h, d), components, dtype)
        cur = self.textures.get(name)
        created = False
        if cur is not None and self._texture_meta.get(name) != meta:
            self.textures.pop(name, None)
            self._texture_meta.pop(name, None)
            cur.release()
            cur = None

        if cur is None:
            cur = self.ctx.texture_array((w, h, d), components, data, dtype=dtype)
            if self.ctx.supports_labels:
                cur.label = f"texarr:{name}"
            self.textures[name] = cur
            self._texture_meta[name] = meta
            created = True
        elif data is not None:
            cur.write(data)

        cur.filter = filter_mode
        cur.repeat_x = repeat_x
        cur.repeat_y = repeat_y
        return cur, created

    def copy_buffer_to_texture_2d(
        self,
        buffer_name: str,
        texture_name: str,
        size: tuple[int, int],
        components: int = 4,
        dtype: str = "f4",
        filter_mode: tuple[int, int] = (moderngl.NEAREST, moderngl.NEAREST),
    ) -> moderngl.Texture:
        """Прямое GPU->GPU копирование буфера (например, после VertexStage) в 2D-текстуру через PBO."""
        buf = self.buffers[buffer_name]
        tex, _ = self.ensure_texture_2d(
            texture_name, size=size, components=components, data=None, dtype=dtype, filter_mode=filter_mode
        )
        tex.write(buf)
        return tex

    def copy_texture_to_buffer(self, source_name: str, buffer_name: str) -> moderngl.Buffer:
        """Прямое GPU->GPU копирование текстуры или RenderTarget в Buffer через PBO."""
        tex = self.resolve_texture(source_name)
        if tex is None:
            raise KeyError(f"ResourcePool: texture/target '{source_name}' not found")
        elem_bytes = {"f1": 1, "f2": 2, "f4": 4, "i1": 1, "i2": 2, "i4": 4, "u1": 1, "u2": 2, "u4": 4}.get(tex.dtype, 4)
        w, h = tex.size[0], tex.size[1]
        layers = tex.size[2] if len(tex.size) > 2 else 1
        total_bytes = w * h * layers * tex.components * elem_bytes
        buf = self.ensure_buffer(buffer_name, reserve_bytes=total_bytes, dynamic=True)
        tex.read_into(buf, alignment=1)
        return buf

    def resolve_texture(self, name: str) -> moderngl.Texture | moderngl.TextureArray | None:
        if name in self.textures:
            return self.textures[name]
        if name in self.targets:
            return self.targets[name].color_tex
        return None

    def get_program(
        self,
        vert_src: str,
        frag_src: str | None = None,
        geom_src: str | None = None,
        varyings: Sequence[str] = (),
        varyings_capture_mode: str = "interleaved",
        dump_on_error: bool = True,
    ) -> tuple[moderngl.Program, UniformAccessor, bool]:
        """Скомпилировать программу или вернуть из кеша. Возвращает (prog, accessor, cache_hit)."""
        key = (vert_src, frag_src, geom_src, tuple(varyings), varyings_capture_mode)
        cached = self._programs.get(key)
        if cached is not None:
            self._programs.move_to_end(key)
            self.stats_program_hits += 1
            return cached[0], cached[1], True

        self.stats_program_misses += 1
        try:
            prog = self.ctx.program(
                vertex_shader=vert_src,
                fragment_shader=frag_src,
                geometry_shader=geom_src,
                varyings=tuple(varyings),
                varyings_capture_mode=varyings_capture_mode,
            )
        except Exception as exc:
            if dump_on_error and self._debug_cb:
                src_to_dump = frag_src if frag_src is not None else vert_src
                numbered = "\n".join(
                    f"{idx + 1:04d}: {line}" for idx, line in enumerate(src_to_dump.splitlines())
                )
                self._debug_cb(f"Shader compile failed: {exc}\n--- SHADER SOURCE ---\n{numbered}")
            raise

        accessor = UniformAccessor(prog, debug_cb=self._debug_cb)
        while len(self._programs) >= self._max_programs:
            _, (old_prog, _) = self._programs.popitem(last=False)
            try:
                old_prog.release()
            except Exception:
                pass
        self._programs[key] = (prog, accessor)
        return prog, accessor, False

    def read_target_to_qimage(
        self,
        target_name: str,
        flush_gpu: bool = False,
        highlight_nan_inf: bool = False,
    ) -> QImage | None:
        """Вычитать RenderTarget (f1 или f4) в QImage с вертикальным разворотом OpenGL->Qt."""
        rt = self.targets.get(target_name)
        if rt is None:
            return None
        if flush_gpu:
            self.ctx.finish()

        w, h = rt.size
        if rt.dtype == "f1" and rt.components == 4:
            raw = rt.fbo.read(components=4, alignment=1, dtype="f1")
            return QImage(raw, w, h, QImage.Format_RGBA8888).mirrored().copy()

        # Векторизованный путь для float-таргетов (например, при инспекции HDR-этапа в дебаге)
        raw = rt.fbo.read(components=4, alignment=1, dtype="f4")
        arr = np.frombuffer(raw, dtype=np.float32).reshape((h, w, 4))[::-1]
        finite_mask = np.isfinite(arr).all(axis=2)
        clamped = np.clip(np.nan_to_num(arr, nan=0.0, posinf=0.0, neginf=0.0), 0.0, 1.0)
        out = (clamped * 255.0 + 0.5).astype(np.uint8)
        if not finite_mask.all():
            out[~finite_mask] = (255, 0, 255, 255) if highlight_nan_inf else (0, 0, 0, 255)
        return QImage(out.tobytes(), w, h, QImage.Format_RGBA8888).copy()

    def inspect_buffer_struct(self, buffer_name: str, fmt: str, count: int = 16) -> list[tuple]:
        """Прочитать первые count структур fmt из GPU-буфера для отладки VertexStage."""
        buf = self.buffers.get(buffer_name)
        if buf is None:
            return []
        stride = struct.calcsize(fmt)
        if stride <= 0:
            return []
        max_bytes = min(buf.size, stride * max(1, int(count)))
        raw = buf.read(size=max_bytes)
        items = max_bytes // stride
        return [struct.unpack_from(fmt, raw, i * stride) for i in range(items)]

    def release_resource(self, name: str) -> None:
        rt = self.targets.pop(name, None)
        if rt is not None:
            rt.release()
        buf = self.buffers.pop(name, None)
        if buf is not None:
            try:
                buf.release()
            except Exception:
                pass
        tex = self.textures.pop(name, None)
        self._texture_meta.pop(name, None)
        if tex is not None:
            try:
                tex.release()
            except Exception:
                pass

    def release_all(self) -> None:
        for rt in self.targets.values():
            rt.release()
        self.targets.clear()

        for buf in self.buffers.values():
            try:
                buf.release()
            except Exception:
                pass
        self.buffers.clear()

        for tex in self.textures.values():
            try:
                tex.release()
            except Exception:
                pass
        self.textures.clear()
        self._texture_meta.clear()

        if self._quad_vbo is not None:
            try:
                self._quad_vbo.release()
            except Exception:
                pass
            self._quad_vbo = None

        for prog, _ in self._programs.values():
            try:
                prog.release()
            except Exception:
                pass
        self._programs.clear()

#endregion


#region Контекст кадра и строительные этапы пайплайна

@dataclass
class FrameBlackboard:
    """Оперативные данные одного кадра, общие для всех этапов активного пайплайна."""
    payload: dict[str, Any] = field(default_factory=dict)
    uniforms: dict[str, Any] = field(default_factory=dict)
    data: dict[str, Any] = field(default_factory=dict)
    loop_indices: dict[str, int] = field(default_factory=dict)
    output_image: QImage | None = None


class PipelineContext:
    """Контекст исполнения, передаваемый во все этапы пайплайна."""

    def __init__(
        self,
        ctx: moderngl.Context,
        pool: ResourcePool,
        debug: RenderDebugConfig,
        in_worker_thread: bool = False,
    ):
        self.ctx = ctx
        self.pool = pool
        self.debug = debug
        self.in_worker_thread = in_worker_thread

        self.widget_w: float = 1.0
        self.widget_h: float = 1.0
        self.preview_scale: float = 1.0
        self.render_w: int = 1
        self.render_h: int = 1
        self.chunk_size: int = 64

        self.blackboard = FrameBlackboard()
        self.custom_state: dict[str, Any] = {}  # меж-кадровое состояние конкретной реализации рендерера

    def set_viewport_metrics(self, w: float, h: float, preview_scale: float, chunk_size: int | None = None) -> None:
        self.preview_scale = float(preview_scale) if preview_scale else 1.0
        self.widget_w = float(w)
        self.widget_h = float(h)
        self.render_w = max(1, int(self.widget_w / self.preview_scale))
        self.render_h = max(1, int(self.widget_h / self.preview_scale))
        if chunk_size is not None:
            self.chunk_size = max(1, int(chunk_size))

    def log_debug(self, msg: str) -> None:
        _write_pipeline_log(msg, force=bool(self.debug.enabled))


class PipelineStage:
    """Базовый протокол этапа пайплайна."""
    kind: str = "base"

    def __init__(self, name: str, enabled: bool = True, yield_after_step: bool = False):
        if not name or not isinstance(name, str):
            raise ValueError("PipelineStage name must be a non-empty string")
        self.name = name
        self.enabled = bool(enabled)
        self.yield_after_step = bool(yield_after_step)

    def on_attach(self, pctx: PipelineContext) -> None:
        pass

    def on_detach(self, pctx: PipelineContext) -> None:
        pass

    def on_resize(self, pctx: PipelineContext, render_w: int, render_h: int) -> None:
        pass

    def begin(self, pctx: PipelineContext) -> int:
        """Подготовить этап и вернуть расчётное количество шагов (0 = пропустить)."""
        return 1

    def step(self, pctx: PipelineContext, step_index: int) -> bool:
        """Выполнить шаг step_index. Возвращает True, если этап полностью завершён."""
        return True

    def end(self, pctx: PipelineContext) -> None:
        pass


class CPUStage(PipelineStage):
    """
    Этап вычислений на CPU.
    Поддерживает как обычную функцию fn(pctx), так и генераторную функцию (yield),
    позволяющую разбивать длинные CPU-проходы на неблокирующие кванты по таймеру.
    """
    kind = "cpu"

    def __init__(
        self,
        name: str,
        fn: Callable[[PipelineContext], Any],
        estimated_steps_fn: Callable[[PipelineContext], int] | None = None,
        yield_after_step: bool | None = None,
        enabled: bool = True,
    ):
        is_gen = inspect.isgeneratorfunction(fn)
        super().__init__(
            name=name,
            enabled=enabled,
            yield_after_step=is_gen if yield_after_step is None else bool(yield_after_step),
        )
        self._fn = fn
        self._estimated_steps_fn = estimated_steps_fn
        self._is_gen = is_gen
        self._gen = None

    def begin(self, pctx: PipelineContext) -> int:
        self._close_gen()
        if self._is_gen:
            self._gen = self._fn(pctx)
            if self._estimated_steps_fn is not None:
                return max(0, int(self._estimated_steps_fn(pctx)))
            return 1
        return 1

    def step(self, pctx: PipelineContext, step_index: int) -> bool:
        if self._gen is not None:
            try:
                next(self._gen)
                return False
            except StopIteration:
                self._gen = None
                return True
        self._fn(pctx)
        return True

    def end(self, pctx: PipelineContext) -> None:
        self._close_gen()

    def _close_gen(self) -> None:
        if self._gen is not None:
            try:
                self._gen.close()
            except Exception:
                pass
            self._gen = None


@dataclass(init=False)
class BufferInputSpec:
    """Описание привязки вершинного буфера из ResourcePool к атрибутам шейдера."""
    buffer_name: str
    fmt: str
    attributes: tuple[str, ...]

    def __init__(self, buffer_name: str, fmt: str, *attributes: str):
        self.buffer_name = buffer_name
        self.fmt = fmt
        self.attributes = tuple(attributes)


class VertexStage(PipelineStage):
    """
    Вершинный (и опционально геометрический) GPU-этап на базе Transform Feedback.
    Читает вершинные буферы и/или текстуры из ResourcePool и записывает выходные
    varyings напрямую в один или несколько GPU-буферов без растеризации.
    """
    kind = "vertex"

    def __init__(
        self,
        name: str,
        vertex_shader: str | Callable[[PipelineContext], str],
        varyings: Sequence[str],
        output_buffers: str | Sequence[str],
        output_size_bytes: int | Sequence[int] | Callable[[PipelineContext], int | Sequence[int]],
        inputs: Sequence[BufferInputSpec] | None = None,
        geometry_shader: str | Callable[[PipelineContext], str | None] | None = None,
        varyings_capture_mode: str = "interleaved",
        samplers: dict[str, str] | None = None,
        ubo_bindings: dict[int, str] | None = None,
        uniform_binder: Callable[[PipelineContext, UniformAccessor], None] | None = None,
        mode: int = moderngl.POINTS,
        vertices: int | Callable[[PipelineContext], int] = -1,
        instances: int | Callable[[PipelineContext], int] = 1,
        yield_after_step: bool = False,
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled, yield_after_step=yield_after_step)
        self.vertex_shader = vertex_shader
        self.geometry_shader = geometry_shader
        self.varyings = tuple(varyings)
        self.output_buffers = (output_buffers,) if isinstance(output_buffers, str) else tuple(output_buffers)
        self.output_size_bytes = output_size_bytes
        self.inputs = list(inputs or [])
        self.varyings_capture_mode = varyings_capture_mode
        self.samplers = dict(samplers or {})
        self.ubo_bindings = dict(ubo_bindings or {})
        self.uniform_binder = uniform_binder
        self.mode = mode
        self.vertices = vertices
        self.instances = instances

    def step(self, pctx: PipelineContext, step_index: int) -> bool:
        vert_src = self.vertex_shader(pctx) if callable(self.vertex_shader) else self.vertex_shader
        geom_src = self.geometry_shader(pctx) if callable(self.geometry_shader) else self.geometry_shader
        prog, uacc, _ = pctx.pool.get_program(
            vert_src=vert_src,
            frag_src=None,
            geom_src=geom_src,
            varyings=self.varyings,
            varyings_capture_mode=self.varyings_capture_mode,
            dump_on_error=pctx.debug.dump_failed_shaders,
        )

        sizes = self.output_size_bytes(pctx) if callable(self.output_size_bytes) else self.output_size_bytes
        if isinstance(sizes, int):
            sizes_list = [sizes] * len(self.output_buffers)
        else:
            sizes_list = list(sizes)

        out_bufs = [
            pctx.pool.ensure_buffer(bname, reserve_bytes=int(sz), dynamic=True)
            for bname, sz in zip(self.output_buffers, sizes_list)
        ]

        content = [
            (pctx.pool.buffers[inp.buffer_name], inp.fmt, *inp.attributes)
            for inp in self.inputs
        ]
        vao = pctx.ctx.vertex_array(prog, content)
        try:
            for slot, (u_name, res_name) in enumerate(self.samplers.items()):
                tex = pctx.pool.resolve_texture(res_name)
                if tex is not None:
                    tex.use(location=slot)
                    uacc.set_value(u_name, slot)

            for binding_idx, ubo_name in self.ubo_bindings.items():
                pctx.pool.buffers[ubo_name].bind_to_uniform_block(binding=binding_idx)

            if self.uniform_binder is not None:
                self.uniform_binder(pctx, uacc)
            else:
                for k, v in pctx.blackboard.uniforms.items():
                    uacc.set_value(k, v)

            v_count = self.vertices(pctx) if callable(self.vertices) else int(self.vertices)
            inst_count = self.instances(pctx) if callable(self.instances) else int(self.instances)
            target_out = out_bufs[0] if len(out_bufs) == 1 else out_bufs
            vao.transform(target_out, mode=self.mode, vertices=v_count, instances=inst_count)
        finally:
            vao.release()
        return True


@dataclass
class TargetSpec:
    """Спецификация целевого RenderTarget для FragmentStage."""
    name: str = "main"
    dtype: str | Callable[[PipelineContext], str] = "f1"
    size: Callable[[PipelineContext], tuple[int, int]] | None = None
    filter_mode: tuple[int, int] = (moderngl.LINEAR, moderngl.LINEAR)


@dataclass
class GeometrySpec:
    """Спецификация геометрических буферов для FragmentStage."""
    inputs: Sequence[BufferInputSpec] | None = None
    index_buffer: str | None = None
    primitive_mode: int = moderngl.TRIANGLE_STRIP
    vertices: int | Callable[[PipelineContext], int] = -1
    instances: int | Callable[[PipelineContext], int] = 1


@dataclass
class RenderStateSpec:
    """Спецификация состояния OpenGL для FragmentStage."""
    clear_color: tuple[float, float, float, float] | None = (0.0, 0.0, 0.0, 0.0)
    depth_test: bool = False
    enable_flags: int = moderngl.NOTHING
    blend_func: tuple[int, int] | None = None
    wireframe: bool = False
    finish_each_step: bool = False


class FragmentStage(PipelineStage):
    """
    Этап растеризации в RenderTarget (FBO).
    - Без geometry_inputs рисует полноэкранный квад (для SDF реймарчинга и пост-эффектов).
    - С geometry_inputs рисует произвольные вершинные/индексные буферы (например, после VertexStage/CPUStage).
    - При chunked=True разбивает кадр на scissor-тайлы chunk_size x chunk_size.
    """
    kind = "fragment"

    def __init__(
        self,
        name: str,
        fragment_shader: str | Callable[[PipelineContext], str],
        target: str = "main",
        target_dtype: str | Callable[[PipelineContext], str] = "f1",
        target_size: Callable[[PipelineContext], tuple[int, int]] | None = None,
        target_filter: tuple[int, int] = (moderngl.LINEAR, moderngl.LINEAR),
        vertex_shader: str | Callable[[PipelineContext], str] = DEFAULT_QUAD_VERT,
        geometry_shader: str | Callable[[PipelineContext], str | None] | None = None,
        geometry_inputs: Sequence[BufferInputSpec] | None = None,
        index_buffer: str | None = None,
        primitive_mode: int = moderngl.TRIANGLE_STRIP,
        vertices: int | Callable[[PipelineContext], int] = -1,
        instances: int | Callable[[PipelineContext], int] = 1,
        samplers: dict[str, str] | None = None,
        ubo_bindings: dict[int, str] | None = None,
        uniform_binder: Callable[[PipelineContext, UniformAccessor], None] | None = None,
        chunked: bool = False,
        clear_color: tuple[float, float, float, float] | None = (0.0, 0.0, 0.0, 0.0),
        depth_test: bool = False,
        enable_flags: int = moderngl.NOTHING,
        blend_func: tuple[int, int] | None = None,
        wireframe: bool = False,
        finish_each_step: bool = False,
        condition: Callable[[PipelineContext], bool] | None = None,
        yield_after_step: bool | None = None,
        enabled: bool = True,
        target_spec: TargetSpec | None = None,
        geometry_spec: GeometrySpec | None = None,
        render_state: RenderStateSpec | None = None,
    ):
        super().__init__(
            name=name,
            enabled=enabled,
            yield_after_step=bool(chunked) if yield_after_step is None else bool(yield_after_step),
        )
        self.fragment_shader = fragment_shader
        self.vertex_shader = vertex_shader
        self.geometry_shader = geometry_shader
        if target_spec is not None:
            self.target = target_spec.name
            self.target_dtype = target_spec.dtype
            self.target_size = target_spec.size
            self.target_filter = target_spec.filter_mode
        else:
            self.target = target
            self.target_dtype = target_dtype
            self.target_size = target_size
            self.target_filter = target_filter

        if geometry_spec is not None:
            self.geometry_inputs = list(geometry_spec.inputs) if geometry_spec.inputs is not None else None
            self.index_buffer = geometry_spec.index_buffer
            self.primitive_mode = geometry_spec.primitive_mode
            self.vertices = geometry_spec.vertices
            self.instances = geometry_spec.instances
        else:
            self.geometry_inputs = list(geometry_inputs) if geometry_inputs is not None else None
            self.index_buffer = index_buffer
            self.primitive_mode = primitive_mode
            self.vertices = vertices
            self.instances = instances

        if render_state is not None:
            self.clear_color = render_state.clear_color
            self.depth_test = bool(render_state.depth_test)
            self.enable_flags = render_state.enable_flags
            self.blend_func = render_state.blend_func
            self.wireframe = bool(render_state.wireframe)
            self.finish_each_step = bool(render_state.finish_each_step)
        else:
            self.clear_color = clear_color
            self.depth_test = bool(depth_test)
            self.enable_flags = enable_flags
            self.blend_func = blend_func
            self.wireframe = bool(wireframe)
            self.finish_each_step = bool(finish_each_step)

        self.samplers = dict(samplers or {})
        self.ubo_bindings = dict(ubo_bindings or {})
        self.uniform_binder = uniform_binder
        self.chunked = bool(chunked)
        self.condition = condition

        self._rt: RenderTarget | None = None
        self._uacc: UniformAccessor | None = None
        self._vao: moderngl.VertexArray | None = None
        self._vao_key: tuple | None = None
        self._grid_w: int = 1
        self._grid_h: int = 1
        self._active_chunk_size: int = 64

    def on_detach(self, pctx: PipelineContext) -> None:
        self._release_vao()

    def _release_vao(self) -> None:
        if self._vao is not None:
            try:
                self._vao.release()
            except Exception:
                pass
            self._vao = None
            self._vao_key = None

    def _resolve_fragment_shader(self, pctx: PipelineContext) -> str:
        if pctx.debug.global_test_shader or self.name in pctx.debug.test_shader_stages:
            return DEFAULT_TEST_FRAG
        return self.fragment_shader(pctx) if callable(self.fragment_shader) else self.fragment_shader

    def begin(self, pctx: PipelineContext) -> int:
        if self.condition is not None and not self.condition(pctx):
            return 0
        size = self.target_size(pctx) if self.target_size is not None else (pctx.render_w, pctx.render_h)
        dtype = self.target_dtype(pctx) if callable(self.target_dtype) else self.target_dtype
        self._rt = pctx.pool.ensure_target(
            self.target,
            size=size,
            components=4,
            dtype=dtype,
            depth=self.depth_test,
            filter_mode=self.target_filter,
        )

        vert_src = self.vertex_shader(pctx) if callable(self.vertex_shader) else self.vertex_shader
        geom_src = self.geometry_shader(pctx) if callable(self.geometry_shader) else self.geometry_shader
        frag_src = self._resolve_fragment_shader(pctx)

        prog, self._uacc, _ = pctx.pool.get_program(
            vert_src=vert_src,
            frag_src=frag_src,
            geom_src=geom_src,
            dump_on_error=pctx.debug.dump_failed_shaders,
        )

        if self.geometry_inputs is None:
            q_vbo = pctx.pool.ensure_quad_vbo()
            vao_key = (prog.glo, q_vbo.glo, None)
            if self._vao is None or self._vao_key != vao_key:
                self._release_vao()
                self._vao = pctx.ctx.vertex_array(prog, [(q_vbo, "2f", "in_pos")])
                self._vao_key = vao_key
        else:
            content = [
                (pctx.pool.buffers[inp.buffer_name], inp.fmt, *inp.attributes)
                for inp in self.geometry_inputs
            ]
            ibo = pctx.pool.buffers[self.index_buffer] if self.index_buffer else None
            vao_key = (prog.glo, tuple((c[0].glo, c[1], c[2:]) for c in content), ibo.glo if ibo else None)
            if self._vao is None or self._vao_key != vao_key:
                self._release_vao()
                self._vao = pctx.ctx.vertex_array(prog, content, index_buffer=ibo)
                self._vao_key = vao_key

        for slot, (u_name, res_name) in enumerate(self.samplers.items()):
            tex = pctx.pool.resolve_texture(res_name)
            if tex is not None:
                tex.use(location=slot)
                self._uacc.set_value(u_name, slot)

        for binding_idx, ubo_name in self.ubo_bindings.items():
            pctx.pool.buffers[ubo_name].bind_to_uniform_block(binding=binding_idx)

        if self.uniform_binder is not None:
            self.uniform_binder(pctx, self._uacc)
        else:
            for k, v in pctx.blackboard.uniforms.items():
                self._uacc.set_value(k, v)

        if not self.chunked:
            self._grid_w, self._grid_h = 1, 1
            return 1

        cs = max(1, int(pctx.chunk_size))
        self._active_chunk_size = cs
        self._grid_w = math.ceil(self._rt.size[0] / cs)
        self._grid_h = math.ceil(self._rt.size[1] / cs)
        return max(1, self._grid_w * self._grid_h)

    def step(self, pctx: PipelineContext, step_index: int) -> bool:
        rt = self._rt
        if rt is None or self._vao is None:
            return True

        w_tot, h_tot = rt.size
        rt.fbo.use()
        pctx.ctx.viewport = (0, 0, w_tot, h_tot)

        flags = self.enable_flags | (moderngl.DEPTH_TEST if self.depth_test else 0)
        pctx.ctx.enable_only(flags)
        if self.blend_func is not None:
            pctx.ctx.blend_func = self.blend_func
        pctx.ctx.wireframe = self.wireframe

        if step_index == 0 and self.clear_color is not None:
            pctx.ctx.scissor = None
            pctx.ctx.clear(*self.clear_color)

        for slot, (_, res_name) in enumerate(self.samplers.items()):
            tex = pctx.pool.resolve_texture(res_name)
            if tex is not None:
                tex.use(location=slot)

        for binding_idx, ubo_name in self.ubo_bindings.items():
            pctx.pool.buffers[ubo_name].bind_to_uniform_block(binding=binding_idx)

        v_count = self.vertices(pctx) if callable(self.vertices) else int(self.vertices)
        inst_count = self.instances(pctx) if callable(self.instances) else int(self.instances)

        try:
            if not self.chunked:
                pctx.ctx.scissor = None
                self._vao.render(mode=self.primitive_mode, vertices=v_count, instances=inst_count)
                if self.finish_each_step:
                    pctx.ctx.finish()
                return True

            cx = step_index % self._grid_w
            cy = step_index // self._grid_w
            cs = self._active_chunk_size
            x = cx * cs
            y = cy * cs
            w = min(cs, w_tot - x)
            h = min(cs, h_tot - y)

            pctx.ctx.scissor = (x, y, w, h)
            self._vao.render(mode=self.primitive_mode, vertices=v_count, instances=inst_count)
            pctx.ctx.scissor = None
            if self.finish_each_step:
                pctx.ctx.finish()

            return (step_index + 1) >= (self._grid_w * self._grid_h)
        finally:
            if self.blend_func is not None:
                pctx.ctx.blend_func = moderngl.DEFAULT_BLENDING
            if self.wireframe:
                pctx.ctx.wireframe = False


class RepeatStage(PipelineStage):
    """Композитный этап-цикл: повторяет вложенный список этапов заданное количество раз."""
    kind = "repeat"

    def __init__(
        self,
        name: str,
        stages: Sequence[PipelineStage],
        iterations: int | Callable[[PipelineContext], int],
        enabled: bool = True,
    ):
        super().__init__(name=name, enabled=enabled, yield_after_step=False)
        self.stages: list[PipelineStage] = list(stages)
        self.iterations = iterations

    def on_attach(self, pctx: PipelineContext) -> None:
        for st in self.stages:
            st.on_attach(pctx)

    def on_detach(self, pctx: PipelineContext) -> None:
        for st in self.stages:
            st.on_detach(pctx)

    def on_resize(self, pctx: PipelineContext, render_w: int, render_h: int) -> None:
        for st in self.stages:
            st.on_resize(pctx, render_w, render_h)

#endregion


#region Мутируемый граф/список пайплайна и пошаговый планировщик

class RenderPipeline:
    """Упорядоченный список этапов с поддержкой горячей вставки, удаления и замены."""

    def __init__(self, name: str, stages: Sequence[PipelineStage] | None = None):
        self.name = name
        self._stages: list[PipelineStage] = []
        self._pctx: PipelineContext | None = None
        for st in (stages or ()):
            self.add(st)

    @property
    def stages(self) -> list[PipelineStage]:
        return list(self._stages)

    def names(self) -> list[str]:
        return [s.name for s in self._stages]

    def bind_context(self, pctx: PipelineContext | None) -> None:
        if self._pctx is pctx:
            return
        if self._pctx is not None:
            for st in self._stages:
                st.on_detach(self._pctx)
        self._pctx = pctx
        if pctx is not None:
            for st in self._stages:
                st.on_attach(pctx)

    def notify_resize(self, pctx: PipelineContext, render_w: int, render_h: int) -> None:
        for st in self._stages:
            st.on_resize(pctx, render_w, render_h)

    def get(self, name: str) -> PipelineStage | None:
        for st in self._stages:
            if st.name == name:
                return st
        return None

    def add(self, stage: PipelineStage) -> "RenderPipeline":
        return self.insert(len(self._stages), stage)

    def insert(self, index: int, stage: PipelineStage) -> "RenderPipeline":
        if self.get(stage.name) is not None:
            raise ValueError(f"RenderPipeline '{self.name}': stage '{stage.name}' already exists")
        self._stages.insert(index, stage)
        if self._pctx is not None:
            stage.on_attach(self._pctx)
        return self

    def insert_before(self, target_name: str, stage: PipelineStage) -> "RenderPipeline":
        idx = self._index_of(target_name)
        return self.insert(idx, stage)

    def insert_after(self, target_name: str, stage: PipelineStage) -> "RenderPipeline":
        idx = self._index_of(target_name)
        return self.insert(idx + 1, stage)

    def remove(self, name: str) -> PipelineStage | None:
        for i, st in enumerate(self._stages):
            if st.name == name:
                removed = self._stages.pop(i)
                if self._pctx is not None:
                    removed.on_detach(self._pctx)
                return removed
        return None

    def replace(self, name: str, new_stage: PipelineStage) -> PipelineStage | None:
        idx = self._index_of(name)
        if new_stage.name != name and self.get(new_stage.name) is not None:
            raise ValueError(f"RenderPipeline '{self.name}': stage '{new_stage.name}' already exists")
        old = self._stages[idx]
        if self._pctx is not None:
            old.on_detach(self._pctx)
            new_stage.on_attach(self._pctx)
        self._stages[idx] = new_stage
        return old

    def set_enabled(self, name: str, enabled: bool) -> None:
        st = self.get(name)
        if st is None:
            raise KeyError(f"RenderPipeline '{self.name}': stage '{name}' not found")
        st.enabled = bool(enabled)

    def clear(self) -> None:
        if self._pctx is not None:
            for st in self._stages:
                st.on_detach(self._pctx)
        self._stages.clear()

    def _index_of(self, name: str) -> int:
        for i, st in enumerate(self._stages):
            if st.name == name:
                return i
        raise KeyError(f"RenderPipeline '{self.name}': stage '{name}' not found")


class _SequenceFrame:
    """Кадр стека исполнения для линейного списка этапов или тела RepeatStage."""

    def __init__(
        self,
        stages: list[PipelineStage],
        loop_name: str | None = None,
        total_iters: int = 1,
    ):
        self.stages = stages
        self.stage_idx: int = 0
        self.step_idx: int = 0
        self.stage_active: bool = False
        self.stage_est_steps: int = 0
        self.loop_name = loop_name
        self.total_iters = max(1, int(total_iters))
        self.curr_iter: int = 0


class PipelineRunner:
    """
    Пошаговый исполнитель пайплайна без рекурсивного блокирования потока.
    За один тик выполняет все мгновенные этапы (yield_after_step=False) и не более одного
    квантуемого шага (yield_after_step=True), обеспечивая точную чанковую развертку и прогресс.
    """

    def __init__(self, pipeline: RenderPipeline, pctx: PipelineContext):
        self.pipeline = pipeline
        self.pctx = pctx
        self._stack: list[_SequenceFrame] = []
        self._stats: OrderedStats = OrderedStats()
        self.yielding_done: int = 0
        self.yielding_total: int = 0
        self.started_at_ns: int = 0
        self.finished: bool = False

    def start(self) -> None:
        self.abort()
        self.started_at_ns = time.perf_counter_ns()
        self._stats = OrderedStats()
        self.yielding_done = 0
        self.yielding_total = self._estimate_yielding_steps(self.pipeline.stages)
        self.finished = False
        self._stack = [_SequenceFrame(self.pipeline.stages)]

    def run_initial_non_yielding(self) -> bool:
        """Выполнить стартовые неквантуемые этапы (например, загрузку параметров) до первого чанка."""
        return self._advance(yielding_budget=0)

    def tick(self) -> bool:
        """Продвинуть пайплайн на 1 квантуемый шаг (чанк) + последующие быстрые этапы."""
        return self._advance(yielding_budget=1)

    def abort(self) -> None:
        while self._stack:
            top = self._stack.pop()
            if top.stage_active and top.stage_idx < len(top.stages):
                try:
                    top.stages[top.stage_idx].end(self.pctx)
                except Exception:
                    pass
            if top.loop_name is not None:
                self.pctx.blackboard.loop_indices.pop(top.loop_name, None)
        self.finished = True

    def build_debug_report(self) -> dict[str, Any]:
        total_ms = (time.perf_counter_ns() - self.started_at_ns) / 1_000_000.0 if self.started_at_ns else 0.0
        return {
            "pipeline": self.pipeline.name,
            "render_size": (self.pctx.render_w, self.pctx.render_h),
            "chunk_size": self.pctx.chunk_size,
            "total_ms": round(total_ms, 3),
            "yielding_steps": (self.yielding_done, self.yielding_total),
            "program_cache": {
                "hits": self.pctx.pool.stats_program_hits,
                "misses": self.pctx.pool.stats_program_misses,
            },
            "stages": [st.to_dict() for st in self._stats.values()],
        }

    def _estimate_yielding_steps(self, stages: list[PipelineStage]) -> int:
        """Предварительная оценка общего числа чанков для индикатора прогресса экспорта."""
        total = 0
        cs = max(1, int(self.pctx.chunk_size))
        default_chunks = math.ceil(self.pctx.render_w / cs) * math.ceil(self.pctx.render_h / cs)

        for st in stages:
            if not st.enabled:
                continue
            if isinstance(st, RepeatStage):
                iters = st.iterations(self.pctx) if callable(st.iterations) else int(st.iterations)
                if iters > 0:
                    total += iters * self._estimate_yielding_steps(st.stages)
            elif isinstance(st, FragmentStage) and st.yield_after_step:
                if st.condition is not None and not st.condition(self.pctx):
                    continue
                if st.chunked:
                    sz = st.target_size(self.pctx) if st.target_size is not None else (self.pctx.render_w, self.pctx.render_h)
                    gw = math.ceil(max(1, int(sz[0])) / cs)
                    gh = math.ceil(max(1, int(sz[1])) / cs)
                    total += max(1, gw * gh)
                else:
                    total += 1
            elif isinstance(st, CPUStage) and st.yield_after_step:
                if st._estimated_steps_fn is not None:
                    total += max(1, int(st._estimated_steps_fn(self.pctx)))
                else:
                    total += 1
            elif st.yield_after_step:
                total += default_chunks
        return total

    def _advance(self, yielding_budget: int) -> bool:
        if self.finished:
            return True

        used_yielding = 0
        dbg = self.pctx.debug

        while self._stack:
            frame = self._stack[-1]

            if frame.stage_idx >= len(frame.stages):
                # Текущая последовательность завершена: проверяем, есть ли ещё итерации цикла
                if frame.loop_name is not None and frame.curr_iter + 1 < frame.total_iters:
                    frame.curr_iter += 1
                    frame.stage_idx = 0
                    frame.step_idx = 0
                    frame.stage_active = False
                    self.pctx.blackboard.loop_indices[frame.loop_name] = frame.curr_iter
                    continue
                if frame.loop_name is not None:
                    self.pctx.blackboard.loop_indices.pop(frame.loop_name, None)
                self._stack.pop()
                continue

            stage = frame.stages[frame.stage_idx]
            if not stage.enabled:
                frame.stage_idx += 1
                continue

            if isinstance(stage, RepeatStage):
                iters = stage.iterations(self.pctx) if callable(stage.iterations) else int(stage.iterations)
                frame.stage_idx += 1
                if iters > 0 and stage.stages:
                    self.pctx.blackboard.loop_indices[stage.name] = 0
                    self._stack.append(
                        _SequenceFrame(stage.stages, loop_name=stage.name, total_iters=iters)
                    )
                continue

            # Если следующий шаг квантуемый, а лимит шагов в этом тике исчерпан — отдаём управление таймеру
            if stage.yield_after_step and used_yielding >= yielding_budget:
                return False

            stat = self._stats.get_or_create(stage.name, stage.kind)

            if not frame.stage_active:
                t0 = time.perf_counter_ns()
                est = stage.begin(self.pctx)
                stat.cpu_ms += (time.perf_counter_ns() - t0) / 1_000_000.0
                stat.executions += 1
                if est <= 0:
                    stage.end(self.pctx)
                    frame.stage_idx += 1
                    continue
                frame.stage_est_steps = est
                frame.step_idx = 0
                frame.stage_active = True

            is_gpu_stage = stage.kind in ("vertex", "fragment")
            if dbg.enabled and dbg.check_gl_errors and is_gpu_stage:
                self.pctx.ctx.clear_errors()

            query = None
            if dbg.enabled and dbg.gpu_timing and is_gpu_stage and not stage.yield_after_step:
                try:
                    query = self.pctx.ctx.query(time=True)
                except Exception:
                    query = None

            t0 = time.perf_counter_ns()
            with self.pctx.ctx.debug_scope(f"SDForge:{stage.name}"):
                if query is not None:
                    with query:
                        stage_done = stage.step(self.pctx, frame.step_idx)
                else:
                    stage_done = stage.step(self.pctx, frame.step_idx)
            stat.cpu_ms += (time.perf_counter_ns() - t0) / 1_000_000.0
            stat.steps += 1

            if query is not None:
                try:
                    gpu_ns = query.elapsed
                    stat.gpu_ms = (stat.gpu_ms or 0.0) + (gpu_ns / 1_000_000.0)
                except Exception:
                    pass

            if dbg.enabled and dbg.check_gl_errors and is_gpu_stage:
                err = self.pctx.ctx.error
                if err and err != "GL_NO_ERROR":
                    stat.gl_error = err
                    self.pctx.log_debug(
                        f"SDForge.pipeline: GL error '{err}' in stage '{stage.name}' (step={frame.step_idx})"
                    )

            frame.step_idx += 1
            if stage.yield_after_step:
                used_yielding += 1
                self.yielding_done += 1

            if stage_done:
                t0 = time.perf_counter_ns()
                stage.end(self.pctx)
                stat.cpu_ms += (time.perf_counter_ns() - t0) / 1_000_000.0
                frame.stage_active = False
                frame.step_idx = 0
                frame.stage_idx += 1

        # Все этапы завершены; при активном debug.inspect_target подменяем выходное изображение
        if dbg.enabled and dbg.inspect_target:
            inspected = self.pctx.pool.read_target_to_qimage(
                dbg.inspect_target,
                flush_gpu=self.pctx.in_worker_thread,
                highlight_nan_inf=dbg.highlight_nan_inf,
            )
            if inspected is not None:
                self.pctx.blackboard.output_image = inspected

        self.finished = True
        if dbg.enabled and dbg.log_frame_summary:
            self.pctx.log_debug(f"SDForge.pipeline summary: {self.build_debug_report()}")
        return True


class OrderedStats(dict):
    """Словарь метрик этапов с сохранением порядка первого вызова."""

    def get_or_create(self, name: str, kind: str) -> StageRuntimeStats:
        st = self.get(name)
        if st is None:
            st = StageRuntimeStats(name=name, kind=kind)
            self[name] = st
        return st

#endregion


#region Внутренний воркер и публичный базовый класс рендерера

class _PipelineWorker(QtCore.QObject):
    """
    Исполнитель GL-контекста и таймера чанков.
    Всегда работает строго в том потоке, которому принадлежит (GUI или выделенный QThread).
    """

    frame_ready = pyqtSignal(QImage)
    init_done = pyqtSignal(bool)
    export_progress_changed = pyqtSignal(int, int)
    export_done = pyqtSignal(object)
    debug_report_ready = pyqtSignal(dict)
    warmup_started = pyqtSignal()
    warmup_finished = pyqtSignal(bool, str)

    def __init__(self, owner: "BasePipelineRenderer", in_worker_thread: bool):
        super().__init__(None)
        self._owner = owner
        self._in_worker_thread = bool(in_worker_thread)

        self.ctx: moderngl.Context | None = None
        self.pool: ResourcePool | None = None
        self.pctx: PipelineContext | None = None
        self.pipelines: dict[str, RenderPipeline] = {}

        self._runner: PipelineRunner | None = None
        self._active_mode: str | None = None  # "frame" | "export"
        self._frame_seq: int = 0
        self._initialized: bool = False

        self.fps_timer = QtCore.QTimer(self)
        # @pyqtSlot на _on_timer_tick обязателен: без него PyQt5 создаёт PyQtSlotProxy в GUI-потоке,
        # и после moveToThread тики таймера уходят в GUI-поток без OpenGL-контекста
        self.fps_timer.timeout.connect(self._on_timer_tick, Qt.DirectConnection)

    def _assert_own_thread(self) -> None:
        if QtCore.QThread.currentThread() is not self.thread():
            raise RuntimeError(
                "PipelineWorker: обращение к OpenGL-контексту не из потока владельца контекста"
            )

    def ensure_context(self) -> PipelineContext:
        self._assert_own_thread()
        if self.ctx is None:
            if self._owner._is_debug_active():
                self._owner._on_internal_debug_msg(
                    f"SDForge.worker.ensure_context: creating context, in_worker={self._in_worker_thread} "
                    f"qthread={QtCore.QThread.currentThread().objectName() or hex(id(QtCore.QThread.currentThread()))} "
                    f"wgl_before={_current_wgl_context_hex()}"
                )
            self.ctx = moderngl.create_context(standalone=True)
            if self._owner._is_debug_active():
                info = {}
                try:
                    info = self.ctx.info
                except Exception:
                    pass
                self._owner._on_internal_debug_msg(
                    f"SDForge.worker.ensure_context: created version={self.ctx.version_code} "
                    f"vendor={info.get('GL_VENDOR')} renderer={info.get('GL_RENDERER')} "
                    f"gl_ver={info.get('GL_VERSION')} wgl_after={_current_wgl_context_hex()}"
                )
        if self.pool is None or self.pctx is None:
            self.pool = ResourcePool(self.ctx, debug_cb=self._owner._on_internal_debug_msg)
            self.pctx = PipelineContext(
                ctx=self.ctx,
                pool=self.pool,
                debug=self._owner.debug_config,
                in_worker_thread=self._in_worker_thread,
            )
            built = self._owner.setup_pipelines(self.pctx)
            for name, pipe in built.items():
                pipe.bind_context(self.pctx)
                self.pipelines[name] = pipe
            self._owner._on_context_created(self.pctx)
        return self.pctx

    @pyqtSlot(dict)
    def slot_initialize(self, payload: dict) -> None:
        if self._owner._is_debug_active():
            self._owner._on_internal_debug_msg(
                f"SDForge.worker.slot_initialize: enter w={payload.get('w')} h={payload.get('h')} "
                f"scale={payload.get('preview_scale')} wgl={_current_wgl_context_hex()}"
            )
        try:
            self.slot_stop_render()
            pctx = self.ensure_context()
            self._owner.handle_initialize(pctx, payload)
            self._initialized = True
            if self._owner._is_debug_active():
                self._owner._on_internal_debug_msg("SDForge.worker.slot_initialize: success -> init_done(True)")
            self.init_done.emit(True)
        except Exception as exc:
            self._initialized = False
            self._owner._on_internal_debug_msg(
                f"SDForge.worker.slot_initialize: FAILED: {exc}\n{traceback.format_exc()}"
            )
            self._owner._on_initialize_error(exc)
            self.init_done.emit(False)

    @pyqtSlot(dict)
    def slot_resize(self, payload: dict) -> None:
        try:
            pctx = self.ensure_context()
            self._owner.handle_resize(pctx, payload)
            for pipe in self.pipelines.values():
                pipe.notify_resize(pctx, pctx.render_w, pctx.render_h)
        except Exception as exc:
            self._owner._on_internal_debug_msg(
                f"SDForge.worker.slot_resize: FAILED: {exc}\n{traceback.format_exc()}"
            )

    @pyqtSlot(int, dict)
    def slot_start_frame(self, generation: int, payload: dict) -> None:
        latest = self._owner._get_latest_frame_gen()
        if self._in_worker_thread and generation < latest:
            if self._owner._is_debug_active():
                self._owner._on_internal_debug_msg(
                    f"SDForge.worker.slot_start_frame: skipped stale gen={generation} latest={latest}"
                )
            return
        if not self._initialized or self.pctx is None:
            if self._owner._is_debug_active():
                self._owner._on_internal_debug_msg(
                    f"SDForge.worker.slot_start_frame: skipped not initialized gen={generation}"
                )
            return

        try:
            self.slot_stop_render()
            pipe_name, interval_ms = self._owner.resolve_pipeline_for_frame(self.pctx, payload)
            pipeline = self.pipelines.get(pipe_name)
            if pipeline is None:
                raise KeyError(f"Pipeline '{pipe_name}' is not registered")

            self._frame_seq += 1
            self._active_mode = "frame"
            self.pctx.blackboard = FrameBlackboard(payload=payload)
            self._runner = PipelineRunner(pipeline, self.pctx)
            self._runner.start()

            done = self._runner.run_initial_non_yielding()
            if self._owner._is_debug_active():
                self._owner._on_internal_debug_msg(
                    f"SDForge.worker.slot_start_frame: seq={self._frame_seq} gen={generation} "
                    f"size={self.pctx.render_w}x{self.pctx.render_h} chunks={self._runner.yielding_total} "
                    f"done_imm={done} interval_ms={interval_ms} wgl={_current_wgl_context_hex()}"
                )
            if done:
                self._finish_active_run()
            else:
                self.fps_timer.start(max(1, int(interval_ms)))
        except Exception as exc:
            self.slot_stop_render()
            self._owner._on_internal_debug_msg(
                f"SDForge.worker.slot_start_frame: FAILED: {exc}\n{traceback.format_exc()}"
            )

    @pyqtSlot(dict)
    def slot_start_export(self, payload: dict) -> None:
        try:
            self.slot_stop_render()
            pctx = self.ensure_context()
            pipe_name, interval_ms = self._owner.resolve_pipeline_for_export(pctx, payload)
            pipeline = self.pipelines.get(pipe_name)
            if pipeline is None:
                raise KeyError(f"Export pipeline '{pipe_name}' is not registered")

            self._owner.before_export_start(pctx, payload)
            self._active_mode = "export"
            pctx.blackboard = FrameBlackboard(payload=payload)
            self._runner = PipelineRunner(pipeline, pctx)
            self._runner.start()

            done = self._runner.run_initial_non_yielding()
            total = max(1, self._runner.yielding_total)
            self.export_progress_changed.emit(0, total)

            if done:
                self._finish_active_run()
            else:
                self.fps_timer.start(max(1, int(interval_ms)))
        except Exception as exc:
            self._owner._on_export_error(exc)
            self.slot_stop_render()
            self.export_done.emit(None)

    @pyqtSlot()
    def slot_stop_render(self) -> None:
        self.fps_timer.stop()
        if self._runner is not None:
            self._runner.abort()
            self._runner = None
        self._active_mode = None

    @pyqtSlot(object)
    def slot_mutate_pipeline(
        self,
        payload: tuple[Callable[[dict[str, RenderPipeline], PipelineContext | None], Any], list[BaseException | None]],
    ) -> None:
        fn, error_box = payload
        try:
            self.slot_stop_render()
            fn(self.pipelines, self.pctx)
        except BaseException as exc:
            error_box[0] = exc

    @pyqtSlot()
    def slot_release_all(self) -> None:
        self.slot_stop_render()
        for pipe in self.pipelines.values():
            pipe.bind_context(None)
        self.pipelines.clear()
        if self.pctx is not None:
            self._owner.on_release_custom_state(self.pctx)
        if self.pool is not None:
            self.pool.release_all()
            self.pool = None
        if self.ctx is not None:
            if self._in_worker_thread:
                try:
                    self.ctx.release()
                except Exception:
                    pass
                self.ctx = None
        self.pctx = None
        self._initialized = False
        self._owner._on_release_done()

    @pyqtSlot()
    def _on_timer_tick(self) -> None:
        self._assert_own_thread()
        if self._runner is None or self.pctx is None:
            self.fps_timer.stop()
            return

        mode = self._active_mode
        try:
            done = self._runner.tick()
            if mode == "export" and self._runner is not None:
                total = max(1, self._runner.yielding_total)
                done_steps = min(self._runner.yielding_done, total)
                self.export_progress_changed.emit(done_steps, total)
            if done:
                self._finish_active_run()
        except Exception as exc:
            self.slot_stop_render()
            if mode == "export":
                self._owner._on_export_error(exc)
                self.export_done.emit(None)
            else:
                self._owner._on_internal_debug_msg(
                    f"Pipeline frame error: {exc}\n{traceback.format_exc()}"
                )

    def _finish_active_run(self) -> None:
        self.fps_timer.stop()
        runner = self._runner
        mode = self._active_mode
        self._runner = None
        self._active_mode = None

        if runner is None or self.pctx is None:
            return

        img = self.pctx.blackboard.output_image
        if self.pctx.debug.enabled:
            report = runner.build_debug_report()
            self._owner._store_debug_report(report)
            self.debug_report_ready.emit(report)

        if mode == "frame":
            self._owner._store_last_image(img)
            if img is not None:
                if self._owner._is_debug_active():
                    c = img.pixelColor(img.width() // 2, img.height() // 2).getRgb()
                    self._owner._on_internal_debug_msg(
                        f"SDForge.worker.finish_frame: seq={self._frame_seq} img={img.width()}x{img.height()} "
                        f"null={img.isNull()} center_rgba={c}"
                    )
                self.frame_ready.emit(img.copy())
            elif self._owner._is_debug_active():
                self._owner._on_internal_debug_msg(
                    f"SDForge.worker.finish_frame: seq={self._frame_seq} img is None!"
                )
        elif mode == "export":
            self._owner.after_export_finish(self.pctx)
            self.export_done.emit(img.copy() if img is not None else None)


class BasePipelineRenderer(QtCore.QObject):
    """
    Базовый класс рендерера с модульным пайплайном и опциональным рабочим потоком.
    Полностью совместим по сигналам и слотам с контрактом SDFRenderPreviewer.
    """

    frame_ready = pyqtSignal(QImage)
    init_done = pyqtSignal(bool)
    export_progress_changed = pyqtSignal(int, int)
    export_done = pyqtSignal(object)
    debug_report_ready = pyqtSignal(dict)

    # Внутренние сигналы доставки команд в QThread при use_thread=True
    _sig_initialize = pyqtSignal(dict)
    _sig_resize = pyqtSignal(dict)
    _sig_start_frame = pyqtSignal(int, dict)
    _sig_start_export = pyqtSignal(dict)
    _sig_stop_render = pyqtSignal()
    _sig_mutate_pipeline = pyqtSignal(object)
    _sig_release_all = pyqtSignal()

    def __init__(
        self,
        parent=None,
        *,
        use_thread: bool = False,
        debug_config: RenderDebugConfig | None = None,
        warmup_python_executable: str | Path | None = None,
        piplib_dir: str | Path | None = None,
    ):
        super().__init__(parent)
        self._use_thread = bool(use_thread)
        self.debug_config = debug_config or RenderDebugConfig(enabled=bool(DEBUG_LOG_ENABLED))
        self._warmup_python_executable = warmup_python_executable
        self._piplib_dir = Path(piplib_dir) if piplib_dir is not None else find_piplib_dir()

        self._state_lock = threading.Lock()
        self._thread_lock = threading.Lock()
        self._last_image: QImage | None = None
        self._last_debug_report: dict[str, Any] | None = None
        self._latest_frame_gen: int = 0

        self._warmup_process: QProcess | None = None
        self._is_warming_up: bool = False
        self._warmup_auto_init: bool = False
        self._warmup_fallback_to_init: bool = True
        self._warmup_init_payload: dict | None = None

        self._thread: QtCore.QThread | None = None
        self._worker = _PipelineWorker(self, in_worker_thread=self._use_thread)

        self._worker.frame_ready.connect(self.frame_ready)
        self._worker.init_done.connect(self.init_done)
        self._worker.export_progress_changed.connect(self.export_progress_changed)
        self._worker.export_done.connect(self.export_done)
        self._worker.debug_report_ready.connect(self.debug_report_ready)

        if self._use_thread:
            self._thread = QtCore.QThread()
            self._thread.setObjectName("SDForgeRenderThread")
            self._worker.moveToThread(self._thread)

            self._sig_initialize.connect(self._worker.slot_initialize, Qt.QueuedConnection)
            self._sig_resize.connect(self._worker.slot_resize, Qt.QueuedConnection)
            self._sig_start_frame.connect(self._worker.slot_start_frame, Qt.QueuedConnection)
            self._sig_start_export.connect(self._worker.slot_start_export, Qt.QueuedConnection)
            self._sig_stop_render.connect(self._worker.slot_stop_render, Qt.QueuedConnection)
            self._sig_mutate_pipeline.connect(self._worker.slot_mutate_pipeline, Qt.BlockingQueuedConnection)
            self._sig_release_all.connect(self._worker.slot_release_all, Qt.BlockingQueuedConnection)

            self._thread.start()

    @property
    def is_threaded(self) -> bool:
        return self._use_thread

    @property
    def is_warming_up(self) -> bool:
        return self._is_warming_up

    #region --- Точки расширения для наследников ---
    def get_warmup_shaders(self, payload: dict | None = None) -> list[dict]:
        """
        Вернуть список словарей аргументов для ctx.program(**spec),
        которые нужно предварительно скомпилировать в отдельном процессе (GLCache warmup).
        """
        return []

    def setup_pipelines(self, pctx: PipelineContext) -> dict[str, RenderPipeline]:
        """Создать и вернуть словарь именованных пайплайнов (вызывается при создании контекста)."""
        return {
            "preview": RenderPipeline("preview"),
            "export": RenderPipeline("export"),
        }

    def prepare_frame_payload(self, payload: dict, is_export: bool = False) -> dict:
        """
        Создать потокобезопасный снимок входного payload на стороне вызывающего потока.
        Вызывается автоматически при use_thread=True или debug_config.strict_isolation=True.
        """
        return dict(payload)

    def handle_initialize(self, pctx: PipelineContext, payload: dict) -> None:
        """Инициализировать шейдеры/размеры из payload (вызывается в потоке контекста)."""
        pass

    def handle_resize(self, pctx: PipelineContext, payload: dict) -> None:
        """Обновить метрики размера кадра из payload (вызывается в потоке контекста)."""
        pctx.set_viewport_metrics(
            w=payload["w"],
            h=payload["h"],
            preview_scale=float(payload.get("preview_scale", 1.0)),
            chunk_size=int(payload["chunk_size"]) if "chunk_size" in payload else None,
        )

    def resolve_pipeline_for_frame(self, pctx: PipelineContext, payload: dict) -> tuple[str, int]:
        return str(payload.get("pipeline", "preview")), max(1, int(payload.get("chunk_interval_ms", 1)))

    def resolve_pipeline_for_export(self, pctx: PipelineContext, payload: dict) -> tuple[str, int]:
        return str(payload.get("pipeline", "export")), max(1, int(payload.get("chunk_interval_ms", 1)))

    def before_export_start(self, pctx: PipelineContext, payload: dict) -> None:
        pass

    def after_export_finish(self, pctx: PipelineContext) -> None:
        pass

    def on_release_custom_state(self, pctx: PipelineContext) -> None:
        pctx.custom_state.clear()

    def _on_context_created(self, pctx: PipelineContext) -> None:
        pass

    def _on_initialize_error(self, exc: Exception) -> None:
        self._on_internal_debug_msg(f"Renderer initialize failed: {exc}")

    def _on_export_error(self, exc: Exception) -> None:
        self._on_internal_debug_msg(f"Renderer export failed: {exc}")

    def _on_release_done(self) -> None:
        pass

    def _is_debug_active(self) -> bool:
        return bool(self.debug_config.enabled or DEBUG_LOG_ENABLED)

    def _on_internal_debug_msg(self, message: str) -> None:
        if self._is_debug_active():
            _write_pipeline_log(message, force=bool(self.debug_config.enabled))

    #endregion

    #region --- Управление пайплайнами и этапами налету ---

    def _next_frame_gen(self) -> int:
        with self._state_lock:
            self._latest_frame_gen += 1
            return self._latest_frame_gen

    def _get_latest_frame_gen(self) -> int:
        with self._state_lock:
            return self._latest_frame_gen

    def _apply_pipeline_mutation(
        self, fn: Callable[[dict[str, RenderPipeline], PipelineContext | None], Any]
    ) -> None:
        error_box: list[BaseException | None] = [None]
        payload = (fn, error_box)
        if self._use_thread:
            with self._thread_lock:
                thread = self._thread
                running = thread is not None and thread.isRunning()
                if running:
                    if QtCore.QThread.currentThread() is thread:
                        self._worker.slot_mutate_pipeline(payload)
                    else:
                        self._sig_mutate_pipeline.emit(payload)
                else:
                    self._worker.slot_mutate_pipeline(payload)
        else:
            self._worker.slot_mutate_pipeline(payload)
        if error_box[0] is not None:
            raise error_box[0]

    def get_pipeline(self, name: str = "preview") -> RenderPipeline | None:
        """Вернуть объект пайплайна (для чтения в однопоточном режиме или внутри воркера)."""
        return self._worker.pipelines.get(name)

    def register_pipeline(self, pipeline: RenderPipeline) -> None:
        """Добавить новый или заменить существующий именованный пайплайн."""
        def _op(pipes: dict[str, RenderPipeline], pctx: PipelineContext | None):
            old = pipes.get(pipeline.name)
            if old is not None and old is not pipeline:
                old.bind_context(None)
            if pctx is not None:
                pipeline.bind_context(pctx)
            pipes[pipeline.name] = pipeline
        self._apply_pipeline_mutation(_op)

    def add_stage(self, pipeline_name: str, stage: PipelineStage, index: int | None = None) -> None:
        def _op(pipes: dict[str, RenderPipeline], _pctx: PipelineContext | None):
            pipe = pipes[pipeline_name]
            if index is None:
                pipe.add(stage)
            else:
                pipe.insert(index, stage)
        self._apply_pipeline_mutation(_op)

    def insert_stage_before(self, pipeline_name: str, target_name: str, stage: PipelineStage) -> None:
        self._apply_pipeline_mutation(lambda pipes, _: pipes[pipeline_name].insert_before(target_name, stage))

    def insert_stage_after(self, pipeline_name: str, target_name: str, stage: PipelineStage) -> None:
        self._apply_pipeline_mutation(lambda pipes, _: pipes[pipeline_name].insert_after(target_name, stage))

    def remove_stage(self, pipeline_name: str, stage_name: str) -> None:
        self._apply_pipeline_mutation(lambda pipes, _: pipes[pipeline_name].remove(stage_name))

    def replace_stage(self, pipeline_name: str, stage_name: str, new_stage: PipelineStage) -> None:
        self._apply_pipeline_mutation(lambda pipes, _: pipes[pipeline_name].replace(stage_name, new_stage))

    def set_stage_enabled(self, pipeline_name: str, stage_name: str, enabled: bool) -> None:
        self._apply_pipeline_mutation(lambda pipes, _: pipes[pipeline_name].set_enabled(stage_name, enabled))

    #endregion

    #region --- Дебаг-режим и диагностика ---

    def set_debug_mode(self, enabled: bool, **options) -> None:
        """Включить/выключить дебаг-режим и обновить опции RenderDebugConfig."""
        with self._state_lock:
            self.debug_config.enabled = bool(enabled)
            for key, val in options.items():
                if not hasattr(self.debug_config, key):
                    raise AttributeError(f"RenderDebugConfig has no option '{key}'")
                setattr(self.debug_config, key, val)

    def set_test_shader_mode(self, enabled: bool) -> None:
        """Переключить глобальный тестовый фрагментный шейдер."""
        with self._state_lock:
            self.debug_config.global_test_shader = bool(enabled)

    def get_last_debug_report(self) -> dict[str, Any] | None:
        with self._state_lock:
            return dict(self._last_debug_report) if self._last_debug_report is not None else None

    def get_image(self) -> QImage | None:
        with self._state_lock:
            return self._last_image.copy() if self._last_image is not None else None

    def _store_last_image(self, img: QImage | None) -> None:
        with self._state_lock:
            self._last_image = img.copy() if img is not None else None

    def _store_debug_report(self, report: dict[str, Any]) -> None:
        with self._state_lock:
            self._last_debug_report = report

    #endregion

    #region --- Публичные слоты (контракт GPURenderer / SDFRenderPreviewer) ---

    @pyqtSlot(dict)
    def initialize(self, payload: dict) -> None:
        data = dict(payload)
        if self._use_thread:
            self._sig_initialize.emit(data)
        else:
            self._worker.slot_initialize(data)

    @pyqtSlot(dict)
    def resize(self, payload: dict) -> None:
        data = dict(payload)
        if self._use_thread:
            self._sig_resize.emit(data)
        else:
            self._worker.slot_resize(data)

    @pyqtSlot(dict)
    def start_frame(self, payload: dict) -> None:
        try:
            need_freeze = self._use_thread or self.debug_config.strict_isolation
            prepared = self.prepare_frame_payload(payload, is_export=False) if need_freeze else dict(payload)
        except Exception as exc:
            self._on_internal_debug_msg(
                f"SDForge.host.start_frame: prepare_frame_payload FAILED: {exc}\n{traceback.format_exc()}"
            )
            return
        gen = self._next_frame_gen()
        if self._use_thread:
            self._sig_start_frame.emit(gen, prepared)
        else:
            self._worker.slot_start_frame(gen, prepared)

    @pyqtSlot(dict)
    def start_export(self, payload: dict) -> None:
        need_freeze = self._use_thread or self.debug_config.strict_isolation
        prepared = self.prepare_frame_payload(payload, is_export=True) if need_freeze else dict(payload)
        if self._use_thread:
            self._sig_start_export.emit(prepared)
        else:
            self._worker.slot_start_export(prepared)

    @pyqtSlot()
    def stop_render(self) -> None:
        # Инкремент поколения мгновенно отсекает уже стоящие в очереди старые start_frame
        self._next_frame_gen()
        if self._use_thread:
            self._sig_stop_render.emit()
        else:
            self._worker.slot_stop_render()

    def warmup_shaders(
        self,
        shaders: list[dict] | None = None,
        *,
        init_payload: dict | None = None,
        auto_initialize: bool = True,
        fallback_to_init: bool = True,
    ) -> bool:
        """
        Асинхронно прогревает драйверный кеш шейдеров (GLCache) в отдельном процессе QProcess.
        Путь к встроенному Python и piplib определяется автоматически (через init.py / автопоиск).
        """
        if self._is_warming_up:
            return False

        specs = shaders if shaders is not None else self.get_warmup_shaders(init_payload)
        if not specs:
            self.warmup_finished.emit(True, "")
            if auto_initialize:
                self.initialize(init_payload or {})
            return True

        python_exe = find_warmup_python(self._warmup_python_executable, self._piplib_dir)
        if python_exe is None:
            msg = "Python interpreter not found for out-of-process shader warmup"
            self._on_internal_debug_msg(f"SDForge.warmup: {msg}")
            self.warmup_finished.emit(False, msg)
            if auto_initialize and fallback_to_init:
                self.initialize(init_payload or {})
            return False

        proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        extra_paths: list[str] = []
        if self._piplib_dir is not None and self._piplib_dir.is_dir():
            extra_paths.append(str(self._piplib_dir))
        for p in sys.path:
            if p and p not in extra_paths:
                extra_paths.append(p)
        existing_pp = env.value("PYTHONPATH", "")
        if existing_pp:
            extra_paths.append(existing_pp)
        env.insert("PYTHONPATH", os.pathsep.join(extra_paths))
        proc.setProcessEnvironment(env)
        proc.setProgram(python_exe)
        proc.setArguments(["-c", _SHADER_WARMUP_SCRIPT])
        proc.finished.connect(self._on_warmup_finished)
        proc.errorOccurred.connect(self._on_warmup_error)

        self._warmup_process = proc
        self._is_warming_up = True
        self._warmup_auto_init = bool(auto_initialize)
        self._warmup_fallback_to_init = bool(fallback_to_init)
        self._warmup_init_payload = dict(init_payload) if init_payload is not None else {}
        self.warmup_started.emit()

        proc.start()
        payload_bytes = json.dumps(specs).encode("utf-8")
        proc.write(payload_bytes)
        proc.closeWriteChannel()
        return True

    def cancel_warmup(self) -> None:
        """Немедленно останавливает фоновый прогрев шейдеров без блокировки GUI."""
        proc = self._warmup_process
        if proc is None:
            return
        self._warmup_process = None
        self._is_warming_up = False
        self._warmup_auto_init = False
        self._warmup_init_payload = None
        try:
            proc.finished.disconnect(self._on_warmup_finished)
        except Exception:
            pass
        try:
            proc.errorOccurred.disconnect(self._on_warmup_error)
        except Exception:
            pass
        if proc.state() != QProcess.NotRunning:
            proc.kill()
        proc.deleteLater()

    def _on_warmup_finished(self, exit_code: int, exit_status) -> None:
        proc = self._warmup_process
        auto_init = self._warmup_auto_init
        fallback = self._warmup_fallback_to_init
        init_payload = self._warmup_init_payload or {}

        self._warmup_process = None
        self._is_warming_up = False
        self._warmup_auto_init = False
        self._warmup_init_payload = None

        err_text = ""
        if proc is not None:
            err_text = bytes(proc.readAllStandardError()).decode("utf-8", errors="replace").strip()
            proc.deleteLater()

        ok = (exit_code == 0 and exit_status == QProcess.NormalExit)
        if not ok:
            self._on_internal_debug_msg(f"SDForge.warmup failed (code={exit_code}): {err_text}")
        self.warmup_finished.emit(ok, "" if ok else (err_text or f"Exit code {exit_code}"))

        if auto_init and (ok or fallback):
            self.initialize(init_payload)

    def _on_warmup_error(self, error) -> None:
        if not self._is_warming_up:
            return
        proc = self._warmup_process
        auto_init = self._warmup_auto_init
        fallback = self._warmup_fallback_to_init
        init_payload = self._warmup_init_payload or {}

        self._warmup_process = None
        self._is_warming_up = False
        self._warmup_auto_init = False
        self._warmup_init_payload = None

        err_msg = proc.errorString() if proc is not None else str(error)
        if proc is not None:
            proc.deleteLater()
        self._on_internal_debug_msg(f"SDForge.warmup QProcess error: {err_msg}")
        self.warmup_finished.emit(False, f"QProcess error: {err_msg}")

        if auto_init and fallback:
            self.initialize(init_payload)

    @pyqtSlot()
    def release_all(self) -> None:
        self.cancel_warmup()
        self._next_frame_gen()
        if self._use_thread:
            with self._thread_lock:
                thread = self._thread
                if thread is not None and thread.isRunning():
                    if QtCore.QThread.currentThread() is thread:
                        self._worker.slot_release_all()
                    else:
                        self._sig_release_all.emit()
                    thread.quit()
                    thread.wait(5000)
        else:
            self._worker.slot_release_all()

    #endregion

#endregion
