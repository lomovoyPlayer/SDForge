"""
SDForge: дефолты, выводимые из кода при импорте модуля.

Источники: SDFShaderManager.get_default_sdfs() (SDF-типы) и
RenderPreviewParams (материалы, демо-сцена, настройки рендера и
управления). Экземпляры RenderPreviewParams создаются здесь только для
чтения дефолтов (render_settings / control_settings).
"""

from .core.data_classes import (
    Material, GroupOperation,
    UNARY_OP_TYPE_NAMES, BINARY_OP_TYPE_NAMES, OP_TYPE_NAMES,
)

from .core.render.shader_builder import SDFShaderManager
from .ui.render_previewer import RenderPreviewParams

DEFAULT_SDF_TYPES = SDFShaderManager.get_default_sdfs()

DEFAULT_MATERIALS = RenderPreviewParams.get_default_materials()

DEFAULT_GROUP_OPERATION = GroupOperation(childs = RenderPreviewParams.get_default_operations()[1])

DEFAULT_RENDER_SETTINGS = RenderPreviewParams().render_settings

DEFAULT_CONTROL_SETTINGS = RenderPreviewParams().control_settings




