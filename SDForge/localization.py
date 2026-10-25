"""
SDForge: система локализации текстов и сообщений для плагина.
"""

from PyQt5.QtCore import QLocale

locale: QLocale = QLocale()

language: str

if locale.language() == QLocale.Language.Russian:
    language = "ru"
elif locale.language() == QLocale.Language.English:
    language = "en"
else:
    language = "ru"

L_RU: dict[str, str] = {
#region db
"db.migrate.conn.in_transaction.error": "Миграцию нельзя начинать внутри открытой транзакции",
"db.migrate.version.error": "Версия БД {version} новее поддерживаемой {SCHEMA_VERSION}",
"db.migrate.migrating_schema.log_debug": "SDForge.db:база данных мигрируется по схеме v{target_version}",
#endregion

#region repository
"repository._loads.error.log_debug": "SDForge.repo: не удалось распарсить JSON, используется значение по умолчанию",
"repository._node_tree_id.error": "Узел {node_id} не найден",
"repository._library_order_for_insert.error": "Неправильная таблица-библиотека {table!r}",
"repository._require_not_builtin.error.unexpected_table": "Неправильная таблица-библиотека {table!r}",
"repository._require_not_builtin.error.row_not_found": "Таблица {table} строка {row_id} не найдена",
"repository._require_not_builtin.error.is_builtin": "Таблица {table} строка {row_id} является встроенным и только для чтения",
"repository._get_meta_column.error.unexpected_column": "Неправильное имя колонки {column} в таблице meta",
"repository._update_meta_column.error.unexpected_column": "Неправильное имя колонки {column} в таблице meta",
"repository.get_tree_settings.error.tree_not_found": "Дерево объектов {tree_id} не найдено",
"repository.update_tree_settings.error.tree_not_found": "Дерево объектов {tree_id} не найдено",
"repository.add_node.error.unknown_node_kind": "Неизвестная категория узла: {kind!r}",
"repository.add_node.error.missing_figure": "Фигура обязательна для категории \"{KIND_FIGURE}\"",
"repository.move_node.error.node_not_found": "Узел {node_id} не найден",
"repository.move_node.error.itself_move": "Нельзя переместить узел в самого себя",
"repository.move_node.error.parent_not_found": "Родитель {parent_id} не найден",
"repository.move_node.error.cross_tree_move": "Перемещение между деревьями не поддерживается",
"repository.move_node.error.itself_subtree_move": "Нельзя переместить узел в его собственное поддерево",
"repository.duplicate_node.error.node_not_found": "Узел {node_id} не найден",
"repository.duplicate_node.error.cyclic_tree": "SDForge.repository: орфаны или циклические узлы обнаружены в дереве {tree_id}",
"repository.figure_to_payload.material_not_found.log_debug": "SDForge.repository: id материала {mat_index} вне диапазона, сохранен как null",
"repository.figure_from_payload.material_not_found.log_debug": "SDForge.repo: id материала {material_db_id} не найден, используется материал 0",
"repository.duplicate_material.error.material_not_found": "Материал {material_id} не найден",

"repository._spec_for.attribute_missing.log_debug": "SDForge.repo: у {class_name}.{param_name} нет атрибута, пропущено",
"repository.duplicate_tree.error.tree_not_found": "Дерево объектов {tree_id} не найдено",
"repository.duplicate_tree.error.cyclic_tree": "SDForge.repository: орфаны или циклические узлы обнаружены в дереве {tree_id}",
"repository._texture_slot_to_payload.no_filename.log_debug": "SDForge.repo: у слота текстуры нет имени файла, пропущено",
"repository._slot_to_texture.file_not_found.log_debug": "SDForge.repo: файл текстуры {filename!r} не найден, слот отключён",
"repository.duplicate_node.name_suffix": " (копия)",
"repository.duplicate_material.name_suffix": " (копия)",
"repository.remove_sdf_type.error.not_found": "SDF-тип {type_id} не найден",
"repository.remove_sdf_type.error.is_builtin": "SDF-тип {type_id} является встроенным и не может быть удалён",
#endregion

#region renderer_mgl
"renderer_mgl.init.complete.log_debug": "SDForge.renderer: инициализация завершена",
"renderer_mgl.set_test_shader_mode.changed.log_debug": "SDForge.renderer: set_test_shader_mode enabled={enabled}",
"renderer_mgl._prepare_fragment_shader.using_test_shader.log_debug": "SDForge.renderer: вместо шейдера сцены используется ТЕСТОВЫЙ, orig_len={orig_len} test_len={test_len}",
"renderer_mgl._ensure_context.created.log_debug": "SDForge.renderer: контекст создан, version_code={version_code}",

"renderer_mgl.initialize_gl.begin.log_debug": "SDForge.renderer: initializeGL начат",
"renderer_mgl.initialize_gl.program_cache_hit.log_debug": "SDForge.renderer: initializeGL: программа шейдера найдена в кеше",
"renderer_mgl.initialize_gl.program_cache_miss.log_debug": "SDForge.renderer: initializeGL: программы нет в кеше, шейдер скомпилирован",
"renderer_mgl.initialize_gl.program_compiled.log_debug": "SDForge.renderer: программа шейдера собрана, членов: {members}",
"renderer_mgl.initialize_gl.done.log_debug": "SDForge.renderer: initializeGL завершён",

"renderer_mgl.resize_gl.requested.log_debug": "SDForge.renderer: resizeGL: виджет={src_w:.2f}x{src_h:.2f} рендер={render_w}x{render_h} previewScale={preview_scale}",
"renderer_mgl.resize_gl.done.log_debug": "SDForge.renderer: resizeGL завершён, сетка={grid_w}x{grid_h}",

"renderer_mgl._cache_uniform.missing.log_debug": "SDForge.renderer: униформа отсутствует при инициализации: {name}",
"renderer_mgl._cache_uniform.cached.log_debug": "SDForge.renderer: униформа закеширована: {name}",
"renderer_mgl._cache_uniform.cache_failed.error.log_debug": "SDForge.renderer: не удалось закешировать униформу {name}: {error}",
"renderer_mgl._set_cached_uniform.set_failed.error.log_debug": "SDForge.renderer: не удалось записать униформу {name} значением {value}: {error}",
"renderer_mgl._set_cached_int_array_values.whole_array_failed.error.log_debug": "SDForge.renderer: {name}: не удалось записать массив целиком: {error}",

"renderer_mgl._ensure_default_texture_array.created.log_debug": "SDForge.renderer: создан массив текстур по умолчанию 1x1x1",
"renderer_mgl._normalize_layer.cache_miss.log_debug": "SDForge.renderer: текстура {src_w}x{src_h} -> слой {tw}x{th}, контент={content_w}x{content_h} (нет в кеше)",
"renderer_mgl.set_texture_array.resized.log_debug": "SDForge.renderer: массив текстур пересоздаётся, было={old_size} стало={new_size}",
"renderer_mgl.set_texture_array.created.log_debug": "SDForge.renderer: массив текстур создан, размер={size}",
"renderer_mgl.set_texture_array.updated.log_debug": "SDForge.renderer: массив текстур обновлён, размер={size}",
"renderer_mgl.compress_nodes_params_to_texture.updated.log_debug": "SDForge.renderer: текстура TBP обновлена",

"renderer_mgl._pass_all_parameters.lights_limited.log_debug": "SDForge.renderer: количество источников света ограничено до {limit} из {count}",
"renderer_mgl.paint_gl.early_exit.log_debug": "SDForge.renderer: paintGL: ранний выход, нет fbo/program/vao",
"renderer_mgl._capture_current_image.fbo_missing.log_debug": "SDForge.renderer: _capture_current_image пропущен: fbo не создан",
"renderer_mgl.get_image.no_image.log_debug": "SDForge.renderer: get_image: кадра в кеше нет",
"renderer_mgl.get_image.returning.log_debug": "SDForge.renderer: get_image возвращает {width}x{height}",

"renderer_mgl.initialize.failed.error.log_debug": "SDForge.renderer: не удалось инициализировать рендерер: {error}",
"renderer_mgl.start_export.linear_uniform_missing.log_debug": "SDForge.renderer: ПРЕДУПРЕЖДЕНИЕ: u_outputLinear отсутствует — накопление пойдёт по тонмапнутым кадрам (смещённое среднее)",
"renderer_mgl.start_export.failed.error.log_debug": "SDForge.renderer: не удалось запустить экспорт: {error}",
"renderer_mgl._accum_frame.non_finite.log_debug": "SDForge.renderer: _accum_frame: {bad} нечисловых значений в кадре {index}, обнулены",

"renderer_mgl.release_all.object_failed.error.log_debug": "SDForge.renderer: release_all: не удалось освободить {name}: {error}",
"renderer_mgl.release_all.program_failed.error.log_debug": "SDForge.renderer: release_all: не удалось освободить программу: {error}",
"renderer_mgl.release_all.done.log_debug": "SDForge.renderer: release_all завершён",

"renderer_mgl._dbg_state.dump.log_debug": "SDForge.renderer: {tag} | {payload}",
"renderer_mgl._dbg_uniform_presence.dump.log_debug": "SDForge.renderer: {tag} | {data}",
"renderer_mgl._dbg_scene_summary.materials.log_debug": "SDForge.renderer: сводка сцены, материалы={rows}",
"renderer_mgl._dbg_scene_summary.operations.log_debug": "SDForge.renderer: сводка сцены, операции={rows}",
"renderer_mgl._dbg_scene_summary.lights.log_debug": "SDForge.renderer: сводка сцены, источники света={lights}",
#endregion

#region shader_builder
"shader_builder.sdf_shader_manager.get_sdf_index.error.unknown_figure": "Неизвестная SDF-фигура '{name}'. Доступные: {available}",
"shader_builder.sdf_shader_manager._validate_sdf_dict.error.invalid_name": "Ключи словаря SDF должны быть непустыми названиями (str), получено: {name!r}",
"shader_builder.sdf_shader_manager._validate_sdf_dict.error.invalid_code": "Код фигуры '{name}' должен быть GLSL-строкой, содержащей return.",

"shader_builder._build_shader.fast_placeholder_missing.log_debug": "SDForge.shader: ПРЕДУПРЕЖДЕНИЕ: метка быстрого рендера '{placeholder}' не найдена в шейдере, быстрая ветка останется пустой!",
#endregion

#region data_classes
"data_classes.operation.error.operands_count": "Операндов операции должно быть 4",
"data_classes.tree_object.error.figure_must_be_none": "unary_op.figure должно быть None для TreeObject",
"data_classes.tree_object.error.base_object_required": "base_object или base_object.figure не должны быть None",
"data_classes.tree_object.error.binary_op_type_missmatch": "base_object.op_type должен быть бинарным типом операции",
"data_classes.tree_object.error.unary_op_type_missmatch": "unary_op.op_type должен быть унарным типом операции",
"data_classes.group_operation.error.binary_op_type_missmatch": "Неправильный тип бинарной операции: {op_type}",
"data_classes.group_operation.init.error.operands_count_missmatch": "Операндов операции должно быть 4",
"data_classes.group_operation.error.unary_op_type_missmatch": "Неправильный тип унарной операции: {unary_op_type}",
"data_classes.group_operation.allocate_register.error.no_free_registers": "Нет свободных регистров",
"data_classes.group_operation.free_register.error.no_used_registers": "Нет использованных регистров для освобождения",
"data_classes.group_operation.to_operation_sequence.error.registers_count_missmatch": "registers_count должно быть >= 2",

#endregion

#region math_utils
"math_utils.figures_center.error.weights_count_missmatch": "Веса должны соответствовать количеству позиций",
#endregion

#region forms
"forms.add_spec.error.unknown_kind": "Неизвестный kind: {kind!r}",

#endregion

#region frame_export_panel
"frame_export_panel.frame_export_resolution_scale": "Масштаб разрешения:",
"frame_export_panel.frame_export_use_viewport_base": "База = размер вьюпорта",
"frame_export_panel.frame_export_base_width": "Базовая ширина:",
"frame_export_panel.frame_export_base_height": "Базовая высота:",
"frame_export_panel.frame_export_show_floor": "Показывать пол",
"frame_export_panel.frame_export_transparent_background": "Прозрачный фон",
"frame_export_panel.frame_export_max_reflect_count": "Отражения (max):",
"frame_export_panel.frame_export_phys_glass": "Физическое стекло",
"frame_export_panel.frame_export_dispersion": "Дисперсия( >0 кадры семплирования округляются до кратных 3):",
"frame_export_panel.frame_export_area_ray_count": "Дополнительные лучи к источникам света(<3 считается как 0)",
"frame_export_panel.frame_export_top_light_count": "Top-k выборка источников для доп лучей",
"frame_export_panel.frame_export_accum_frames": "Кадры накопления (сглаживание, 1 = выкл):",

"frame_export_panel.frame_export_header": "Рендер кадра",
"frame_export_panel.export_to_layer": "Экспорт в слой",
"frame_export_panel.export_to_file.button": "Экспорт в файл",
"frame_export_panel.export_to_file.dialog.caption": "Сохранить кадр",
"frame_export_panel.frame_export_resolution_size": "Итоговый размер: {w} × {h} px",
"frame_export_panel.layer_name": "SDF кадр {width}x{height}",
"frame_export_panel.has_no_open_document": "Нет открытого документа — экспортировать некуда",
"frame_export_panel.export_progress_sampling": "Кадр {cur_frame}/{total_frames} — {pct}% — {elapsed}",
"frame_export_panel.export_progress_default": "{pct}% — {elapsed}",
"frame_export_panel.warning.renderer_not_initialized": "Рендерер ещё не инициализирован, попробуй позже.",
"frame_export_panel.warning.render_no_image": "Рендер не вернул изображение.",
"frame_export_panel.warning.save_failed": "Не удалось сохранить файл:\n{path}",
"frame_export_panel.warning.no_active_document": "Документ был закрыт во время рендера.",
"frame_export_panel.warning.document_not_rgba-8-bit": "Поддерживается только RGBA 8-bit, у документа {colorModel}/{colorDepth}.",
"frame_export_panel.render_done": "Готово",

#endregion

#region inspector panel
"inspector_panel.header": "Инспектор",
"inspector_panel.no_selection": "Ничего не выбрано",
"inspector_panel.node_name": "Имя:",
"inspector_panel.build_group_fields.operation": "Операция:",
"inspector_panel.build_group_fields.radius_or_coeff": "Параметр (k/r):",
"inspector_panel.build_group_fields.modifier": "Модификатор:",
"inspector_panel.build_group_fields.unary_k": "Параметр модификатора (r):",
"inspector_panel.build_figure_fields.no_figure": "Пустая операция",
"inspector_panel.build_figure_fields.type": "Тип фигуры:",
"inspector_panel.build_figure_fields.operation": "Наложение:",
"inspector_panel.build_figure_fields.radius_or_coeff": "Параметр наложения (k/r):",
"inspector_panel.build_figure_fields.unary_op": "Модификатор:",
"inspector_panel.build_figure_fields.unary_k": "Параметр модификатора (r):",
"inspector_panel.build_figure_fields.material": "Материал:",
"inspector_panel.build_figure_fields.albedo": "Альбедо:",
"inspector_panel.build_figure_fields.transformations": "Трансформации",
"inspector_panel.build_figure_fields.position": "Позиция:",
"inspector_panel.build_figure_fields.rotation": "Вращение:",
"inspector_panel.build_figure_fields.elongation": "Элонгация:",
"inspector_panel.build_figure_fields.scale": "Масштаб:",
"inspector_panel.build_figure_fields.textures": "Текстуры",

#endregion

#region material panel
"material_panel.fields.name": "Имя:",
"material_panel.fields_specs.roughness": "Матовость:",
"material_panel.fields_specs.metallic": "Металличность:",
"material_panel.fields_specs.transmission": "Прозрачность:",
"material_panel.fields_specs.ior": "IOR:",
"material_panel.fields_specs.subsurface": "SSS (глубина):",
"material_panel.fields_specs.sss_color": "SSS (цвет):",
"material_panel.fields_specs.emission": "Излучение:",

"material_panel.buttons.add": "Добавить",
"material_panel.buttons.duplicate": "Дублировать",
"material_panel.buttons.delete": "Удалить",

"material_panel.commit.warning.is_builtin.title": "Редактирование материала",
"material_panel.commit.warning.is_builtin.text": "Вы пытаетесь редактировать встроенный материал. \nВстроенные материалы нельзя изменять напрямую, но вы можете создать их копию и редактировать её.",

"material_panel.on_delete.warning.is_builtin.title": "Удалить материал",
"material_panel.on_delete.warning.is_builtin.text": "Системный предустановленный материал нельзя удалить. \nВы можете удалить только пользовательские материалы.",

"material_panel.on_delete.warning.in_use.title": "Удалить материал?",
"material_panel.on_delete.warning.in_use.text": "Материал используется фигурами — они получат материал 0. \nУдалить?",

#endregion

#region projects panel
"projects_panel.label": "Проекты:",
"projects_panel.buttons.new": "Новый проект",
"projects_panel.buttons.duplicate": "Дублировать",
"projects_panel.buttons.rename": "Переименовать",
"projects_panel.buttons.delete": "Удалить",

"projects_panel.on_new.title": "Новый проект",
"projects_panel.on_new.text": "Имя:",
"projects_panel.on_new.warning.existing_name.text": "Проект с таким именем уже существует.",

"projects_panel.on_duplicate.title": "Дублировать проект",
"projects_panel.on_duplicate.text": "Имя копии:",

"projects_panel.on_rename.title": "Переименовать проект",
"projects_panel.on_rename.text": "Новое имя:",

"projects_panel.on_delete.title": "Удалить проект",
"projects_panel.on_delete.text": "Удалить проект {name} вместе со всеми узлами?",

#endregion

#region render settings panel
"render_settings_panel.camera.header": "Камера",
"render_settings_panel.camera.fields_specs.camera_pos": "Позиция:",
"render_settings_panel.camera.fields_specs.camera_rot": "Вращение:",
"render_settings_panel.camera.fields_specs.camera_fov_deg": "FOV (град):",

"render_settings_panel.light.header": "Свет",
"render_settings_panel.light.fields_specs.light_power_scale": "Множитель мощности света:",

"render_settings_panel.background.header": "Фон",
"render_settings_panel.background.fields_specs.show_floor": "Показывать пол",
"render_settings_panel.background.fields_specs.transparent_background": "Прозрачный фон",

"render_settings_panel.ambient.header": "Окружение",
"render_settings_panel.ambient.fields_specs.ambient_color": "Цвет Окружения:",
"render_settings_panel.ambient.fields_specs.ambient_intensity": "Интенсивность фонового освещения:",
"render_settings_panel.ambient.fields_specs.sky_sun_azimuth_deg": "Азимут небесного светила(град):",
"render_settings_panel.ambient.fields_specs.sky_sun_elevation_deg": "Высота небесного светила(град):",
"render_settings_panel.ambient.fields_specs.sky_sun_ang_radius": "Угловой радиус небесного светила:",
"render_settings_panel.ambient.fields_specs.sky_air_density": "Плотность воздушной среды окружения:",

"render_settings_panel.quality.header": "Качество",
"render_settings_panel.quality.fields_specs.max_reflect_count": "Отражения (max):",
"render_settings_panel.quality.fields_specs.phys_glass": "Физическое стекло",
"render_settings_panel.quality.fields_specs.area_ray_count": "Дополнительные лучи к источникам света(<3 считается как 0):",
"render_settings_panel.quality.fields_specs.top_light_count": "Top-k выборка источников для доп лучей:",

#endregion

#region scene tree panel
"scene_tree_widget.drop_error": "Ошибка перемещения",

"scene_tree_panel.label": "Дерево объектов:",
"scene_tree_panel.buttons.add": "+ Фигура",
"scene_tree_panel.buttons.add_group": "+ Группа",
"scene_tree_panel.buttons.duplicate": "Дублировать",
"scene_tree_panel.buttons.delete": "Удалить",

#endregion

#region texture_picker_panel
"texture_picker_panel.krita_resources.resources_failed.error.log_debug": "SDForge.picker: не удалось получить ресурсы Krita ({res_type!r}): {error}",
"texture_picker_panel._db_type_map.type_not_found.log_debug": "SDForge.picker: тип {api!r} не найден в кэше Krita",
"texture_picker_panel._tags_from_cache.cache_open_failed.error.log_debug": "SDForge.picker: кэш Krita не открывается: {error}",
"texture_picker_panel._tags_from_cache.query_failed.error.log_debug": "SDForge.picker: не удалось выполнить запрос тегов Krita: {error}",
"texture_picker_panel.krita_all_tags.cache_open_failed.error.log_debug": "SDForge.picker: кэш Krita не открывается: {error}",
"texture_picker_panel.krita_all_tags.query_failed.error.log_debug": "SDForge.picker: не удалось выполнить запрос списка тегов: {error}",
"texture_picker_panel.load_texture_raw.krita_unavailable.log_debug": "SDForge.picker: Krita API недоступен",
"texture_picker_panel.load_texture_raw.resource_not_found.log_debug": "SDForge.picker: ресурс не найден: {uri!r}",
"texture_picker_panel.load_texture_raw.load_failed.error.log_debug": "SDForge.picker: не удалось загрузить ресурс Krita {uri!r}: {error}",
"texture_picker_panel._make_thumb.load_failed.error.log_debug": "SDForge.picker: не удалось загрузить миниатюру {filename!r}: {error}",
"texture_picker_panel._probe_size.failed.error.log_debug": "SDForge.picker: не удалось определить размер текстуры {filename!r}: {error}",
"texture_picker_panel._payload_for.size_probe_failed.log_debug": "SDForge.picker: не удалось определить размер текстуры {filename!r}, используется размер полного слоя",
"texture_picker_panel._accept_entry.register_failed.error.log_debug": "SDForge.picker: не удалось зарегистрировать текстуру {filename!r}: {error}",

"texture_picker_panel.slot_titles.albedo": "Альбедо",
"texture_picker_panel.slot_titles.normal": "Нормали",
"texture_picker_panel.slot_titles.height": "Высота",

"texture_picker_panel.init.window_title": "Текстуры — {slot_title}",
"texture_picker_panel.init.search.placeholder": "Поиск по имени…",

"texture_picker_panel.init.buttons.clear": "Очистить слот",
"texture_picker_panel.init.buttons.choose": "Выбрать",

"texture_picker_panel.tag_combo.all_tags": "Все теги",
"texture_picker_panel.tag_combo.untagged": "Без тегов",
"texture_picker_panel._refill.tooltip.tags": "\nТеги: {tags}",

"texture_picker_panel.fields_specs.offset": "Смещение:",
"texture_picker_panel.fields_specs.rotation": "Поворот:",
"texture_picker_panel.fields_specs.tiling_scale": "Тайлинг:",
"texture_picker_panel.fields_specs.depth_scale": "Глубина:",

"texture_picker_panel._build_slot_row.buttons.pick.tooltip": "Выбрать текстуру",
"texture_picker_panel._build_slot_row.buttons.clear.tooltip": "Очистить слот",
"texture_picker_panel._build_slot_row.offset.tooltip": "Смещение в долях периода тайла (1.0 = полный период)",

#endregion

#region main_dialog
"main_dialog.init.sync_builtin_sdf_types.error.log_debug": "SDForge.dialog: не удалось синхронизировать встроенные SDF-типы: {error}",
"main_dialog.init.sync_builtin.error.log_debug": "SDForge.dialog: не удалось синхронизировать встроенные данные: {error}",

"main_dialog.init.tabs.scene_tree": "Древо Сцены",
"main_dialog.init.tabs.projects": "Проекты",
"main_dialog.init.tabs.inspector": "Инспектор",
"main_dialog.init.tabs.render_settings": "Рендер",
"main_dialog.init.tabs.materials": "Материалы",
"main_dialog.init.tabs.frame_export": "Рендер кадра",

"main_dialog.init.buttons.undo": "↶ Отменить",
"main_dialog.init.buttons.undo.tooltip": "Отменить изменение сцены (Ctrl+Z)",
"main_dialog.init.buttons.redo": "↷ Повторить",
"main_dialog.init.buttons.redo.tooltip": "Повторить изменение сцены (Ctrl+Shift+Z / Ctrl+Y)",

"main_dialog._open_initial_tree.default_tree_name": "Без названия",
#endregion

#region render_previewer
"render_previewer.scene_history.init.error.max_states": "max_states должно быть >= 2 или None",
"render_previewer.scene_history.activate.error.frozen": "Нельзя переключать проект во время составной правки",

"render_previewer.init.complete.log_debug": "SDForge.previewer: инициализация завершена",
"render_previewer._load_fast_render_code.load_failed.error.log_debug": "SDForge.previewer: не удалось загрузить код быстрого рендера: {error}",
"render_previewer._on_renderer_ready.init_failed.error.log_debug": "SDForge.previewer: не удалось инициализировать рендерер",
"render_previewer._suspend_background_work.suspend.log_debug": "SDForge.previewer: фоновая работа приостановлена: {reason}",

"render_previewer._paint_loading_splash.shader_failed.title": "Шейдер не скомпилировался",
"render_previewer._paint_loading_splash.shader_failed.hint": "Исправьте шейдер — превью пересоберётся само",
"render_previewer._paint_loading_splash.loading": "Загрузка шейдеров{dots}",
#endregion

#region scene_controller
"scene_controller.open_tree.no_root.log_debug": "SDForge.ctrl: у дерева {tree_id} нет корня, создаётся новый",
"scene_controller._rebuild_texture_images.loader_failed.error.log_debug": "SDForge.ctrl: texture_loader не смог загрузить {filename!r}: {error}",
"scene_controller._rebuild_texture_images.texture_unresolved.log_debug": "SDForge.ctrl: текстура {filename!r} не загружена, пропускается",
"scene_controller.commit_node.no_db_id.log_debug": "SDForge.ctrl: commit_node вызван для узла без db_id",
#endregion

#region init_diagnostics
"init_diagnostics.log_header.begin.log_debug": "SDForge.init: ===== начало инициализации =====",
"init_diagnostics.log_header.plugin_dir.log_debug": "SDForge.init: plugin_dir={plugin_dir}",
"init_diagnostics.log_header.piplib_dir.log_debug": "SDForge.init: piplib_dir={piplib_dir}",
"init_diagnostics.log_header.cwd.log_debug": "SDForge.init: текущая директория={cwd}",
"init_diagnostics.log_header.executable.log_debug": "SDForge.init: исполняемый файл={executable}",
"init_diagnostics.log_header.python_version.log_debug": "SDForge.init: версия Python={python_version}",
"init_diagnostics.log_header.platform.log_debug": "SDForge.init: платформа={platform}",
"init_diagnostics.log_header.architecture.log_debug": "SDForge.init: архитектура={architecture}",
"init_diagnostics.log_header.maxsize.log_debug": "SDForge.init: maxsize={maxsize}",

"init_diagnostics.log_sys_path.entry.log_debug": "SDForge.init: sys.path[{index}]={path}",

"init_diagnostics.log_piplib_contents.dir_missing.log_debug": "SDForge.init: директория piplib не существует",
"init_diagnostics.log_piplib_contents.entries_count.log_debug": "SDForge.init: элементов в piplib: {count}",
"init_diagnostics.log_piplib_contents.entry_dir.log_debug": "SDForge.init: piplib папка: {name}",
"init_diagnostics.log_piplib_contents.entry_file.log_debug": "SDForge.init: piplib файл: {name}",
"init_diagnostics.log_piplib_contents.list_failed.error.log_debug": "SDForge.init: не удалось получить содержимое piplib: {error}",
"init_diagnostics.log_piplib_contents.path_exists.log_debug": "SDForge.init: существует {name} -> {exists}, путь={path}",

"init_diagnostics.log_imports.begin.log_debug": "SDForge.init: начало диагностики импортов",
"init_diagnostics.log_imports.moderngl_from.log_debug": "SDForge.init: moderngl импортирован из: {file}",
"init_diagnostics.log_imports.moderngl_version.log_debug": "SDForge.init: версия moderngl: {version}",
"init_diagnostics.log_imports.moderngl_has_create_context.log_debug": "SDForge.init: у moderngl есть create_context={has_create_context}",
"init_diagnostics.log_imports.moderngl_import_failed.error.log_debug": "SDForge.init: не удалось импортировать moderngl: {error}",
"init_diagnostics.log_imports.private_moderngl_from.log_debug": "SDForge.init: _moderngl импортирован из: {file}",
"init_diagnostics.log_imports.private_moderngl_import_failed.error.log_debug": "SDForge.init: не удалось импортировать _moderngl: {error}",
"init_diagnostics.log_imports.glcontext_from.log_debug": "SDForge.init: glcontext импортирован из: {file}",
"init_diagnostics.log_imports.glcontext_dir_sample.log_debug": "SDForge.init: первые элементы dir(glcontext)={sample}",
"init_diagnostics.log_imports.glcontext_import_failed.error.log_debug": "SDForge.init: не удалось импортировать glcontext: {error}",
"init_diagnostics.log_imports.create_context_try.log_debug": "SDForge.init: попытка moderngl.create_context(standalone=True)",
"init_diagnostics.log_imports.create_context_done.log_debug": "SDForge.init: автономный контекст создан, version_code={version_code}",
"init_diagnostics.log_imports.create_context_info.log_debug": "SDForge.init: ctx.info={info}",
"init_diagnostics.log_imports.create_context_released.log_debug": "SDForge.init: контекст освобождён",
"init_diagnostics.log_imports.create_context_release_failed.error.log_debug": "SDForge.init: не удалось освободить контекст: {error}",
"init_diagnostics.log_imports.create_context_failed.error.log_debug": "SDForge.init: не удалось создать автономный контекст: {error}",
"init_diagnostics.log_imports.end.log_debug": "SDForge.init: конец диагностики импортов",

#endregion

#region main
"main.init.log_debug": "SDForge успешно запущен",

#endregion

}

L_EN: dict[str, str] = {
#region db
"db.migrate.conn.in_transaction.error": "Migration cannot be started inside an open transaction",
"db.migrate.version.error": "Database version {version} is newer than supported {SCHEMA_VERSION}",
"db.migrate.migrating_schema.log_debug": "SDForge.db: database schema is migrating to v{target_version}",
#endregion

#region repository
"repository._loads.error.log_debug": "SDForge.repo: failed to parse JSON payload, using default",
"repository._node_tree_id.error": "Node {node_id} not found",
"repository._library_order_for_insert.error": "Unexpected library-table {table!r}",
"repository._require_not_builtin.error.unexpected_table": "Unexpected library-table {table!r}",
"repository._require_not_builtin.error.row_not_found": "Table {table} row {row_id} not found",
"repository._require_not_builtin.error.is_builtin": "Table {table} row {row_id} is builtin and read-only",
"repository._get_meta_column.error.unexpected_column": "Unexpected column {column} in table meta",
"repository._update_meta_column.error.unexpected_column": "Unexpected column {column} in table meta",
"repository.get_tree_settings.error.tree_not_found": "Object tree {tree_id} not found",
"repository.update_tree_settings.error.tree_not_found": "Object tree {tree_id} not found",
"repository.add_node.error.unknown_node_kind": "Unknown node kind: {kind!r}",
"repository.add_node.error.missing_figure": "Figure is required for kind = \"{KIND_FIGURE}\"",
"repository.move_node.error.node_not_found": "Node {node_id} not found",
"repository.move_node.error.itself_move": "Cannot move node into itself",
"repository.move_node.error.parent_not_found": "Parent {parent_id} not found",
"repository.move_node.error.cross_tree_move": "Cross-tree moves are not supported",
"repository.move_node.error.itself_subtree_move": "Cannot move node into its own subtree",
"repository.duplicate_node.error.node_not_found": "Node {node_id} not found",
"repository.duplicate_node.error.cyclic_tree": "SDForge.repository: orphan or cyclic nodes detected in tree {tree_id}",
"repository.figure_to_payload.material_not_found.log_debug": "SDForge.repository: material id {mat_index} out of range, stored as null",
"repository.figure_from_payload.material_not_found.log_debug": "SDForge.repo: material id {material_db_id} not found, falling back to material 0",
"repository.duplicate_material.error.material_not_found": "Material {material_id} not found",

"repository._spec_for.attribute_missing.log_debug": "SDForge.repo: {class_name}.{param_name} has no attribute, skipped",
"repository.duplicate_tree.error.tree_not_found": "Object tree {tree_id} not found",
"repository.duplicate_tree.error.cyclic_tree": "SDForge.repository: orphan or cyclic nodes detected in tree {tree_id}",
"repository._texture_slot_to_payload.no_filename.log_debug": "SDForge.repo: texture slot without filename, skipped",
"repository._slot_to_texture.file_not_found.log_debug": "SDForge.repo: texture file {filename!r} not found, slot disabled",
"repository.duplicate_node.name_suffix": " (copy)",
"repository.duplicate_material.name_suffix": " (copy)",
"repository.remove_sdf_type.error.not_found": "SDF type {type_id} not found",
"repository.remove_sdf_type.error.is_builtin": "SDF type {type_id} is built-in and cannot be deleted",
#endregion

#region renderer_mgl
"renderer_mgl.init.complete.log_debug": "SDForge.renderer: init complete",
"renderer_mgl.set_test_shader_mode.changed.log_debug": "SDForge.renderer: set_test_shader_mode enabled={enabled}",
"renderer_mgl._prepare_fragment_shader.using_test_shader.log_debug": "SDForge.renderer: using TEST fragment shader instead of scene shader, orig_len={orig_len} test_len={test_len}",
"renderer_mgl._ensure_context.created.log_debug": "SDForge.renderer: context created, version_code={version_code}",

"renderer_mgl.initialize_gl.begin.log_debug": "SDForge.renderer: initializeGL begin",
"renderer_mgl.initialize_gl.program_cache_hit.log_debug": "SDForge.renderer: initializeGL: shader program cache HIT",
"renderer_mgl.initialize_gl.program_cache_miss.log_debug": "SDForge.renderer: initializeGL: shader program cache MISS, compiled",
"renderer_mgl.initialize_gl.program_compiled.log_debug": "SDForge.renderer: shader program compiled, members={members}",
"renderer_mgl.initialize_gl.done.log_debug": "SDForge.renderer: initializeGL done",

"renderer_mgl.resize_gl.requested.log_debug": "SDForge.renderer: resizeGL: widget={src_w:.2f}x{src_h:.2f} render={render_w}x{render_h} previewScale={preview_scale}",
"renderer_mgl.resize_gl.done.log_debug": "SDForge.renderer: resizeGL done, grid={grid_w}x{grid_h}",

"renderer_mgl._cache_uniform.missing.log_debug": "SDForge.renderer: uniform missing at init: {name}",
"renderer_mgl._cache_uniform.cached.log_debug": "SDForge.renderer: uniform cached: {name}",
"renderer_mgl._cache_uniform.cache_failed.error.log_debug": "SDForge.renderer: failed to cache uniform {name}: {error}",
"renderer_mgl._set_cached_uniform.set_failed.error.log_debug": "SDForge.renderer: failed to set uniform {name} to {value}: {error}",
"renderer_mgl._set_cached_int_array_values.whole_array_failed.error.log_debug": "SDForge.renderer: {name}: whole-array set failed: {error}",

"renderer_mgl._ensure_default_texture_array.created.log_debug": "SDForge.renderer: default texture array created 1x1x1",
"renderer_mgl._normalize_layer.cache_miss.log_debug": "SDForge.renderer: normalize texture {src_w}x{src_h} -> layer {tw}x{th}, content={content_w}x{content_h} (cache MISS)",
"renderer_mgl.set_texture_array.resized.log_debug": "SDForge.renderer: texture array resize, old={old_size} new={new_size}",
"renderer_mgl.set_texture_array.created.log_debug": "SDForge.renderer: texture array created, size={size}",
"renderer_mgl.set_texture_array.updated.log_debug": "SDForge.renderer: texture array updated, size={size}",
"renderer_mgl.compress_nodes_params_to_texture.updated.log_debug": "SDForge.renderer: TBP texture updated",

"renderer_mgl._pass_all_parameters.lights_limited.log_debug": "SDForge.renderer: lights count limited to {limit} from {count} original lights",
"renderer_mgl.paint_gl.early_exit.log_debug": "SDForge.renderer: paintGL early exit: missing fbo/program/vao",
"renderer_mgl._capture_current_image.fbo_missing.log_debug": "SDForge.renderer: _capture_current_image skipped: fbo is None",
"renderer_mgl.get_image.no_image.log_debug": "SDForge.renderer: get_image: cached image is None",
"renderer_mgl.get_image.returning.log_debug": "SDForge.renderer: get_image returning {width}x{height}",

"renderer_mgl.initialize.failed.error.log_debug": "SDForge.renderer: initialize failed: {error}",
"renderer_mgl.start_export.linear_uniform_missing.log_debug": "SDForge.renderer: WARNING: u_outputLinear missing — accumulation will use tone-mapped frames (biased average)",
"renderer_mgl.start_export.failed.error.log_debug": "SDForge.renderer: start_export failed: {error}",
"renderer_mgl._accum_frame.non_finite.log_debug": "SDForge.renderer: _accum_frame: {bad} non-finite values in frame {index}, zeroed",

"renderer_mgl.release_all.object_failed.error.log_debug": "SDForge.renderer: release_all: {name} release failed: {error}",
"renderer_mgl.release_all.program_failed.error.log_debug": "SDForge.renderer: release_all: program release failed: {error}",
"renderer_mgl.release_all.done.log_debug": "SDForge.renderer: release_all done",

"renderer_mgl._dbg_state.dump.log_debug": "SDForge.renderer: {tag} | {payload}",
"renderer_mgl._dbg_uniform_presence.dump.log_debug": "SDForge.renderer: {tag} | {data}",
"renderer_mgl._dbg_scene_summary.materials.log_debug": "SDForge.renderer: scene summary, materials={rows}",
"renderer_mgl._dbg_scene_summary.operations.log_debug": "SDForge.renderer: scene summary, operations={rows}",
"renderer_mgl._dbg_scene_summary.lights.log_debug": "SDForge.renderer: scene summary, lights={lights}",
#endregion

#region shader_builder
"shader_builder.sdf_shader_manager.get_sdf_index.error.unknown_figure": "Unknown SDF figure '{name}'. Available: {available}",
"shader_builder.sdf_shader_manager._validate_sdf_dict.error.invalid_name": "SDF dictionary keys must be non-empty names (str), got: {name!r}",
"shader_builder.sdf_shader_manager._validate_sdf_dict.error.invalid_code": "Code of figure '{name}' must be a GLSL string containing return.",

"shader_builder._build_shader.fast_placeholder_missing.log_debug": "SDForge.shader: WARNING: fast render placeholder '{placeholder}' not found in shader, fast branch will stay empty!",
#endregion

#region data_classes
"data_classes.operation.error.operands_count": "Operation operands must have be 4",
"data_classes.tree_object.error.figure_must_be_none": "unary_op.figure must be None for TreeObject",
"data_classes.tree_object.error.base_object_required": "base_object or base_object.figure must not be None",
"data_classes.tree_object.error.binary_op_type_missmatch": "base_object.op_type must be a binary operation type",
"data_classes.tree_object.error.unary_op_type_missmatch": "unary_op.op_type must be a unary operation type",
"data_classes.group_operation.error.binary_op_type_missmatch": "Unsupported binary operation type: {op_type}",
"data_classes.group_operation.init.error.operands_count_missmatch": "Operation operands must have be 4",
"data_classes.group_operation.error.unary_op_type_missmatch": "Unsupported unary operation type: {unary_op_type}",
"data_classes.group_operation.allocate_register.error.no_free_registers": "No free registers available",
"data_classes.group_operation.free_register.error.no_used_registers": "No used registers to free",
"data_classes.group_operation.to_operation_sequence.error.registers_count_missmatch": "registers_count must be >= 2",

#endregion

#region math_utils
"math_utils.figures_center.error.weights_count_missmatch": "weights must match positions length",
#endregion

#region forms
"forms.add_spec.error.unknown_kind": "Unknown kind: {kind!r}",

#endregion

#region frame_export_panel
"frame_export_panel.frame_export_resolution_scale": "Resolution scale",
"frame_export_panel.frame_export_use_viewport_base": "Base = viewport size",
"frame_export_panel.frame_export_base_width": "Base width",
"frame_export_panel.frame_export_base_height": "Base height",
"frame_export_panel.frame_export_show_floor": "Show floor",
"frame_export_panel.frame_export_transparent_background": "Transparent background",
"frame_export_panel.frame_export_max_reflect_count": "Max reflect count",
"frame_export_panel.frame_export_phys_glass": "Physical glass",
"frame_export_panel.frame_export_dispersion": "Dispersion (>0 sampling frames are rounded to multiples of 3)",
"frame_export_panel.frame_export_area_ray_count": "Additional rays to light sources (<3 is considered as 0)",
"frame_export_panel.frame_export_top_light_count": "Top-k light source sampling for additional rays",
"frame_export_panel.frame_export_accum_frames": "Accumulation frames (smoothing, 1 = off)",

"frame_export_panel.frame_export_header": "Render frame",
"frame_export_panel.export_to_layer": "Export to layer",
"frame_export_panel.export_to_file.button": "Export to file",
"frame_export_panel.export_to_file.dialog.caption": "Save frame",
"frame_export_panel.frame_export_resolution_size": "Final size: {w} × {h} px",
"frame_export_panel.layer_name": "SDF frame {width}x{height}",
"frame_export_panel.has_no_open_document": "No open document — nothing to export.",
"frame_export_panel.export_progress_sampling": "Frame {cur_frame}/{total_frames} — {pct}% — {elapsed}",
"frame_export_panel.export_progress_default": "{pct}% — {elapsed}",
"frame_export_panel.warning.renderer_not_initialized": "Renderer not initialized, try again later.",
"frame_export_panel.warning.render_no_image": "Render did not return an image.",
"frame_export_panel.warning.save_failed": "Failed to save file:\n{path}",
"frame_export_panel.warning.no_active_document": "Document was closed during rendering.",
"frame_export_panel.warning.document_not_rgba-8-bit": "Only RGBA 8-bit is supported, document has {colorModel}/{colorDepth}.",
"frame_export_panel.render_done": "Done",

#endregion

#region inspector panel
"inspector_panel.header": "Inspector",
"inspector_panel.no_selection": "Nothing selected",
"inspector_panel.node_name": "Name:",
"inspector_panel.build_group_fields.operation": "Operation:",
"inspector_panel.build_group_fields.radius_or_coeff": "Parameter (k/r):",
"inspector_panel.build_group_fields.modifier": "Modifier:",
"inspector_panel.build_group_fields.unary_k": "Modifier Parameter (r):",
"inspector_panel.build_figure_fields.no_figure": "Empty operation",
"inspector_panel.build_figure_fields.type": "Type:",
"inspector_panel.build_figure_fields.operation": "Overlay:",
"inspector_panel.build_figure_fields.radius_or_coeff": "Overlay Parameter (k/r):",
"inspector_panel.build_figure_fields.unary_op": "Modifier:",
"inspector_panel.build_figure_fields.unary_k": "Modifier Parameter (r):",
"inspector_panel.build_figure_fields.material": "Material:",
"inspector_panel.build_figure_fields.albedo": "Albedo:",
"inspector_panel.build_figure_fields.transformations": "Transformations",
"inspector_panel.build_figure_fields.position": "Position:",
"inspector_panel.build_figure_fields.rotation": "Rotation:",
"inspector_panel.build_figure_fields.elongation": "Elongation:",
"inspector_panel.build_figure_fields.scale": "Scale:",
"inspector_panel.build_figure_fields.textures": "Textures",

#endregion

#region material panel
"material_panel.fields.name": "Name:",
"material_panel.fields_specs.roughness": "Roughness:",
"material_panel.fields_specs.metallic": "Metallic:",
"material_panel.fields_specs.transmission": "Transmission:",
"material_panel.fields_specs.ior": "IOR:",
"material_panel.fields_specs.subsurface": "SSS (depth):",
"material_panel.fields_specs.sss_color": "SSS (color):",
"material_panel.fields_specs.emission": "Emission:",

"material_panel.buttons.add": "Add",
"material_panel.buttons.duplicate": "Duplicate",
"material_panel.buttons.delete": "Delete",

"material_panel.commit.warning.is_builtin.title": "Editing Material",
"material_panel.commit.warning.is_builtin.text": "You are trying to edit a built-in material. \nBuilt-in materials cannot be edited directly, but you can create a copy and edit that.",

"material_panel.on_delete.warning.is_builtin.title": "Delete Material",
"material_panel.on_delete.warning.is_builtin.text": "A system built-in preset material cannot be deleted. \nYou can only delete user-created materials.",

"material_panel.on_delete.warning.in_use.title": "Delete Material?",
"material_panel.on_delete.warning.in_use.text": "This material is in use — other objects will get material 0. \nAre you sure you want to delete it?",

#endregion

#region projects panel
"projects_panel.label": "Projects:",
"projects_panel.buttons.new": "New Project",
"projects_panel.buttons.duplicate": "Duplicate",
"projects_panel.buttons.rename": "Rename",
"projects_panel.buttons.delete": "Delete",

"projects_panel.on_new.title": "New Project",
"projects_panel.on_new.text": "Name:",
"projects_panel.on_new.warning.existing_name.text": "A project with that name already exists.",

"projects_panel.on_duplicate.title": "Duplicate Project",
"projects_panel.on_duplicate.text": "Copy Name:",

"projects_panel.on_rename.title": "Rename Project",
"projects_panel.on_rename.text": "New Name:",

"projects_panel.on_delete.title": "Delete Project",
"projects_panel.on_delete.text": "Delete project {name} along with all its nodes?",

#endregion

#region render settings panel
"render_settings_panel.camera.header": "Camera",
"render_settings_panel.camera.fields_specs.camera_pos": "Position:",
"render_settings_panel.camera.fields_specs.camera_rot": "Rotation:",
"render_settings_panel.camera.fields_specs.camera_fov_deg": "FOV (degrees):",

"render_settings_panel.light.header": "Light",
"render_settings_panel.light.fields_specs.light_power_scale": "Light Power Scale:",

"render_settings_panel.background.header": "Background",
"render_settings_panel.background.fields_specs.show_floor": "Show Floor",
"render_settings_panel.background.fields_specs.transparent_background": "Transparent Background",

"render_settings_panel.ambient.header": "Ambient",
"render_settings_panel.ambient.fields_specs.ambient_color": "Ambient Color:",
"render_settings_panel.ambient.fields_specs.ambient_intensity": "Ambient Intensity:",
"render_settings_panel.ambient.fields_specs.sky_sun_azimuth_deg": "Azimuth of Celestial (degrees):",
"render_settings_panel.ambient.fields_specs.sky_sun_elevation_deg": "Elevation of Celestial (degrees):",
"render_settings_panel.ambient.fields_specs.sky_sun_ang_radius": "Celestial Angular Radius:",
"render_settings_panel.ambient.fields_specs.sky_air_density": "Air Density:",

"render_settings_panel.quality.header": "Quality",
"render_settings_panel.quality.fields_specs.max_reflect_count": "Max Reflect Count:",
"render_settings_panel.quality.fields_specs.phys_glass": "Physical Glass",
"render_settings_panel.quality.fields_specs.area_ray_count": "Additional Rays to Light Sources (<3 is treated as 0):",
"render_settings_panel.quality.fields_specs.top_light_count": "Top-k selection of light sources for additional rays:",

#endregion

#region scene tree panel
"scene_tree_widget.drop_error": "Drop error",

"scene_tree_panel.label": "Object Tree:",
"scene_tree_panel.buttons.add": "+ Figure",
"scene_tree_panel.buttons.add_group": "+ Group",
"scene_tree_panel.buttons.duplicate": "Duplicate",
"scene_tree_panel.buttons.delete": "Delete",

#endregion

#region texture_picker_panel
"texture_picker_panel.krita_resources.resources_failed.error.log_debug": "SDForge.picker: failed to get Krita resources ({res_type!r}): {error}",
"texture_picker_panel._db_type_map.type_not_found.log_debug": "SDForge.picker: type {api!r} not found in Krita cache",
"texture_picker_panel._tags_from_cache.cache_open_failed.error.log_debug": "SDForge.picker: cannot open Krita cache: {error}",
"texture_picker_panel._tags_from_cache.query_failed.error.log_debug": "SDForge.picker: Krita tags query failed: {error}",
"texture_picker_panel.krita_all_tags.cache_open_failed.error.log_debug": "SDForge.picker: cannot open Krita cache: {error}",
"texture_picker_panel.krita_all_tags.query_failed.error.log_debug": "SDForge.picker: tag list query failed: {error}",
"texture_picker_panel.load_texture_raw.krita_unavailable.log_debug": "SDForge.picker: Krita API is unavailable",
"texture_picker_panel.load_texture_raw.resource_not_found.log_debug": "SDForge.picker: resource not found: {uri!r}",
"texture_picker_panel.load_texture_raw.load_failed.error.log_debug": "SDForge.picker: failed to load Krita resource {uri!r}: {error}",
"texture_picker_panel._make_thumb.load_failed.error.log_debug": "SDForge.picker: failed to load thumbnail {filename!r}: {error}",
"texture_picker_panel._probe_size.failed.error.log_debug": "SDForge.picker: failed to probe texture size {filename!r}: {error}",
"texture_picker_panel._payload_for.size_probe_failed.log_debug": "SDForge.picker: failed to probe texture size {filename!r}, using full-layer size",
"texture_picker_panel._accept_entry.register_failed.error.log_debug": "SDForge.picker: failed to register texture {filename!r}: {error}",

"texture_picker_panel.slot_titles.albedo": "Albedo",
"texture_picker_panel.slot_titles.normal": "Normal",
"texture_picker_panel.slot_titles.height": "Height",

"texture_picker_panel.init.window_title": "Textures — {slot_title}",
"texture_picker_panel.init.search.placeholder": "Search by name…",
"texture_picker_panel.init.buttons.clear": "Clear slot",
"texture_picker_panel.init.buttons.choose": "Choose",

"texture_picker_panel.tag_combo.all_tags": "All tags",
"texture_picker_panel.tag_combo.untagged": "Untagged",
"texture_picker_panel._refill.tooltip.tags": "\nTags: {tags}",

"texture_picker_panel.fields_specs.offset": "Offset:",
"texture_picker_panel.fields_specs.rotation": "Rotation:",
"texture_picker_panel.fields_specs.tiling_scale": "Tiling:",
"texture_picker_panel.fields_specs.depth_scale": "Depth:",

"texture_picker_panel._build_slot_row.buttons.pick.tooltip": "Choose texture",
"texture_picker_panel._build_slot_row.buttons.clear.tooltip": "Clear slot",
"texture_picker_panel._build_slot_row.offset.tooltip": "Offset in fractions of the tile period (1.0 = one full period)",

#endregion

#region main_dialog
"main_dialog.init.sync_builtin_sdf_types.error.log_debug": "SDForge.dialog: failed to sync builtin SDF types: {error}",
"main_dialog.init.sync_builtin.error.log_debug": "SDForge.dialog: failed to sync builtin data: {error}",

"main_dialog.init.tabs.scene_tree": "Scene Tree",
"main_dialog.init.tabs.projects": "Projects",
"main_dialog.init.tabs.inspector": "Inspector",
"main_dialog.init.tabs.render_settings": "Render",
"main_dialog.init.tabs.materials": "Materials",
"main_dialog.init.tabs.frame_export": "Frame Render",

"main_dialog.init.buttons.undo": "↶ Undo",
"main_dialog.init.buttons.undo.tooltip": "Undo scene change (Ctrl+Z)",
"main_dialog.init.buttons.redo": "↷ Redo",
"main_dialog.init.buttons.redo.tooltip": "Redo scene change (Ctrl+Shift+Z / Ctrl+Y)",

"main_dialog._open_initial_tree.default_tree_name": "Untitled",
#endregion

#region render_previewer
"render_previewer.scene_history.init.error.max_states": "max_states must be >= 2 or None",
"render_previewer.scene_history.activate.error.frozen": "Cannot switch project during a batch edit",

"render_previewer.init.complete.log_debug": "SDForge.previewer: init complete",
"render_previewer._load_fast_render_code.load_failed.error.log_debug": "SDForge.previewer: failed to load fast render code: {error}",
"render_previewer._on_renderer_ready.init_failed.error.log_debug": "SDForge.previewer: renderer init failed",
"render_previewer._suspend_background_work.suspend.log_debug": "SDForge.previewer: background work suspended: {reason}",

"render_previewer._paint_loading_splash.shader_failed.title": "Shader failed to compile",
"render_previewer._paint_loading_splash.shader_failed.hint": "Fix the shader — the preview will rebuild itself",
"render_previewer._paint_loading_splash.loading": "Loading shaders{dots}",
#endregion

#region scene_controller
"scene_controller.open_tree.no_root.log_debug": "SDForge.ctrl: tree {tree_id} has no root, creating one",
"scene_controller._rebuild_texture_images.loader_failed.error.log_debug": "SDForge.ctrl: texture_loader failed for {filename!r}: {error}",
"scene_controller._rebuild_texture_images.texture_unresolved.log_debug": "SDForge.ctrl: texture {filename!r} unresolved, skipped",
"scene_controller.commit_node.no_db_id.log_debug": "SDForge.ctrl: commit_node called on node without db_id",
#endregion

#region init_diagnostics
"init_diagnostics.log_header.begin.log_debug": "SDForge.init: ===== init begin =====",
"init_diagnostics.log_header.plugin_dir.log_debug": "SDForge.init: plugin_dir={plugin_dir}",
"init_diagnostics.log_header.piplib_dir.log_debug": "SDForge.init: piplib_dir={piplib_dir}",
"init_diagnostics.log_header.cwd.log_debug": "SDForge.init: cwd={cwd}",
"init_diagnostics.log_header.executable.log_debug": "SDForge.init: executable={executable}",
"init_diagnostics.log_header.python_version.log_debug": "SDForge.init: python_version={python_version}",
"init_diagnostics.log_header.platform.log_debug": "SDForge.init: platform={platform}",
"init_diagnostics.log_header.architecture.log_debug": "SDForge.init: architecture={architecture}",
"init_diagnostics.log_header.maxsize.log_debug": "SDForge.init: maxsize={maxsize}",

"init_diagnostics.log_sys_path.entry.log_debug": "SDForge.init: sys.path[{index}]={path}",

"init_diagnostics.log_piplib_contents.dir_missing.log_debug": "SDForge.init: piplib directory does not exist",
"init_diagnostics.log_piplib_contents.entries_count.log_debug": "SDForge.init: piplib entries count={count}",
"init_diagnostics.log_piplib_contents.entry_dir.log_debug": "SDForge.init: piplib dir: {name}",
"init_diagnostics.log_piplib_contents.entry_file.log_debug": "SDForge.init: piplib file: {name}",
"init_diagnostics.log_piplib_contents.list_failed.error.log_debug": "SDForge.init: failed to list piplib contents: {error}",
"init_diagnostics.log_piplib_contents.path_exists.log_debug": "SDForge.init: exists {name} -> {exists}, path={path}",

"init_diagnostics.log_imports.begin.log_debug": "SDForge.init: import diagnostics begin",
"init_diagnostics.log_imports.moderngl_from.log_debug": "SDForge.init: moderngl imported from: {file}",
"init_diagnostics.log_imports.moderngl_version.log_debug": "SDForge.init: moderngl version: {version}",
"init_diagnostics.log_imports.moderngl_has_create_context.log_debug": "SDForge.init: moderngl has create_context={has_create_context}",
"init_diagnostics.log_imports.moderngl_import_failed.error.log_debug": "SDForge.init: failed to import moderngl: {error}",
"init_diagnostics.log_imports.private_moderngl_from.log_debug": "SDForge.init: _moderngl imported from: {file}",
"init_diagnostics.log_imports.private_moderngl_import_failed.error.log_debug": "SDForge.init: failed to import _moderngl: {error}",
"init_diagnostics.log_imports.glcontext_from.log_debug": "SDForge.init: glcontext imported from: {file}",
"init_diagnostics.log_imports.glcontext_dir_sample.log_debug": "SDForge.init: glcontext dir sample={sample}",
"init_diagnostics.log_imports.glcontext_import_failed.error.log_debug": "SDForge.init: failed to import glcontext: {error}",
"init_diagnostics.log_imports.create_context_try.log_debug": "SDForge.init: trying moderngl.create_context(standalone=True)",
"init_diagnostics.log_imports.create_context_done.log_debug": "SDForge.init: standalone context created, version_code={version_code}",
"init_diagnostics.log_imports.create_context_info.log_debug": "SDForge.init: ctx.info={info}",
"init_diagnostics.log_imports.create_context_released.log_debug": "SDForge.init: standalone context released",
"init_diagnostics.log_imports.create_context_release_failed.error.log_debug": "SDForge.init: context release failed: {error}",
"init_diagnostics.log_imports.create_context_failed.error.log_debug": "SDForge.init: standalone context creation failed: {error}",
"init_diagnostics.log_imports.end.log_debug": "SDForge.init: import diagnostics end",

#endregion

#region main
"main.init.log_debug": "SDForge started successfully",

#endregion

}

_TABLES = {"ru": L_RU, "en": L_EN}
_current = _TABLES[language]


class _SafeDict(dict):
    def __missing__(self, key):
        return "{" + key + "}"


def get_text(key: str, **values) -> str:
    template = _current.get(key) or L_RU.get(key, key)
    try:
        return template.format_map(_SafeDict(values))
    except (ValueError, IndexError, AttributeError):
        return template
    