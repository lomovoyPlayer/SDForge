"""
SDForge: панель настроек рендера и камеры — вкладка правой колонки диалога.

Независимый блок: получает (params, controller). Все виджеты пишут прямо в
атрибуты RenderPreviewParams (имена ключей DEFAULT_TREE_SETTINGS совпадают
с ними) и эмитят params.changed — рендерер перерисовывается сам. В БД
настройки текущего дерева попадают при переключении дерева / закрытии
диалога (controller.close_current_tree).

Панель перечитывает params по tree_loaded (открыли другое дерево — форма
показывает его настройки).
"""

from PyQt5 import QtWidgets
from PyQt5.QtCore import Qt

from .forms import FieldSpec, Form
from ..ui_style import tune_layout

from ..scene_controller import SceneController
from ..render_previewer import RenderPreviewParams

from ... import localization

CAMERA_SPECS = [
    FieldSpec("vec3", localization.get_text("render_settings_panel.camera.fields_specs.camera_pos"), "camera_pos"),
    FieldSpec("angle_vec3", localization.get_text("render_settings_panel.camera.fields_specs.camera_rot"), "camera_rot", step=1.0, suffix=" °"),
    FieldSpec("dial", localization.get_text("render_settings_panel.camera.fields_specs.camera_fov_deg"), "camera_fov_deg", 
              minimum=10.0, maximum=120.0, step=0.5, 
              decimals=1, suffix=" °"),
]

LIGHT_SPECS = [
    FieldSpec("float", localization.get_text("render_settings_panel.light.fields_specs.light_power_scale"), "light_power_scale", minimum=0.1, maximum=50.0, step=0.5),
]

BACKGROUND_SPECS = [
    FieldSpec("bool", localization.get_text("render_settings_panel.background.fields_specs.show_floor"), "show_floor"),
    FieldSpec("bool", localization.get_text("render_settings_panel.background.fields_specs.transparent_background"), "transparent_background"),
    
]

AMBIENT_SPECS = [
    FieldSpec("color", localization.get_text("render_settings_panel.ambient.fields_specs.ambient_color"), "ambient_color", krita=True),
    FieldSpec("float", localization.get_text("render_settings_panel.ambient.fields_specs.ambient_intensity"), "ambient_intensity", minimum=0.0, maximum=10.0, step=0.05),
    FieldSpec("float", localization.get_text("render_settings_panel.ambient.fields_specs.sky_sun_azimuth_deg"), "sky_sun_azimuth_deg", minimum=0.0, maximum=360.0, step=0.5),
    FieldSpec("float", localization.get_text("render_settings_panel.ambient.fields_specs.sky_sun_elevation_deg"), "sky_sun_elevation_deg", minimum=-90.0, maximum=90.0, step=0.5),
    FieldSpec("float", localization.get_text("render_settings_panel.ambient.fields_specs.sky_sun_ang_radius"), "sky_sun_ang_radius", minimum=0.0, maximum=1.0, step=0.0005, decimals=4),
    FieldSpec("float", localization.get_text("render_settings_panel.ambient.fields_specs.sky_air_density"), "sky_air_density", minimum=0.0, maximum=2.0, step=0.01),
]

QUALITY_SPECS = [
    FieldSpec("int", localization.get_text("render_settings_panel.quality.fields_specs.max_reflect_count"), "max_reflect_count", minimum=1, maximum=32, step=1),
    FieldSpec("bool", localization.get_text("render_settings_panel.quality.fields_specs.phys_glass"), "phys_glass"),
    FieldSpec("int", localization.get_text("render_settings_panel.quality.fields_specs.area_ray_count"), "area_ray_count", minimum=0, maximum=10, step=1),
    FieldSpec("int", localization.get_text("render_settings_panel.quality.fields_specs.top_light_count"), "top_light_count", minimum=0, maximum=4, step=1),
    
]

class RenderSettingsPanel(QtWidgets.QWidget):
    def __init__(self, params:"RenderPreviewParams", controller: "SceneController", parent=None):
        super().__init__(parent)
        self.params = params
        self.controller = controller

        layout = QtWidgets.QVBoxLayout(self)
        layout.setAlignment(Qt.AlignTop)
        tune_layout(layout)


        # --- Камера --- #
        self.camera_form = Form(self, sync=True)
        self.camera_form.add_header(localization.get_text("render_settings_panel.camera.header"))
        self.camera_form.add_specs(CAMERA_SPECS, target=params, commit=params.emit_changed)
        layout.addWidget(self.camera_form)

        # --- Свет --- #
        self.light_form = Form(self)
        self.light_form.add_header(localization.get_text("render_settings_panel.light.header"))
        self.light_form.add_specs(LIGHT_SPECS, target=params, commit=params.emit_changed)
        layout.addWidget(self.light_form)

        # --- Фон --- #
        self.background_form = Form(self)
        self.background_form.add_header(localization.get_text("render_settings_panel.background.header"))
        self.background_form.add_specs(BACKGROUND_SPECS, target=params, commit=params.emit_changed)
        layout.addWidget(self.background_form)

        # --- Окружение --- #
        self.ambient_form = Form(self)
        self.ambient_form.add_header(localization.get_text("render_settings_panel.ambient.header"))
        self.ambient_form.add_specs(AMBIENT_SPECS, target=params, commit=params.emit_changed)
        layout.addWidget(self.ambient_form)

        # --- Качество --- #
        self.quality_form = Form(self)
        self.quality_form.add_header(localization.get_text("render_settings_panel.quality.header"))
        self.quality_form.add_specs(QUALITY_SPECS, target=params, commit=params.emit_changed)
        layout.addWidget(self.quality_form)

        self.rebuild()
        controller.tree_loaded.connect(lambda _id: self.rebuild())

    def rebuild(self):
        self.camera_form.refresh()
        self.light_form.refresh()
        self.background_form.refresh()
        self.ambient_form.refresh()
        self.quality_form.refresh()
