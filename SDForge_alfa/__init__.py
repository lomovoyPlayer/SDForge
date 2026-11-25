"""
SDForge — плагин Krita для 3D-моделирования SDF-сцен.

Точка входа из меню Scripts: подключает piplib в sys.path, настраивает
embedded Python для фонового прогрева шейдеров и регистрирует расширение.
"""

import os
import sys
from pathlib import Path

from .config import DEBUG_LOG_ENABLED   # глобальный флаг, управляется в config.py

# Локальный флаг: диагностика именно запуска (init).
# Включать только для отладки загрузки плагина.
INIT_DEBUG_ENABLED = False

_PLUGIN_DIR = Path(__file__).resolve().parent
PIPLIB_DIR = _PLUGIN_DIR.parent / "piplib"
_PIPLIB_DIR = PIPLIB_DIR
_PIPLIB_STR = str(PIPLIB_DIR)
WARMUP_PYTHON_EXE: Path | None = None


def _ensure_piplib() -> None:
    if PIPLIB_DIR.exists():
        os.environ.setdefault("SDFORGE_PIPLIB_DIR", _PIPLIB_STR)
        if _PIPLIB_STR not in sys.path:
            sys.path.insert(0, _PIPLIB_STR)


def _ensure_warmup_python() -> Path | None:
    """Находит embedded Python в piplib, гарантирует видимость piplib через ._pth и сохраняет путь в env."""
    rel_candidates = (
        Path("python") / "python.exe",
        Path("python.exe"),
        Path("python") / "bin" / "python3",
        Path("python") / "bin" / "python",
    )
    for rel in rel_candidates:
        exe = PIPLIB_DIR / rel
        if exe.is_file():
            for pth in exe.parent.glob("python*._pth"):
                try:
                    raw = pth.read_text(encoding="ascii", errors="ignore")
                    lines = [line.strip() for line in raw.splitlines()]
                    changed = False
                    if "#import site" in lines:
                        lines = ["import site" if l == "#import site" else l for l in lines]
                        changed = True
                    if ".." not in lines:
                        lines.append("..")
                        changed = True
                    if changed:
                        pth.write_text("\n".join(lines) + "\n", encoding="ascii")
                except OSError:
                    pass
            os.environ["SDFORGE_WARMUP_PYTHON"] = str(exe)
            return exe
    return None


# ===== Обязательная часть запуска =====
_ensure_piplib()
WARMUP_PYTHON_EXE = _ensure_warmup_python()

# ===== Опциональная диагностика запуска =====
# _init_diagnostics импортируется только при включённых флагах,
# log_debug и вычисление его аргументов не выполняются вообще.
if DEBUG_LOG_ENABLED and INIT_DEBUG_ENABLED:
    from . import _init_diagnostics
    _init_diagnostics.run_all(_PLUGIN_DIR, PIPLIB_DIR)

# ===== Регистрация расширения =====
from .main import SDForgeExtension
from krita import Krita

Krita.instance().addExtension(SDForgeExtension(Krita.instance()))
