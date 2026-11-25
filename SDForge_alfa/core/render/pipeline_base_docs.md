# Документация `pipeline_base.py` (SDForge Render Pipeline)

Модуль `pipeline_base.py` — базовое ядро модульного рендер-пайплайна на **ModernGL + PyQt5 + NumPy**. Он позволяет динамически собирать, изменять и чередовать CPU-, Vertex- (Transform Feedback) и Fragment-этапы, объединять их в циклы (`RepeatStage`), выполнять рендер по чанкам без блокировки UI, профилировать кадры и переключаться между однопоточным режимом и выделенным `QThread`.

---

## 1. Архитектура

```text
BasePipelineRenderer (публичный API, сигналы/слоты PyQt5, GUI-поток)
 └── _PipelineWorker (владелец moderngl.Context и QTimer, GUI или QThread)
      ├── PipelineContext (метрики вьюпорта, FrameBlackboard, custom_state)
      │    └── ResourcePool (FBO/RenderTarget, VBO/UBO, Texture/TextureArray, LRU-кеш программ)
      ├── RenderPipeline (мутируемый список этапов)
      └── PipelineRunner (пошаговый планировщик без рекурсии)
           ├── CPUStage (обычные функции и генераторы с yield)
           ├── VertexStage (GPU Transform Feedback -> Buffer)
           ├── FragmentStage (полноэкранный квад или геометрия, чанкинг)
           └── RepeatStage (композитный цикл по вложенным этапам)
```

### Ключевые гарантии текущей версии
- **Изоляция OpenGL-контекста**: все вызовы ModernGL выполняются строго в потоке владельца `_PipelineWorker`.
- **Повторная инициализация**: `release_all()` корректно освобождает ресурсы и очищает пайплайны, позволяя безопасно вызывать `initialize()` повторно как в однопоточном, так и в многопоточном режиме.
- **LRU-кеш программ**: `ResourcePool` ограничивает число скомпилированных шейдерных программ (`max_programs=128`) и освобождает вытесненные.
- **Векторизованное чтение float-таргетов**: `read_target_to_qimage()` для `dtype="f4"` использует `numpy` (вертикальный переворот OpenGL → Qt, `clip`, опциональная подсветка `NaN`/`Inf`).
- **Проброс исключений при мутациях**: ошибки в `add_stage()`, `replace_stage()` и других мутациях пайплайна пробрасываются в вызывающий поток даже при `use_thread=True`.
- **Неблокирующий холодный прогрев шейдеров (`QProcess`)**: тяжёлая первичная компиляция шейдеров (2–3 минуты) выносится в отдельный дочерний процесс Python (автоматически подхватывается из `piplib/python/` через `init.py`), прогревая драйверный кеш (GLCache) без блокировки окна плагина и Krita.

---

## 2. Справочник компонентов

### 2.1. `ResourcePool` и `UniformAccessor`
Пул именованных GPU-ресурсов, привязанный к одному `moderngl.Context`:
- `ensure_target(name, size, components=4, dtype="f1", depth=False, filter_mode=...) -> RenderTarget`
- `ensure_buffer(name, reserve_bytes=0, data=None, dynamic=True) -> moderngl.Buffer`
- `ensure_texture_2d(name, size, ...)` / `ensure_texture_array(name, size, ...)`
- `copy_buffer_to_texture_2d(buffer_name, texture_name, size, ...)` / `copy_texture_to_buffer(source_name, buffer_name)` — прямое GPU → GPU копирование через PBO.
- `get_program(vert_src, frag_src=None, geom_src=None, varyings=(), ...)` — компиляция с LRU-кешированием.
- `read_target_to_qimage(target_name, flush_gpu=False, highlight_nan_inf=False) -> QImage | None` — чтение `f1` или `f4` таргета в `QImage`.
- `inspect_buffer_struct(buffer_name, fmt, count=16) -> list[tuple]` — отладочное чтение структур из GPU-буфера.

`UniformAccessor` кеширует униформы программы и поддерживает `set_value()`, `set_int_array()` и `set_struct_array()`.

### 2.2. `PipelineContext` и `FrameBlackboard`
- `pctx.ctx` — `moderngl.Context`.
- `pctx.pool` — `ResourcePool`.
- `pctx.render_w`, `pctx.render_h`, `pctx.chunk_size` — текущие размеры рендера и тайла.
- `pctx.custom_state: dict` — состояние между кадрами.
- `pctx.blackboard` — пересоздаётся на каждый кадр:
  - `payload` — входные данные кадра;
  - `uniforms` — общие униформы;
  - `data` — произвольные промежуточные данные между этапами;
  - `loop_indices` — текущие индексы итераций активных `RepeatStage`;
  - `output_image` — итоговый `QImage`, который воркер отправит через `frame_ready` / `export_done`.

### 2.3. Этапы (`PipelineStage`)
| Класс | Назначение | Квантование (`yield_after_step`) |
|---|---|---|
| `CPUStage` | Обычная функция `fn(pctx)` или генератор (`yield`) | По умолчанию `True` для генераторов |
| `VertexStage` | GPU Transform Feedback: чтение буферов/текстур → запись varyings в буфер | По умолчанию `False` |
| `FragmentStage` | Растеризация полноэкранного квада или геометрии (`BufferInputSpec`) в `RenderTarget` | По умолчанию `True` при `chunked=True` |
| `RepeatStage` | Повторение вложенных этапов `N` раз с записью индекса в `blackboard.loop_indices[name]` | Делегируется вложенным этапам |

Для читаемой настройки `FragmentStage` доступны спецификации (при этом старые именованные аргументы полностью сохранены):
- `TargetSpec(name="main", dtype="f1", size=None, filter_mode=...)`
- `GeometrySpec(inputs=None, index_buffer=None, primitive_mode=..., vertices=-1, instances=1)`
- `RenderStateSpec(clear_color=..., depth_test=False, enable_flags=..., blend_func=None, wireframe=False, finish_each_step=False)`

### 2.4. `RenderDebugConfig`
- `enabled`: включает сбор `StageRuntimeStats` и публикацию `debug_report_ready`.
- `check_gl_errors`: проверяет `ctx.error` после каждого GPU-шага.
- `gpu_timing`: замеряет GPU-время через `ctx.query(time=True)` на неквантуемых этапах.
- `dump_failed_shaders`: логирует исходник шейдера с номерами строк при ошибке компиляции.
- `strict_isolation`: вызывает `prepare_frame_payload()` даже при `use_thread=False`.
- `inspect_target`: подменяет финальный `output_image` выбранным промежуточным `RenderTarget`.
- `highlight_nan_inf`: красит `NaN`/`Inf` пиксели float-таргета в пурпурный `(255, 0, 255)`.
- `global_test_shader` / `test_shader_stages`: подменяет фрагментный шейдер тестовой заливкой.

---

### 2.5. Холодный прогрев шейдеров (`QProcess` + `piplib`)

При первом холодном запуске тяжёлые GLSL-шейдеры могут компилироваться 2–3 минуты. Даже при `use_thread=True` компиляция внутри процесса Krita способна удерживать GIL или блокировать драйвер OpenGL. Для решения этой проблемы в `BasePipelineRenderer` встроен асинхронный прогрев через `QProcess`.

В дочерних классах **не нужно указывать пути вручную**: `init.py` при запуске плагина сам находит `piplib` и `piplib/python/python.exe`, проверяет `python*._pth` и сохраняет пути в `SDFORGE_PIPLIB_DIR` / `SDFORGE_WARMUP_PYTHON`, откуда `BasePipelineRenderer` подхватывает их автоматически.

Как это работает:
1. В наследнике переопределяется `get_warmup_shaders(payload) -> list[dict]`, возвращающий список словарей аргументов для `ctx.program(**spec)`.
2. Вызывается `renderer.warmup_shaders(init_payload=..., auto_initialize=True)`.
3. `find_warmup_python()` автоматически выбирает интерпретатор в порядке: явно переданный `warmup_python_executable` → `SDFORGE_WARMUP_PYTHON` (из `init.py`) → `piplib/python/python.exe` → `sys.executable` (если это не `krita.exe`) → системный `PATH`.
4. Дочерний процесс компилирует шейдеры в standalone-контексте ModernGL (прогревая GLCache драйвера) и завершается.
5. По сигналу `warmup_finished(ok, err)` автоматически вызывается `initialize(init_payload)` (если `auto_initialize=True`), где `ctx.program()` мгновенно забирает прогретые шейдеры из кеша драйвера.
6. При закрытии плагина `release_all()` автоматически вызывает `cancel_warmup()` и мгновенно завершает дочерний процесс.

## 3. Пошаговая инструкция: рабочий рендерер от начала до конца

Ниже мы соберём небольшой, но полноценный рендерер **`WaveParticleRenderer`**, который задействует **весь** функционал ядра:
1. **`CPUStage` (генератор)** — готовит стартовые данные частиц и общие униформы.
2. **`VertexStage` (Transform Feedback)** — обновляет позиции частиц на GPU и пишет их в буфер.
3. **`FragmentStage` (полноэкранный + `chunked=True` + `TargetSpec` HDR `f4`)** — рисует процедурный фон по тайлам.
4. **`FragmentStage` (геометрический + `GeometrySpec` + `RenderStateSpec`)** — рисует частицы поверх фона с аддитивным блендингом.
5. **`RepeatStage`** — делает 2 прохода ping-pong размытия (`blur_a` ↔ `blur_b`).
6. **`FragmentStage` (условный `condition`)** — опциональная виньетка (включается флагом в `payload`).
7. **`CPUStage` (финальный)** — вычитывает итоговый `RenderTarget` в `QImage`.
8. **Горячая мутация пайплайна, экспорт с прогрессом, многопоточность и дебаг-режим**.

### Шаг 1. Шейдеры

```python
import struct
import moderngl
from PyQt5.QtWidgets import QApplication
from pipeline_base import (
    BasePipelineRenderer,
    BufferInputSpec,
    CPUStage,
    FragmentStage,
    GeometrySpec,
    PipelineContext,
    RenderDebugConfig,
    RenderPipeline,
    RenderStateSpec,
    RepeatStage,
    TargetSpec,
    VertexStage,
)

# 1) VertexStage (Transform Feedback): смещаем частицы по синусоиде
PARTICLE_UPDATE_VERT = """#version 330
in vec2 in_pos;
uniform float u_time;
out vec2 out_pos;
void main() {
    float y = in_pos.y + 0.15 * sin(in_pos.x * 6.2831 + u_time);
    out_pos = vec2(in_pos.x, clamp(y, -0.95, 0.95));
}
"""

# 2) Полноэкранный фон в HDR (f4)
BG_FRAG = """#version 330
in vec2 v_uv;
uniform float u_time;
out vec4 fragColor;
void main() {
    float wave = 0.5 + 0.5 * sin(v_uv.x * 4.0 + u_time);
    fragColor = vec4(0.08, 0.15 + 0.25 * wave, 0.35, 1.0);
}
"""

# 3) Отрисовка частиц из буфера после Transform Feedback
PARTICLE_DRAW_VERT = """#version 330
in vec2 in_pos;
void main() {
    gl_Position = vec4(in_pos, 0.0, 1.0);
    gl_PointSize = 6.0;
}
"""

PARTICLE_DRAW_FRAG = """#version 330
out vec4 fragColor;
void main() {
    fragColor = vec4(1.0, 0.85, 0.3, 1.0);
}
"""

# 4) Простой 5-tap blur для RepeatStage (ping-pong)
BLUR_FRAG = """#version 330
in vec2 v_uv;
uniform sampler2D u_src;
uniform vec2 u_texel;
out vec4 fragColor;
void main() {
    vec2 uv = v_uv * 0.5 + 0.5;
    vec4 c = texture(u_src, uv) * 0.4;
    c += texture(u_src, uv + vec2( u_texel.x, 0.0)) * 0.15;
    c += texture(u_src, uv + vec2(-u_texel.x, 0.0)) * 0.15;
    c += texture(u_src, uv + vec2(0.0,  u_texel.y)) * 0.15;
    c += texture(u_src, uv + vec2(0.0, -u_texel.y)) * 0.15;
    fragColor = c;
}
"""

# 5) Условный пост-эффект (виньетка)
VIGNETTE_FRAG = """#version 330
in vec2 v_uv;
uniform sampler2D u_src;
out vec4 fragColor;
void main() {
    vec2 uv = v_uv * 0.5 + 0.5;
    float d = length(v_uv);
    float vig = smoothstep(1.35, 0.35, d);
    vec4 c = texture(u_src, uv);
    fragColor = vec4(c.rgb * vig, c.a);
}
"""
```

### Шаг 2. Реализация наследника `BasePipelineRenderer`

```python
class WaveParticleRenderer(BasePipelineRenderer):
    PARTICLE_COUNT = 64

    def prepare_frame_payload(self, payload: dict, is_export: bool = False) -> dict:
        """Потокобезопасный снимок входных данных (вызывается в GUI-потоке)."""
        return {
            "time": float(payload.get("time", 0.0)),
            "blur_iters": int(payload.get("blur_iters", 2)),
            "vignette": bool(payload.get("vignette", True)),
            "chunk_interval_ms": int(payload.get("chunk_interval_ms", 1)),
            "pipeline": str(payload.get("pipeline", "export" if is_export else "preview")),
        }

    def handle_initialize(self, pctx: PipelineContext, payload: dict) -> None:
        """Первичная инициализация размеров и стартового VBO частиц."""
        pctx.set_viewport_metrics(
            w=payload.get("w", 512),
            h=payload.get("h", 512),
            preview_scale=payload.get("preview_scale", 1.0),
            chunk_size=payload.get("chunk_size", 128),
        )
        # Создаём исходный буфер частиц (64 точки по горизонтали)
        raw = bytearray()
        for i in range(self.PARTICLE_COUNT):
            x = -0.9 + 1.8 * (i / max(1, self.PARTICLE_COUNT - 1))
            raw.extend(struct.pack("2f", x, 0.0))
        pctx.pool.ensure_buffer("particles_in", data=bytes(raw), dynamic=False)

    def before_export_start(self, pctx: PipelineContext, payload: dict) -> None:
        """Сохраняем размеры превью и переключаемся на разрешение экспорта."""
        pctx.custom_state["saved_metrics"] = (
            pctx.widget_w, pctx.widget_h, pctx.preview_scale, pctx.chunk_size
        )
        pctx.set_viewport_metrics(
            w=payload.get("export_w", 1024),
            h=payload.get("export_h", 1024),
            preview_scale=1.0,
            chunk_size=payload.get("export_chunk_size", 128),
        )

    def after_export_finish(self, pctx: PipelineContext) -> None:
        """Возвращаем метрики превью после завершения экспорта."""
        saved = pctx.custom_state.pop("saved_metrics", None)
        if saved is not None:
            pctx.set_viewport_metrics(*saved)

    def setup_pipelines(self, pctx: PipelineContext) -> dict[str, RenderPipeline]:
        return {
            "preview": self._build_pipeline("preview", chunked=False),
            "export": self._build_pipeline("export", chunked=True),
        }

    def _build_pipeline(self, name: str, chunked: bool) -> RenderPipeline:
        # 1. CPUStage: подготовка общих униформ (демонстрация генератора с yield)
        def prepare_uniforms_gen(pctx: PipelineContext):
            pctx.blackboard.uniforms["u_time"] = pctx.blackboard.payload["time"]
            yield  # отдаём квант управления
            pctx.blackboard.uniforms["u_texel"] = (
                1.0 / max(1, pctx.render_w),
                1.0 / max(1, pctx.render_h),
            )

        # 2. VertexStage: Transform Feedback -> буфер particles_out
        tf_stage = VertexStage(
            name="update_particles",
            vertex_shader=PARTICLE_UPDATE_VERT,
            varyings=("out_pos",),
            output_buffers="particles_out",
            output_size_bytes=self.PARTICLE_COUNT * 8,  # 2 float * 4 байта
            inputs=[BufferInputSpec("particles_in", "2f", "in_pos")],
            mode=moderngl.POINTS,
            vertices=self.PARTICLE_COUNT,
        )

        # 3. FragmentStage: полноэкранный HDR-фон (с чанкингом в режиме export)
        bg_stage = FragmentStage(
            name="draw_bg",
            fragment_shader=BG_FRAG,
            target_spec=TargetSpec(name="scene_hdr", dtype="f4"),
            chunked=chunked,
        )

        # 4. FragmentStage: отрисовка частиц поверх scene_hdr без очистки цвета
        particles_stage = FragmentStage(
            name="draw_particles",
            vertex_shader=PARTICLE_DRAW_VERT,
            fragment_shader=PARTICLE_DRAW_FRAG,
            target_spec=TargetSpec(name="scene_hdr", dtype="f4"),
            geometry_spec=GeometrySpec(
                inputs=[BufferInputSpec("particles_out", "2f", "in_pos")],
                primitive_mode=moderngl.POINTS,
                vertices=self.PARTICLE_COUNT,
            ),
            render_state=RenderStateSpec(
                clear_color=None,  # не стираем фон
                enable_flags=moderngl.BLEND,
                blend_func=(moderngl.SRC_ALPHA, moderngl.ONE),
            ),
        )

        # 5. RepeatStage: ping-pong размытие в цикле
        def bind_blur_src(pctx: PipelineContext, uacc):
            it = pctx.blackboard.loop_indices.get("blur_loop", 0)
            src_name = "scene_hdr" if it == 0 else ("blur_a" if it % 2 == 1 else "blur_b")
            tex = pctx.pool.resolve_texture(src_name)
            if tex is not None:
                tex.use(location=0)
                uacc.set_value("u_src", 0)
            uacc.set_value("u_texel", pctx.blackboard.uniforms["u_texel"])

        blur_pass_a = FragmentStage(
            name="blur_to_a",
            fragment_shader=BLUR_FRAG,
            target_spec=TargetSpec(name="blur_a", dtype="f4"),
            uniform_binder=bind_blur_src,
            condition=lambda c: c.blackboard.loop_indices.get("blur_loop", 0) % 2 == 0,
        )
        blur_pass_b = FragmentStage(
            name="blur_to_b",
            fragment_shader=BLUR_FRAG,
            target_spec=TargetSpec(name="blur_b", dtype="f4"),
            uniform_binder=bind_blur_src,
            condition=lambda c: c.blackboard.loop_indices.get("blur_loop", 0) % 2 == 1,
        )
        blur_loop = RepeatStage(
            name="blur_loop",
            stages=[blur_pass_a, blur_pass_b],
            iterations=lambda c: max(0, int(c.blackboard.payload.get("blur_iters", 2))),
        )

        # 6. Условная виньетка (f1-таргет final)
        def final_blur_source(pctx: PipelineContext) -> str:
            iters = int(pctx.blackboard.payload.get("blur_iters", 2))
            if iters <= 0:
                return "scene_hdr"
            return "blur_a" if iters % 2 == 1 else "blur_b"

        def bind_vignette(pctx: PipelineContext, uacc):
            tex = pctx.pool.resolve_texture(final_blur_source(pctx))
            if tex is not None:
                tex.use(location=0)
                uacc.set_value("u_src", 0)

        vignette_stage = FragmentStage(
            name="vignette",
            fragment_shader=VIGNETTE_FRAG,
            target_spec=TargetSpec(name="final", dtype="f1"),
            uniform_binder=bind_vignette,
            condition=lambda c: bool(c.blackboard.payload.get("vignette", True)),
        )

        # 7. Финальный CPUStage: чтение RenderTarget -> QImage
        def readback(pctx: PipelineContext):
            target = "final" if pctx.blackboard.payload.get("vignette", True) else final_blur_source(pctx)
            pctx.blackboard.output_image = pctx.pool.read_target_to_qimage(
                target,
                flush_gpu=pctx.in_worker_thread,
                highlight_nan_inf=pctx.debug.highlight_nan_inf,
            )

        return RenderPipeline(
            name,
            stages=[
                CPUStage("prepare_uniforms", prepare_uniforms_gen, estimated_steps_fn=lambda _: 2),
                tf_stage,
                bg_stage,
                particles_stage,
                blur_loop,
                vignette_stage,
                CPUStage("readback", readback),
            ],
        )
```

### Шаг 3. Запуск, горячая мутация, дебаг и экспорт

```python
app = QApplication([])

debug_cfg = RenderDebugConfig(
    enabled=True,
    check_gl_errors=True,
    gpu_timing=True,
    highlight_nan_inf=True,
)

renderer = WaveParticleRenderer(use_thread=True, debug_config=debug_cfg)

# Подписываемся на сигналы
renderer.init_done.connect(lambda ok: print("Init:", ok))
renderer.frame_ready.connect(lambda img: img.save("preview.png"))
renderer.debug_report_ready.connect(lambda rep: print("Report:", rep["total_ms"], "ms", rep["stages"]))
renderer.export_progress_changed.connect(lambda done, total: print(f"Export: {done}/{total}"))
renderer.export_done.connect(lambda img: img.save("export.png") if img else print("Export failed"))

# 1) Холодный прогрев шейдеров в отдельном процессе + авто-инициализация
# Путь к piplib/python подхватывается автоматически из init.py
renderer.warmup_started.connect(lambda: print("Компиляция шейдеров в фоне..."))
renderer.warmup_finished.connect(
    lambda ok, err: print("Прогрев завершён" if ok else f"Фолбэк после ошибки прогрева: {err}")
)
renderer.warmup_shaders(
    init_payload={"w": 512, "h": 512, "preview_scale": 1.0, "chunk_size": 128},
    auto_initialize=True,
)

# 2) Обычный кадр превью
renderer.start_frame({"time": 1.25, "blur_iters": 2, "vignette": True})

# 3) Горячая мутация пайплайна налету (исключения пробрасываются в GUI-поток)
renderer.set_stage_enabled("preview", "vignette", False)
try:
    renderer.remove_stage("preview", "non_existent_stage")  # вернёт None
    renderer.set_stage_enabled("preview", "missing", True)  # выбросит KeyError
except KeyError as e:
    print("Перехвачена ошибка мутации из воркера:", e)

# 4) Инспекция промежуточного HDR-таргета в дебаг-режиме
renderer.set_debug_mode(True, inspect_target="scene_hdr", highlight_nan_inf=True)
renderer.start_frame({"time": 2.0})

# 5) Чанковый экспорт высокого разрешения с прогрессом
renderer.set_debug_mode(True, inspect_target=None)
renderer.start_export({
    "time": 2.0,
    "blur_iters": 2,
    "vignette": True,
    "export_w": 1024,
    "export_h": 1024,
    "export_chunk_size": 128,
})

# 6) Корректное завершение (при необходимости можно снова вызвать initialize())
# renderer.release_all()
```

---

## 4. Практические рекомендации

1. **Изоляция данных в `prepare_frame_payload()`**: при `use_thread=True` никогда не передавайте в `payload` изменяемые объекты GUI или ссылки на `QObject` — делайте плоский снимок (числа, кортежи, `bytes`, `numpy`-копии).
2. **Инспекция Transform Feedback**: если `VertexStage` выдаёт неожиданные результаты, используйте `pctx.pool.inspect_buffer_struct("particles_out", "2f", count=8)` в следующем `CPUStage`.
3. **Размеры LRU-кеша**: при частой динамической генерации шейдеров можно увеличить лимит программ, передав `max_programs` при создании `ResourcePool` или переопределив `ensure_context`.
4. **Чанкинг (`chunked=True`)**: используйте для тяжёлых фрагментных шейдеров (например, SDF raymarching), чтобы `QTimer` мог обрабатывать события UI и обновлять прогресс-бар между тайлами.
