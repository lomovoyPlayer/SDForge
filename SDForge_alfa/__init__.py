"""
SDForge — плагин Krita для 3D-моделирования SDF-сцен.

Точка входа из меню Scripts: подключает piplib в sys.path и регистрирует
расширение (регистрация выполняется только здесь).
"""

import sys
from pathlib import Path

from .config import DEBUG_LOG_ENABLED   # глобальный флаг, управляется в config.py

# Локальный флаг: диагностика именно запуска (init).
# Включать только для отладки загрузки плагина.
INIT_DEBUG_ENABLED = False

_PLUGIN_DIR = Path(__file__).resolve().parent
_PIPLIB_DIR = _PLUGIN_DIR.parent / "piplib"
_PIPLIB_STR = str(_PIPLIB_DIR)


def _ensure_piplib():
    if _PIPLIB_DIR.exists() and _PIPLIB_STR not in sys.path:
        sys.path.insert(0, _PIPLIB_STR)


# ===== Обязательная часть запуска =====
_ensure_piplib()

# ===== Опциональная диагностика запуска =====
# _init_diagnostics импортируется только при включённых флагах,
# log_debug и вычисление его аргументов не выполняются вообще.
if DEBUG_LOG_ENABLED and INIT_DEBUG_ENABLED:
    from . import _init_diagnostics
    _init_diagnostics.run_all(_PLUGIN_DIR, _PIPLIB_DIR)

# ===== Регистрация расширения =====
from .main import SDForgeExtension
from krita import Krita

Krita.instance().addExtension(SDForgeExtension(Krita.instance()))