"""
SDForge: текстуры — загрузка из ресурсов Krita, выбор, слоты.

Источник текстур — ТОЛЬКО ресурсы Krita (KRITA_RESOURCE_TYPES).
Якорь в реестре и payload — URI вида krita://<тип>/<имя>: он стабилен
и не зависит от того, лежит ресурс файлом или внутри bundle.

Три части:
1. Загрузчики (публичные, их забирает main_dialog):
   - load_texture_image(uri) -> QImage|None — сырой контент; приведение
     к слою GPU-массива (кап размера, даунскейл, padding) выполняет
     рендерер (_normalize_layer в renderer_mgl);
   - probe_texture_size(uri) -> (w, h)|None — реальный размер
     контента в слое (для originalsize в payload слота);
   - load_texture_raw(uri) -> QImage|None — без трансформаций
     (для миниатюр и пробы).
   Krita импортируется лениво внутри функций — модуль можно
   импортировать вне Krita (тесты).
2. TexturePickerDialog — окно выбора: миниатюры, поиск, теги.
   Ресурсы показываются автоматически; регистрация в БД ленивая —
   только при выборе (иначе GPU-массив был бы переполнен).
   Фильтр тегов — реальные теги ресурсов Krita (читаются из
   resourcecache.sqlite, Python API их не отдаёт).
3. TextureSlotsWidget — компактный блок слотов albedo/normal/height
   для инспектора; параметры слотов — шаблонная форма (Form + FieldSpec).

Единицы и форматы (согласованы с sdf_frag_high.frag):
- размер слоя массива выбирает рендерер: максимум по набору текстур,
  вписанный в DEFAULT_TEXTURE_MAX_SIZE; слишком большие текстуры
  уменьшаются, остаток слоя — padding репликацией края (иначе
  LINEAR-фильтр на кромке даст шов; для height — бороздку в геометрии);
- originalsize — реальный пиксельный размер контента в слое
  (шейдер: atlasScale = origTexSize / arraySize);
- rotation — в payload радианы (шейдер: rotate(-texRotation)),
  в UI — градусы;
- offset — доли периода тайла (вычитается до fract в calculateUV);
- tilingscale — множитель частоты (2.0 = два повтора на единицу).
"""

import os
import sqlite3 
from typing import Any, Callable, Dict, List, Optional, Tuple

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QIcon, QImage, QPainter, QPixmap

from ...core.data_classes import FigureTexture, FigureHeightmap
from ..render_previewer import RenderPreviewParams
from ..scene_controller import SceneController

from ...config import log_debug
from .forms import Form, FieldSpec

from ...config import DEFAULT_TEXTURE_MAX_SIZE
from ... import localization

THUMB_SIZE = 96
_SLOT_NAMES = ("albedo", "normal", "height")
_SLOT_TITLES = {"albedo": localization.get_text("texture_picker_panel.slot_titles.albedo"), 
                "normal": localization.get_text("texture_picker_panel.slot_titles.normal"), 
                "height": localization.get_text("texture_picker_panel.slot_titles.height")}
_UNTAGGED = "__untagged__"  # псевдо-тег фильтра: «All untagged» как в Krita

# Типы ресурсов Krita, которые считаем текстурами
KRITA_RESOURCE_TYPES = ("pattern",)
_KRITA_SCHEME = "krita://"


# ----------------------------------------------------------------------
# Ресурсы и загрузка (публичное API для main_dialog)
# ----------------------------------------------------------------------
#region
def _krita():
    """Ленивый импорт Krita API; вне Krita — None."""
    try:
        from krita import Krita
        return Krita.instance()
    except Exception:
        return None


def krita_resources() -> List[Dict[str, str]]:
    """Все ресурсы-текстуры Krita: [{uri, name}].
    uri = krita://<тип>/<имя> — единственный поддерживаемый якорь."""
    app = _krita()
    if app is None:
        return []
    result: List[Dict[str, str]] = []
    for res_type in KRITA_RESOURCE_TYPES:
        try:
            resources = app.resources(res_type)
        except Exception as e:
            log_debug(localization.get_text("texture_picker_panel.krita_resources.resources_failed.error.log_debug", res_type=res_type, error=str(e)))
            continue
        for name in resources.keys():
            result.append({"uri": f"{_KRITA_SCHEME}{res_type}/{name}",
                           "name": name})
    return result

def _resourcecache_path() -> Optional[str]:
    """Путь к resourcecache.sqlite (кэш ресурсов и тегов Krita).

    Порядок: kritarc ResourceCacheDbDirectory / ResourceDirectory
    (папку ресурсов могли перенести), затем стандартные расположения
    (%APPDATA%/krita, ~/.local/share/krita).
    """
    app = _krita()
    if app is None:
        return None
    candidates: List[str] = []
    for key in ("ResourceCacheDbDirectory", "ResourceDirectory"):
        try:
            custom = app.readSetting("", key, "")
        except Exception:
            custom = ""
        if custom:
            candidates.append(custom)
    for loc in (QtCore.QStandardPaths.DataLocation,
                QtCore.QStandardPaths.AppDataLocation):
        candidates.extend(QtCore.QStandardPaths.standardLocations(loc))
    for base in candidates:
        path = os.path.join(base, "resourcecache.sqlite")
        if os.path.isfile(path):
            return path
    return None

def _db_type_map(conn) -> Dict[str, str]:
    """{имя типа в БД: имя типа в API} для KRITA_RESOURCE_TYPES.

    API Krita использует singular ('pattern' — app.resources('pattern')),
    а в resourcecache.sqlite типы записаны plural ('patterns').
    """
    db_names = {r[0] for r in conn.execute("SELECT name FROM resource_types")}
    result: Dict[str, str] = {}
    for api in KRITA_RESOURCE_TYPES:
        for cand in (api, api + "s", api + "es"):
            if cand in db_names:
                result[cand] = api
                break
        else:
            log_debug(localization.get_text("texture_picker_panel._db_type_map.type_not_found.log_debug", api=api))
    return result

def _tags_from_cache(path: str) -> Dict[str, List[str]]:
    """{uri: [тег, ...]} из resourcecache.sqlite (только чтение).

    Схема KisResourceCacheDb: resources + resource_types + tags +
    resource_tags. Колонок active может не быть в старых версиях —
    проверяем через PRAGMA.
    """
    result: Dict[str, List[str]] = {}
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        try:
            conn = sqlite3.connect(path)
        except sqlite3.Error as e:
            log_debug(localization.get_text("texture_picker_panel._tags_from_cache.cache_open_failed.error.log_debug", error=str(e)))
            return result
    try:
        type_map = _db_type_map(conn)
        if not type_map:
            return result

        def _has_col(table: str, col: str) -> bool:
            return any(r[1] == col
                       for r in conn.execute(f"PRAGMA table_info({table})"))
        active = (" AND r.active = 1" if _has_col("resources", "active")
                  else "")
        active += " AND t.active = 1" if _has_col("tags", "active") else ""
        active += (" AND rt.active = 1"
                   if _has_col("resource_tags", "active") else "")
        marks = ", ".join("?" for _ in type_map)
        sql = ("SELECT ty.name, r.name, t.name "
               "FROM resources r "
               "JOIN resource_tags rt ON rt.resource_id = r.id "
               "JOIN tags t ON t.id = rt.tag_id "
               "JOIN resource_types ty ON ty.id = r.resource_type_id "
               f"WHERE ty.name IN ({marks}){active}")
        for db_type, res_name, tag_name in conn.execute(
                sql, tuple(type_map)):
            uri = f"{_KRITA_SCHEME}{type_map[db_type]}/{res_name}"
            tags = result.setdefault(uri, [])
            tag_name = str(tag_name)
            if tag_name not in tags:
                tags.append(tag_name)
    except sqlite3.Error as e:
        log_debug(localization.get_text("texture_picker_panel._tags_from_cache.query_failed.error.log_debug", error=str(e)))
    finally:
        conn.close()
    return result

def krita_resource_tags() -> Dict[str, List[str]]:
    """Реальные теги текстур из Krita: {uri: [тег, ...]}.

    Python API теги ресурсов не отдаёт (у Resource только name/type/
    image/filename/data), поэтому кэш resourcecache.sqlite читается
    напрямую и только на чтение. Ключ — тот же uri krita://<тип>/<имя>,
    что строит krita_resources().
    """
    path = _resourcecache_path()
    return _tags_from_cache(path) if path else {}

def krita_all_tags() -> List[str]:
    """Все теги текстурных типов из Krita — как в её собственном
    выпадающем списке, включая теги, ещё не присвоенные ни одному
    ресурсу (присвоенные по ресурсам даёт krita_resource_tags)."""
    path = _resourcecache_path()
    if path is None:
        return []
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error:
        try:
            conn = sqlite3.connect(path)
        except sqlite3.Error as e:
            log_debug(localization.get_text("texture_picker_panel.krita_all_tags.cache_open_failed.error.log_debug", error=str(e)))
            return []
    try:
        type_map = _db_type_map(conn)
        if not type_map:
            return []
        has_active = any(
            r[1] == "active"
            for r in conn.execute("PRAGMA table_info(tags)"))
        marks = ", ".join("?" for _ in type_map)
        sql = ("SELECT DISTINCT t.name FROM tags t "
               "JOIN resource_types ty ON ty.id = t.resource_type_id "
               f"WHERE ty.name IN ({marks})"
               + (" AND t.active = 1" if has_active else ""))
        return sorted({str(r[0]) for r in
                       conn.execute(sql, tuple(type_map))})
    except sqlite3.Error as e:
        log_debug(localization.get_text("texture_picker_panel.krita_all_tags.query_failed.error.log_debug", error=str(e)))
        return []
    finally:
        conn.close()

def load_texture_raw(uri: str) -> Optional[QImage]:
    """Текстура БЕЗ трансформаций. Основной путь — krita:// URI;
    обычный путь к файлу поддержан для обратной совместимости
    со старыми записями реестра."""
    if uri.startswith(_KRITA_SCHEME):
        app = _krita()
        if app is None:
            log_debug(localization.get_text("texture_picker_panel.load_texture_raw.krita_unavailable.log_debug"))
            return None
        try:
            res_type, name = uri[len(_KRITA_SCHEME):].split("/", 1)
            res = app.resources(res_type).get(name)
            if res is None:
                log_debug(localization.get_text("texture_picker_panel.load_texture_raw.resource_not_found.log_debug", uri=uri))
                return None
            img = res.image()
            if img is None or img.isNull():
                # Запасной вариант: thumbnail() есть у всех ресурсов
                img = res.thumbnail()
            return img if img is not None and not img.isNull() else None
        except Exception as e:
            log_debug(localization.get_text("texture_picker_panel.load_texture_raw.load_failed.error.log_debug", uri=uri, error=str(e)))
            return None
    img = QImage(uri)  # легаси: реальный путь
    return None if img.isNull() else img

def probe_texture_size(uri: str) -> Optional[Tuple[int, int]]:
    """Реальный размер контента в слое (для originalsize в payload)."""
    img = load_texture_raw(uri)
    if img is None:
        return None
    w, h = img.width(), img.height()
    if w >DEFAULT_TEXTURE_MAX_SIZE or h >DEFAULT_TEXTURE_MAX_SIZE:
        k = min(DEFAULT_TEXTURE_MAX_SIZE / w, DEFAULT_TEXTURE_MAX_SIZE / h)
        w, h = max(1, int(w * k + 0.5)), max(1, int(h * k + 0.5))
    return (w, h)

# Функция оставлена на случай необходимости отката, сейчас не используется
def pad_to_array(img: QImage) -> QImage:
    """СлойDEFAULT_TEXTURE_MAX_SIZE^2: контент в (0,0) исходного размера,
    остаток — репликация крайних пикселей (padding, НЕ масштабирование).
    """
    img = img.convertToFormat(QImage.Format_RGBA8888)
    w, h = img.width(), img.height()

    if w >DEFAULT_TEXTURE_MAX_SIZE or h >DEFAULT_TEXTURE_MAX_SIZE:
        img = img.scaled(DEFAULT_TEXTURE_MAX_SIZE, DEFAULT_TEXTURE_MAX_SIZE,
                         Qt.KeepAspectRatio, Qt.SmoothTransformation)
        w, h = img.width(), img.height()
    if w ==DEFAULT_TEXTURE_MAX_SIZE and h ==DEFAULT_TEXTURE_MAX_SIZE:
        return img

    S =DEFAULT_TEXTURE_MAX_SIZE
    canvas = QImage(S, S, QImage.Format_RGBA8888)
    canvas.fill(0)
    p = QPainter(canvas)
    p.drawImage(0, 0, img)
    if w < S:   # правая полоса — растянуть последний столбец
        p.drawImage(QtCore.QRect(w, 0, S - w, h), img,
                    QtCore.QRect(w - 1, 0, 1, h))
    if h < S:   # нижняя полоса — последняя строка
        p.drawImage(QtCore.QRect(0, h, w, S - h), img,
                    QtCore.QRect(0, h - 1, w, 1))
    if w < S and h < S:   # угол — угловой пиксель
        p.drawImage(QtCore.QRect(w, h, S - w, S - h), img,
                    QtCore.QRect(w - 1, h - 1, 1, 1))
    p.end()
    return canvas

def load_texture_image(uri: str) -> Optional[QImage]:
    """Хук для SceneController: сырой контент текстуры; приведение к слою
    массива выполняет рендерер (_normalize_layer)."""
    img = load_texture_raw(uri)
    return img

#endregion
# ----------------------------------------------------------------------
# Диалог выбора текстуры
# ----------------------------------------------------------------------
#region
class TexturePickerDialog(QtWidgets.QDialog):
    """Окно выбора текстуры из ресурсов Krita: миниатюры, поиск, теги.

    texture_loader/size_probe — необязательные; по умолчанию модульные
    load_texture_image / probe_texture_size.
    """

    texture_chosen = pyqtSignal(dict)  # payload слота {"file": ...}

    def __init__(self, params: "RenderPreviewParams", controller: "SceneController", texture_loader=None,
                 slot_name: str = "albedo",
                 size_probe: Optional[Callable] = None, parent=None):
        super().__init__(parent)
        self.params = params
        self.controller = controller
        self.texture_loader = texture_loader or load_texture_image
        self.slot_name = slot_name
        self.size_probe = size_probe or probe_texture_size
        self._krita_tags: Dict[str, List[str]] = {}  # реальные теги Krita
        # элементы: {"filename", "name", "registered": bool}
        self._textures: List[Dict[str, Any]] = []

        self.setWindowTitle(localization.get_text("texture_picker_panel.init.window_title", slot_name=_SLOT_TITLES.get(slot_name, slot_name)))
        self.setMinimumSize(640, 480)
        self.setModal(False)  # не блокируем инспектор

        layout = QtWidgets.QVBoxLayout(self)

        #region --- Панель фильтров --- #
        filters = QtWidgets.QHBoxLayout()
        self.search_edit = QtWidgets.QLineEdit()
        self.search_edit.setPlaceholderText(localization.get_text("texture_picker_panel.init.search.placeholder"))
        self.search_edit.textChanged.connect(self._refill)
        filters.addWidget(self.search_edit, 1)

        self.tag_combo = QtWidgets.QComboBox()
        self.tag_combo.addItem(localization.get_text("texture_picker_panel.tag_combo.all_tags"), None)
        self.tag_combo.currentIndexChanged.connect(self._refill)
        filters.addWidget(self.tag_combo)
        layout.addLayout(filters)

        #endregion

        #region --- Сетка миниатюр --- #
        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setViewMode(QtWidgets.QListView.IconMode)
        self.list_widget.setIconSize(QtCore.QSize(THUMB_SIZE, THUMB_SIZE))
        self.list_widget.setGridSize(QtCore.QSize(THUMB_SIZE + 24,
                                                  THUMB_SIZE + 44))
        self.list_widget.setResizeMode(QtWidgets.QListView.Adjust)
        self.list_widget.setSpacing(6)
        self.list_widget.setUniformItemSizes(True)
        self.list_widget.itemDoubleClicked.connect(self._on_accept_item)
        layout.addWidget(self.list_widget, 1)

        #endregion

        #region --- Нижняя панель: кнопки --- #
        bottom = QtWidgets.QHBoxLayout()
        bottom.addStretch(1)

        self.btn_clear = QtWidgets.QPushButton(localization.get_text("texture_picker_panel.init.buttons.clear"))
        self.btn_clear.clicked.connect(self._on_clear_slot)
        bottom.addWidget(self.btn_clear)

        self.btn_choose = QtWidgets.QPushButton(localization.get_text("texture_picker_panel.init.buttons.choose"))
        self.btn_choose.setDefault(True)
        self.btn_choose.clicked.connect(self._on_accept_current)
        bottom.addWidget(self.btn_choose)
        layout.addLayout(bottom)

        self.reload()

        #endregion

    # ------------------------------------------------------------------
    # Данные
    # ------------------------------------------------------------------
    #region
    def reload(self) -> None:
        """Перечитывает ресурсы Krita + реестр + теги, обновляет сетку."""
        registered = {t["filename"]
                      for t in self.controller.textures()}
        self._textures = [
            {"filename": res["uri"], "name": res["name"],
             "registered": res["uri"] in registered}
            for res in krita_resources()
        ]
        self._krita_tags = krita_resource_tags()

        # Фильтр — все теги Krita (как в её списке, включая пустые)
        current_tag = self.tag_combo.currentData()
        self.tag_combo.blockSignals(True)
        self.tag_combo.clear()
        self.tag_combo.addItem(localization.get_text("texture_picker_panel.tag_combo.all_tags"), None)
        self.tag_combo.addItem(localization.get_text("texture_picker_panel.tag_combo.untagged"), _UNTAGGED)
        for tag in krita_all_tags():
            self.tag_combo.addItem(tag, tag)
        idx = self.tag_combo.findData(current_tag)
        self.tag_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self.tag_combo.blockSignals(False)

        self._refill()

    def _tex_tags(self, filename: str) -> List[str]:
        """Все теги текстуры: реальные из Krita"""
        return sorted(set(self._krita_tags.get(filename, ())))

    def _refill(self) -> None:
        """Заполняет сетку с учётом поиска и фильтра по тегу."""
        query = self.search_edit.text().strip().lower()
        tag = self.tag_combo.currentData()

        self.list_widget.blockSignals(True)
        self.list_widget.clear()
        for tex in self._textures:
            filename = tex["filename"]
            name = tex["name"]
            tags = self._tex_tags(filename)

            if tag == _UNTAGGED:
                if tags:
                    continue
            elif tag and tag not in tags:
                continue
            if query and query not in name.lower() \
                    and query not in filename.lower():
                continue

            item = QtWidgets.QListWidgetItem(name)
            item.setData(Qt.UserRole, filename)
            item.setToolTip(f"{name}"
                            + (localization.get_text("texture_picker_panel._refill.tooltip.tags", tags=", ".join(tags)) if tags else ""))
            thumb = self._make_thumb(filename)
            if thumb is not None:
                item.setIcon(QIcon(thumb))
            self.list_widget.addItem(item)
        self.list_widget.blockSignals(False)
        if self.list_widget.count():
            self.list_widget.setCurrentRow(0)

    def _make_thumb(self, filename: str) -> Optional[QPixmap]:
        # Для миниатюр достаточно raw-загрузки
        try:
            img = load_texture_raw(filename)
        except Exception as e:
            log_debug(localization.get_text("texture_picker_panel._make_thumb.load_failed.error.log_debug", filename=filename, error=str(e)))
            img = None
        if img is None or img.isNull():
            return None
        return QPixmap.fromImage(img).scaled(
            THUMB_SIZE, THUMB_SIZE,
            Qt.KeepAspectRatio, Qt.SmoothTransformation,
        )

    #endregion
    # ------------------------------------------------------------------
    # Выбор / очистка
    # ------------------------------------------------------------------
    #region
    def _current_entry(self) -> Optional[Dict[str, Any]]:
        item = self.list_widget.currentItem()
        if item is None:
            return None
        filename = item.data(Qt.UserRole)
        for tex in self._textures:
            if tex["filename"] == filename:
                return tex
        return {"filename": filename, "name": filename, "registered": True}

    def _probe_size(self, filename: str) -> Optional[List[float]]:
        if self.size_probe is None:
            return None
        try:
            real = self.size_probe(filename)
        except Exception as e:
            log_debug(localization.get_text("texture_picker_panel._probe_size.failed.error.log_debug", filename=filename, error=str(e)))
            return None
        if not real:
            return None
        return [float(real[0]), float(real[1])]

    def _payload_for(self, filename: str) -> Dict[str, Any]:
        """Стартовый payload слота: дефолтная трансформация из класса слота
        + РЕАЛЬНЫЙ размер текстуры (original_size) — шейдер по нему считает
        atlasScale = origTexSize / arraySize внутри слоя."""
        cls = FigureHeightmap if self.slot_name == "height" else FigureTexture
        defaults = cls()
        probed = self._probe_size(filename)
        if probed is None:
            log_debug(localization.get_text("texture_picker_panel._payload_for.size_probe_failed.log_debug", filename=filename))
        payload: Dict[str, Any] = {
            "file": filename,
            "offset": list(defaults.offset),
            "rotation": float(defaults.rotation),
            "original_size": probed or [float(DEFAULT_TEXTURE_MAX_SIZE)] * 2,
            "tiling_scale": float(defaults.tiling_scale),
        }
        if self.slot_name == "height":
            payload["depth_scale"] = float(defaults.depth_scale)
        return payload

    def _accept_entry(self, entry: Dict[str, Any]) -> None:
        filename = entry["filename"]
        if not entry.get("registered", True):
            # Ленивая регистрация: в БД и GPU попадёт только выбранное.
            try:
                self.controller.register_texture(filename, name=entry["name"])
            except TypeError:
                self.controller.register_texture(filename)
            except Exception as e:
                log_debug(localization.get_text("texture_picker_panel._accept_entry.register_failed.error.log_debug", filename=filename, error=str(e)))
        self.texture_chosen.emit(self._payload_for(filename))
        self.accept()

    def _on_accept_item(self, item) -> None:
        entry = self._current_entry()
        if entry:
            self._accept_entry(entry)

    def _on_accept_current(self) -> None:
        entry = self._current_entry()
        if entry:
            self._accept_entry(entry)

    def _on_clear_slot(self) -> None:
        self.texture_chosen.emit({})  # пустой payload = слот выключен
        self.accept()

    #endregion
#endregion

# ----------------------------------------------------------------------
# Компактный блок слотов для инспектора
# ----------------------------------------------------------------------
#region
# Спеки параметров слота для Form.add_specs. payload — dict, поэтому
# target формы — _SlotAdapter (атрибуты поверх ключей dict).
# rotation в payload — радианы; kind="angle" показывает градусы
# (AngleBinding внутри Form.add_angle, см. forms.py).
_SLOT_SPECS = [
    FieldSpec("vec2", localization.get_text("texture_picker_panel.fields_specs.offset"), "offset",
              minimum=-100000.0, maximum=100000.0, step=0.05),
    FieldSpec("angle", localization.get_text("texture_picker_panel.fields_specs.rotation"), "rotation",
              minimum=-360.0, maximum=360.0, step=1.0,
              decimals=1, suffix=" °"),
    FieldSpec("float", localization.get_text("texture_picker_panel.fields_specs.tiling_scale"), "tiling_scale",
              minimum=0.001, maximum=1000.0, step=0.05),
]
_HEIGHT_SPECS = [
    FieldSpec("float", localization.get_text("texture_picker_panel.fields_specs.depth_scale"), "depth_scale",
              minimum=-2.0, maximum=2.0, step=0.001, decimals=4),
]


class _SlotAdapter:
    """Атрибуты поверх payload слота (dict) — чтобы параметры слота
    собирались декларативно, через Form.add_specs.

    Ключи — имена атрибутов FigureTexture/FigureHeightmap, дефолты
    берутся из пробного экземпляра соответствующего класса.

    Пустой слот (нет текстуры) читается как None — поля формы сами
    выключаются (FormField.refresh); запись в пустой слот игнорируется.
    """

    # Редактируемые параметры слота; original_size задаётся пробой при выборе
    _KEYS = ("offset", "rotation", "tiling_scale", "depth_scale")

    def __init__(self, slots: Callable[[], Dict[str, Any]], slot: str):
        object.__setattr__(self, "_slots", slots)
        object.__setattr__(self, "_slot", slot)
        cls = FigureHeightmap if slot == "height" else FigureTexture
        probe = cls()
        defaults = {}
        for key in self._KEYS:
            value = getattr(probe, key, None)
            if value is None:
                continue
            defaults[key] = list(value) if isinstance(value, (list, tuple)) else value
        object.__setattr__(self, "_defaults", defaults)

    def _payload(self) -> Optional[Dict[str, Any]]:
        payload = self._slots().get(self._slot)
        return payload if payload and payload.get("file") else None

    def __getattr__(self, name: str) -> Any:
        if name in self._KEYS:
            payload = self._payload()
            if payload is None:
                return None
            value = payload.get(name, self._defaults.get(name))
            return list(value) if isinstance(value, (list, tuple)) else value
        raise AttributeError(name)

    def __setattr__(self, name: str, value: Any) -> None:
        if name in self._KEYS:
            payload = self._payload()
            if payload is not None:
                payload[name] = value
        else:
            object.__setattr__(self, name, value)

class TextureSlotsWidget(QtWidgets.QWidget):
    """Три слота (albedo/normal/height): миниатюра, имя, параметры слота.

    Читает/пишет fig.texture_slots (payload по URI текстуры) и вызывает
    commit-функцию инспектора после любой правки.

    Параметры слота — шаблонная форма (Form + FieldSpec из forms.py):
    привязки — AttrBinding на _SlotAdapter, поворот конвертируется
    радианы <-> градусы через kind="angle". Ручных спинбоксов, _loading
    и blockSignals здесь больше нет (forms_guide.md, чеклист А6).
    """

    def __init__(self, params: "RenderPreviewParams", controller: "SceneController", node, commit: Callable[[], None],
                 picker_factory: Optional[Callable[[str], None]] = None,
                 parent=None):
        super().__init__(parent)
        self.params = params
        self.controller = controller
        self.node = node
        self.commit = commit
        # picker_factory(slot_name) открывает TexturePickerDialog;
        # если None — создаём диалог сами
        self._picker_factory = picker_factory

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self._slot_rows: Dict[str, Dict[str, Any]] = {}
        for slot in _SLOT_NAMES:
            layout.addWidget(self._build_slot_row(slot))

        self.reload()

    # ------------------------------------------------------------------
    # Построение
    # ------------------------------------------------------------------
    #region
    def _build_slot_row(self, slot: str) -> QtWidgets.QWidget:
        box = QtWidgets.QGroupBox(_SLOT_TITLES[slot])
        rows = QtWidgets.QFormLayout(box)
        rows.setContentsMargins(4, 4, 4, 4)

        # Строка: миниатюра + имя + кнопки
        head = QtWidgets.QHBoxLayout()
        thumb = QtWidgets.QLabel()
        thumb.setFixedSize(40, 40)
        thumb.setStyleSheet("border: 1px solid #444; background: #222;")
        thumb.setAlignment(Qt.AlignCenter)
        head.addWidget(thumb)

        name_lbl = QtWidgets.QLabel("—")
        name_lbl.setWordWrap(True)
        head.addWidget(name_lbl, 1)

        btn_pick = QtWidgets.QPushButton("…")
        btn_pick.setFixedWidth(28)
        btn_pick.setToolTip(localization.get_text("texture_picker_panel._build_slot_row.buttons.pick.tooltip"))
        btn_pick.clicked.connect(lambda _=False, s=slot: self._open_picker(s))
        head.addWidget(btn_pick)

        btn_clear = QtWidgets.QPushButton("×")
        btn_clear.setFixedWidth(28)
        btn_clear.setToolTip(localization.get_text("texture_picker_panel._build_slot_row.buttons.clear.tooltip"))
        btn_clear.clicked.connect(lambda _=False, s=slot: self._clear_slot(s))
        head.addWidget(btn_clear)
        rows.addRow(head)

        # Параметры слота — декларативная форма поверх payload
        params_form = Form(box)
        specs = _SLOT_SPECS + (_HEIGHT_SPECS if slot == "height" else [])
        fields = params_form.add_specs(
            specs, target=_SlotAdapter(self._slots, slot),
            commit=self.commit)
        fields[0].setToolTip(localization.get_text("texture_picker_panel._build_slot_row.offset.tooltip"))
        rows.addRow(params_form)

        self._slot_rows[slot] = {"thumb": thumb, "name": name_lbl,
                                 "params": params_form}
        return box

    #endregion
    # ------------------------------------------------------------------
    # Данные
    # ------------------------------------------------------------------
    #region
    def _slots(self) -> Dict[str, Any]:
        fig = self.node.figure if self.node is not None else None
        if fig is None:
            return {}
        if not hasattr(fig, "texture_slots") or fig.texture_slots is None:
            fig.texture_slots = {}
        return fig.texture_slots

    def reload(self) -> None:
        """Перечитывает слоты фигуры в виджеты (без коммита)."""
        slots = self._slots()
        for slot, row in self._slot_rows.items():
            payload = slots.get(slot) or {}
            filename = payload.get("file")

            row["name"].setText(
                os.path.basename(filename) if filename else "—")
            self._fill_thumb(row["thumb"], filename)

            # Сверка полей с payload; у пустого слота адаптер даёт None —
            # поля выключаются сами (см. FormField.refresh в forms.py)
            row["params"].refresh()
            row["params"].setVisible(bool(filename))

    def _fill_thumb(self, label: QtWidgets.QLabel,
                    filename: Optional[str]) -> None:
        if not filename:
            label.setPixmap(QPixmap())
            label.setText("—")
            return
        try:
            img = load_texture_raw(filename)
        except Exception:
            img = None
        if img is None or img.isNull():
            label.setPixmap(QPixmap())
            label.setText("?")
            return
        label.setPixmap(QPixmap.fromImage(img).scaled(
            40, 40, Qt.KeepAspectRatio, Qt.SmoothTransformation,
        ))

    #endregion
    # ------------------------------------------------------------------
    # Правки
    # ------------------------------------------------------------------
    #region
    def _clear_slot(self, slot: str) -> None:
        slots = self._slots()
        if slot in slots:
            slots.pop(slot)
        self.reload()
        self.commit()

    def _open_picker(self, slot: str) -> None:
        if self._picker_factory is not None:
            self._picker_factory(slot)
            return
        dlg = TexturePickerDialog(
            self.params, self.controller,
            texture_loader=getattr(self.controller, "texture_loader", None),
            slot_name=slot, parent=self,
        )

        def _apply(payload: Dict[str, Any], s=slot):
            slots = self._slots()
            if payload:
                # Сохраняем уже настроенные параметры слота, если были
                old = slots.get(s) or {}
                for key in ("offset", "rotation", "tilingscale", "depthscale"):
                    if key in old and key not in payload:
                        payload[key] = old[key]
                slots[s] = payload
            else:
                slots.pop(s, None)
            self.reload()
            self.commit()

        dlg.texture_chosen.connect(_apply)
        dlg.show()

    #endregion
#endregion