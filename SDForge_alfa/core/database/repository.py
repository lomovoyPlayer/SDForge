"""
SDForge: репозиторий поверх core/db.py.

Сериализация (спеки вместо хардкода полей):
- набор сериализуемых полей каждого класса выводится из сигнатуры его
  __init__ (см. _spec_for): ключ JSON == имя атрибута; runtime-поля
  перечислены в _RUNTIME_FIELDS и не сохраняются, расхождения
  "ключ != параметр конструктора" — в _CTOR_ALIAS (Figure.type);
- дефолты и типы для толерантного чтения берутся из пробного экземпляра
  класса, поэтому изменение дефолта в data_classes.py не требует
  правок здесь;
- пары "объект <-> payload" выполняют generic-хелперы _extract/_construct;
  ручными остаются только поля-исключения: material_id (конвертация
  runtime-индекс <-> стабильный materials.id), textures (производное
  от слотов) и file у текстурных слотов;
- траектории: в БД сохраняются trajectory_fn_id (строковый ключ реестра
  core/trajectories.py) и trajectory_params (dict); функция — runtime,
  резолвится конструктором Figure из реестра.

Правила совместимости версий плагина:
- толерантное чтение: неизвестные ключи не вызывают ошибок, а складываются
  в obj.payload_extra и переживают следующий цикл сохранения;
- запись всегда полная: JSON пишется целиком из актуального объекта,
  поэтому узел "догоняет" текущую версию формата при первом редактировании;
- Figure.material_id в рантайме — индекс в списке материалов
  (ORDER BY order_index), в БД — стабильный materials.id;
- текстуры в БД привязаны к имени файла; runtime id (индекс в
  texture_images / слой texture array) в БД не попадает. После load_tree()
  UI резолвит имена в id и вызывает apply_resolved_textures(), которая
  заполняет fig.node_textures.

Встроенные сущности (is_builtin):
- деревья: sync_builtin_tree() пересоздаёт дефолтную сцену при каждом
  старте — она не переживает сессию; delete/rename для builtin запрещены
  (ValueError), duplicate разрешён (копия — обычное пользовательское дерево);
- материалы: sync_builtin_materials() upsert'ит дефолты по имени при
  каждом старте (id стабильны, ссылки фигур не ломаются), builtin всегда
  в голове списка; remove/update/reorder запрещены, duplicate разрешён
  (копия — пользовательский материал).

Глобальные настройки хранятся в meta (одна строка key='global'):
control_settings — настройки управления (дефолты DEFAULT_CONTROL_SETTINGS),
misc — прочее мелкое состояние; обновление — merge-патчем.

Интеграция с RenderPreviewParams (ui/render_previewer.py):
- ключи DEFAULT_TREE_SETTINGS совпадают с именами атрибутов params,
  поэтому settings_from_params/apply_settings_to_params работают
  прямым getattr/setattr-циклом;
- для apply_resolved_textures нужен маппинг "имя файла -> индекс в
  texture_images": в params держится параллельный список
  texture_filenames: list[str] (тот же порядок, что texture_images).

Атрибуты, которые репозиторий навешивает на объекты data_classes.py:
- node.db_id — id строки в nodes (и группы, и листья-Operation);
- node.name — пользовательское имя узла;
- fig.texture_slots — dict слотов {"albedo"/"normal"/"height": payload};
- fig.material_db_id — стабильный id материала (для справки);
- obj.payload_extra — неизвестные ключи JSON (forward-compat).
"""

from __future__ import annotations

import datetime
import json
from typing import Any, Dict, List, Optional

from ..render.shader_builder import SDFShaderManager
from ...defaults import DEFAULT_RENDER_SETTINGS, DEFAULT_CONTROL_SETTINGS

from ...config import log_debug
from ... import localization
from ..data_classes import (
    Figure,
    FigureHeightmap,
    FigureTexture,
    GroupOperation,
    Material,
    Operation,
    TreeObject,
)
from .db import get_connection

KIND_GROUP = "group"
KIND_FIGURE = "figure"

RANK_STEP = 1.0
RANK_MIN_GAP = 1e-9  # при меньшем зазоре между соседями siblings перенумеровываются

# Ключи = имена атрибутов RenderPreviewParams (render_previewer.py),
# значения = дефолты оттуда же.
DEFAULT_TREE_SETTINGS: Dict[str, Any] = DEFAULT_RENDER_SETTINGS

#region Serialization
import inspect
from typing import NamedTuple

from ..trajectories import trajectory_id_for  # для figure_to_payload

# Параметры __init__, которые НЕ сохраняются в БД (runtime-данные).
_RUNTIME_FIELDS: Dict[type, frozenset] = {
    Figure: frozenset({"register_id", "trajectory_fn"}),
    FigureTexture: frozenset({"id"}),
    FigureHeightmap: frozenset({"id"}),
    Material: frozenset(),
}

# Ключ payload -> имя параметра конструктора, если различаются.
_CTOR_ALIAS: Dict[type, Dict[str, str]] = {
    Figure: {"type": "figure_type"},  # атрибут .type, параметр figure_type
}


class _FieldSpec(NamedTuple):
    key: str      # ключ в JSON-payload == имя атрибута
    attr: str     # имя атрибута объекта
    ctor: str     # имя параметра конструктора
    default: Any  # дефолт из пробного экземпляра
    kind: str     # "floats" | "json" | "int" | "str" | "float"


_SPECS: Dict[type, List[_FieldSpec]] = {}


def _probe(cls):
    """Пробный экземпляр: источник дефолтов и типов. У Figure сигнатурные
    дефолты — None, реальные присваиваются в теле __init__."""
    if cls is Figure:
        return Figure(figure_type=0)
    return cls()


def _spec_for(cls) -> List[_FieldSpec]:
    """Спека сериализации класса; строится один раз и кешируется."""
    spec = _SPECS.get(cls)
    if spec is not None:
        return spec
    probe = _probe(cls)
    excluded = _RUNTIME_FIELDS.get(cls, frozenset())
    param_to_key = {ctor: key for key, ctor in _CTOR_ALIAS.get(cls, {}).items()}
    spec = []
    for param in inspect.signature(cls.__init__).parameters.values():
        if param.name == "self" or param.kind in (
            inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD,
        ):
            continue
        if param.name in excluded:
            continue
        key = param_to_key.get(param.name, param.name)
        if not hasattr(probe, key):
            log_debug(localization.get_text("repository._spec_for.attribute_missing.log_debug", class_name=cls.__name__, param_name=param.name))
            continue
        default = getattr(probe, key, None)
        if isinstance(default, list):
            kind = "floats"
        elif isinstance(default, dict):
            kind = "json"
        elif isinstance(default, (bool, int)):
            kind = "int"
        elif isinstance(default, str):
            kind = "str"
        else:
            kind = "float"
        spec.append(_FieldSpec(key=key, attr=key, ctor=param.name,
                               default=default, kind=kind))
    _SPECS[cls] = spec
    return spec


def _floats(value: Any) -> Any:
    """Рекурсивное приведение списков (extra_params — список списков vec4)."""
    if isinstance(value, (list, tuple)):
        return [_floats(v) for v in value]
    return float(value)


def _coerce(value: Any, field: _FieldSpec) -> Any:
    """Значение из JSON -> тип поля (тип берётся от дефолта)."""
    if value is None:
        value = field.default
    if field.kind == "floats":
        return _floats(value)
    if field.kind == "json":
        return dict(value)
    if field.kind == "int":
        return int(value)
    if field.kind == "str":
        return str(value)
    return float(value)


def _dump_value(value: Any) -> Any:
    if isinstance(value, (list, tuple)):
        return _floats(value)
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    return float(value)
#endregion

_TEXTURE_SLOTS = ("albedo", "normal", "height")
_LIBRARY_TABLES = ("materials", "textures")  # whitelist для f-string SQL

_META_COLUMNS = ("control_settings", "misc")  # whitelist для f-string SQL

class Repository:
    """
    Класс для работы с базой данных.
    """
    def __init__(self, conn=None):
        self.conn = conn or get_connection()

    # ------------------------------------------------------------------
    # Базовые помощники
    # ------------------------------------------------------------------
    #region
    @staticmethod
    def _now() -> str:
        """Текущее время в UTC в формате ISO с точностью до секунд."""
        return datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")

    @staticmethod
    def _dumps(payload: Any) -> str:
        """Сериализация payload в JSON."""
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _loads(text: Optional[str], default: Any) -> Any:
        """Толерантный json.loads: битый payload не роняет загрузку."""
        if not text:
            return default
        try:
            return json.loads(text)
        except (ValueError, TypeError):
            log_debug(localization.get_text("repository._loads.error.log_debug"))
            return default

    def _touch_tree(self, tree_id: int) -> None:
        """Обновляет updated_at дерева (вызывается при любой правке узлов)."""
        self.conn.execute(
            "UPDATE trees SET updated_at = ? WHERE id = ?",
            (self._now(), tree_id),
        )

    def _node_tree_id(self, node_id: int) -> int:
        """Возвращает ID дерева, к которому принадлежит узел."""
        row = self.conn.execute(
            "SELECT tree_id FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if row is None:
            raise KeyError(localization.get_text("repository._node_tree_id.error", node_id=node_id))
        return row["tree_id"]
    
    #endregion
    # ------------------------------------------------------------------
    # generic-хелпера
    # ------------------------------------------------------------------
    #region
    @staticmethod
    def _extract(obj, cls) -> Dict[str, Any]:
        """Объект -> payload по спеке. Поля-исключения (material_id у Figure,
        file у текстур) перезаписываются вызывающим кодом после."""
        return {f.key: _dump_value(getattr(obj, f.attr)) for f in _spec_for(cls)}

    @staticmethod
    def _construct(cls, payload: Dict[str, Any], extra_known: frozenset = frozenset()):
        """Толерантное чтение: известные ключи с дефолтами из пробного
        экземпляра; неизвестные возвращаются вторым значением (payload_extra)."""
        spec = _spec_for(cls)
        known = {f.key for f in spec} | set(extra_known)
        kwargs = {
            field.ctor: _coerce(payload[field.key], field)
            for field in spec if field.key in payload
        }
        obj = cls(**kwargs)
        extra = {k: v for k, v in payload.items() if k not in known}
        return obj, extra
    #endregion
    # ------------------------------------------------------------------
    # Дробное ранжирование (rank у узлов, order_index у библиотек)
    # ------------------------------------------------------------------
    #region
    @staticmethod
    def _fractional_index(existing: List[float], index: Optional[int]) -> Optional[float]:
        """Возвращает ранг для вставки на позицию index среди отсортированных
        рангов existing (None = в конец). Вставка между соседями = среднее,
        существующие строки не трогаются.

        Возвращает None, если зазор исчерпан и нужна перенумерация.
        """
        if not existing:
            return RANK_STEP
        if index is None or index >= len(existing):
            return existing[-1] + RANK_STEP
        if index <= 0:
            return existing[0] - RANK_STEP
        a, b = existing[index - 1], existing[index]
        if b - a < RANK_MIN_GAP:
            return None
        return (a + b) / 2.0

    def _node_rank_for_insert(self, tree_id: int, parent_id: Optional[int],
                              index: Optional[int]) -> float:
        """Возвращает ранг для вставки узла."""
        # parent_id IS ? — null-safe сравнение SQLite (для корня parent NULL)
        rows = self.conn.execute(
            "SELECT id, rank FROM nodes WHERE tree_id = ? AND parent_id IS ? "
            "ORDER BY rank",
            (tree_id, parent_id),
        ).fetchall()
        rank = self._fractional_index([r["rank"] for r in rows], index)
        if rank is None:
            # Редкий случай: зазоры исчерпаны — перенумеровываем siblings
            for i, r in enumerate(rows):
                self.conn.execute(
                    "UPDATE nodes SET rank = ? WHERE id = ?",
                    ((i + 1) * RANK_STEP, r["id"]),
                )
            rank = self._fractional_index(
                [(i + 1) * RANK_STEP for i in range(len(rows))], index
            )
        return rank

    def _library_order_for_insert(self, table: str, index: Optional[int]) -> float:
        """Возвращает order_index для вставки в указанную таблицу-библиотеку."""
        assert table in _LIBRARY_TABLES, localization.get_text("repository._library_order_for_insert.error", table=table)
        rows = self.conn.execute(
            f"SELECT id, order_index FROM {table} ORDER BY order_index"
        ).fetchall()
        order = self._fractional_index([r["order_index"] for r in rows], index)
        if order is None:
            for i, r in enumerate(rows):
                self.conn.execute(
                    f"UPDATE {table} SET order_index = ? WHERE id = ?",
                    ((i + 1) * RANK_STEP, r["id"]),
                )
            order = self._fractional_index(
                [(i + 1) * RANK_STEP for i in range(len(rows))], index
            )
        return order

    
    #endregion
    # ------------------------------------------------------------------
    # Гвард
    # ------------------------------------------------------------------
    #region
    def _require_not_builtin(self, table: str, row_id: int) -> None:
        """Проверяет, что row_id у table(trees|materials) не является встроенным"""
        assert table in ("trees", "materials"), localization.get_text("repository._require_not_builtin.error.unexpected_table", table=table)
        row = self.conn.execute(
            f"SELECT is_builtin FROM {table} WHERE id = ?", (row_id,)
        ).fetchone()
        if row is None:
            raise KeyError(localization.get_text("repository._require_not_builtin.error.row_not_found", table=table, row_id=row_id))
        if row["is_builtin"]:
            raise ValueError(localization.get_text("repository._require_not_builtin.error.is_builtin", table=table, row_id=row_id))

    #endregion
    # ------------------------------------------------------------------
    # Синхронизация дефолтных значений с кодом
    # ------------------------------------------------------------------
    #region
    def sync_builtin_tree(self, name: str, root: GroupOperation) -> int:
        """Пересоздаёт дефолтную сцену из кода; вызывается при каждом старте —
        встроенное дерево не переживает сессию.
        (рабочий _insert_subtree)."""
        with self.conn:
            self.conn.execute("DELETE FROM trees WHERE is_builtin = 1")
            now = self._now()
            cur = self.conn.execute(
                "INSERT INTO trees(name, created_at, updated_at, render_settings, is_builtin) "
                "VALUES (?, ?, ?, ?, 1)",
                (name, now, now, self._dumps(dict(DEFAULT_TREE_SETTINGS))),
            )
            tree_id = cur.lastrowid
            self._insert_subtree(tree_id, None, root, RANK_STEP)
        return tree_id

    def sync_builtin_materials(self, defaults: Dict[str, Material]) -> None:
        """Upsert по имени (имена — ключи DEFAULT_MATERIALS): правки дефолтов
        в коде подтягиваются при каждом старте, id стабильны — ссылки фигур
        не ломаются. Встроенные всегда в голове списка."""
        with self.conn:
            rows = self.conn.execute(
                "SELECT id, name FROM materials WHERE is_builtin = 1"
            ).fetchall()
            existing = {r["name"]: r["id"] for r in rows}
            for old_name, row_id in existing.items():
                if old_name not in defaults:  # исчезли из кода
                    self.conn.execute("DELETE FROM materials WHERE id = ?", (row_id,))
            ids_in_order: List[int] = []
            for name, mat in defaults.items():
                params = self._dumps(self.material_to_payload(mat))
                if name in existing:
                    row_id = existing[name]
                    self.conn.execute(
                        "UPDATE materials SET params = ? WHERE id = ?", (params, row_id)
                    )
                else:
                    cur = self.conn.execute(
                        "INSERT INTO materials(name, order_index, params, is_builtin) "
                        "VALUES (?, 0, ?, 1)", (name, params)
                    )
                    row_id = cur.lastrowid
                ids_in_order.append(row_id)
            user_rows = self.conn.execute(
                "SELECT id FROM materials WHERE is_builtin = 0 ORDER BY order_index"
            ).fetchall()
            ids_in_order.extend(r["id"] for r in user_rows)
            for i, row_id in enumerate(ids_in_order):
                self.conn.execute(
                    "UPDATE materials SET order_index = ? WHERE id = ?",
                    ((i + 1) * RANK_STEP, row_id),
                )
    #endregion
    # ------------------------------------------------------------------
    # Глобальные настройки
    # ------------------------------------------------------------------
    #region
    def _get_meta_column(self, column: str, defaults: Dict[str, Any]) -> Dict[str, Any]:
        """Возвращает глобальные для всего плагина данные из таблицы meta.
            При отсутствии строки возвращает дефолтные значения."""
        assert column in _META_COLUMNS, localization.get_text("repository._get_meta_column.error.unexpected_column", column=column)

        row = self.conn.execute(
            f"SELECT {column} FROM meta"
        ).fetchone()

        merged = dict(defaults)
        if row is not None:
            stored = self._loads(row[column], {})
            if isinstance(stored, dict):
                merged.update(stored)

        return merged

    def _update_meta_column(self, column: str, partial: Dict[str, Any]) -> None:
        """Обновляет глобальные для всего плагина данные в таблице meta."""
        assert column in _META_COLUMNS , localization.get_text("repository._update_meta_column.error.unexpected_column", column=column)
    
        with self.conn:
            # Защита на случай, если строку meta удалили вручную.
            # Уникальный индекс гарантирует, что второй строки не появится.
            self.conn.execute("INSERT OR IGNORE INTO meta DEFAULT VALUES")
    
            row = self.conn.execute(
                f"SELECT {column} FROM meta"
            ).fetchone()
    
            stored = self._loads(row[column], {})
            if not isinstance(stored, dict):
                stored = {}
    
            stored.update(partial)
    
            self.conn.execute(
                f"UPDATE meta SET {column} = ?",
                (self._dumps(stored),),
            )

    def get_control_settings(self) -> Dict[str, Any]:
        """Глобальные настройки управления, дополненные дефолтами."""
        return self._get_meta_column("control_settings", DEFAULT_CONTROL_SETTINGS)

    def update_control_settings(self, partial: Dict[str, Any]) -> None:
        """Merge-патч: переданные ключи обновляются, остальные сохраняются."""
        self._update_meta_column("control_settings", partial)

    def get_misc(self) -> Dict[str, Any]:
        """
        Возвращает прочие глобальные для всего плагина данные из таблицы meta.
        """
        return self._get_meta_column("misc", {})

    def update_misc(self, partial: Dict[str, Any]) -> None:
        """Обновляет прочие глобальные для всего плагина данные в таблице meta."""
        self._update_meta_column("misc", partial)
    
    #endregion
    # ------------------------------------------------------------------
    # Деревья ("Проекты")
    # ------------------------------------------------------------------
    #region
    def list_trees(self) -> List[Dict[str, Any]]:
        """Список деревьев для панели "проектов": id, name, даты, node_count."""
        rows = self.conn.execute(
            "SELECT t.id, t.name, t.created_at, t.updated_at, t.is_builtin, "
            "       (SELECT COUNT(*) FROM nodes n WHERE n.tree_id = t.id) AS node_count "
            "FROM trees t ORDER BY t.name COLLATE NOCASE"
        ).fetchall()
        return [dict(r) for r in rows]

    def create_tree(self, name: str, settings: Optional[Dict[str, Any]] = None) -> int:
        """Создаёт дерево с пустой корневой группой (Union)."""
        now = self._now()
        merged = dict(DEFAULT_TREE_SETTINGS)
        if settings:
            merged.update(settings)
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO trees(name, created_at, updated_at, render_settings) "
                "VALUES (?, ?, ?, ?)",
                (name, now, now, self._dumps(merged)),
            )
            tree_id = cur.lastrowid
            self.conn.execute(
                "INSERT INTO nodes(tree_id, parent_id, rank, kind, name, optype) "
                "VALUES (?, NULL, ?, ?, '', 0)",
                (tree_id, RANK_STEP, KIND_GROUP),
            )
        return tree_id

    def rename_tree(self, tree_id: int, name: str) -> None:
        """Переименовывает дерево."""
        self._require_not_builtin("trees", tree_id)
        with self.conn:
            self.conn.execute(
                "UPDATE trees SET name = ?, updated_at = ? WHERE id = ?",
                (name, self._now(), tree_id),
            )

    def delete_tree(self, tree_id: int) -> None:
        """Удаляет дерево и все его узлы (CASCADE по tree_id)."""
        self._require_not_builtin("trees", tree_id)
        with self.conn:
            self.conn.execute("DELETE FROM trees WHERE id = ?", (tree_id,))

    def duplicate_tree(self, tree_id: int, new_name: str) -> int:
        """Полная копия дерева вместе с настройками (аналог "Save As").

        Узлы вставляются топологическим обходом: id родителя может быть
        больше id ребёнка (после перемещений), поэтому простой ORDER BY id
        не гарантирует порядок "родитель раньше ребёнка".
        """
        tree = self.conn.execute(
            "SELECT * FROM trees WHERE id = ?", (tree_id,)
        ).fetchone()
        if tree is None:
            raise KeyError(localization.get_text("repository.duplicate_tree.error.tree_not_found", tree_id=tree_id))
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE tree_id = ? ORDER BY id", (tree_id,)
        ).fetchall()
        now = self._now()
        with self.conn:
            cur = self.conn.execute(
                "INSERT INTO trees(name, created_at, updated_at, render_settings) "
                "VALUES (?, ?, ?, ?)",
                (new_name, now, now, tree["render_settings"]),
            )
            new_tree_id = cur.lastrowid
            id_map: Dict[int, int] = {}
            pending = list(rows)
            while pending:
                progressed = False
                for r in list(pending):
                    pid = r["parent_id"]
                    if pid is None or pid in id_map:
                        cur = self.conn.execute(
                            "INSERT INTO nodes(tree_id, parent_id, rank, kind, name, "
                            "optype, operands, unaryoptype, unaryoperands, figure) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (new_tree_id, id_map.get(pid), r["rank"], r["kind"],
                             r["name"], r["optype"], r["operands"],
                             r["unaryoptype"], r["unaryoperands"], r["figure"]),
                        )
                        id_map[r["id"]] = cur.lastrowid
                        pending.remove(r)
                        progressed = True
                if not progressed:
                    raise RuntimeError(localization.get_text("repository.duplicate_tree.error.cyclic_tree", tree_id=tree_id))
        return new_tree_id
    
    #endregion
    # ------------------------------------------------------------------
    # Настройки дерева (камера + параметры рендера, индивидуальные)
    # ------------------------------------------------------------------
    #region
    def get_tree_settings(self, tree_id: int) -> Dict[str, Any]:
        """Настройки дерева, дополненные дефолтами; неизвестные ключи
        (из будущих версий) возвращаются как есть."""
        row = self.conn.execute(
            "SELECT render_settings FROM trees WHERE id = ?", (tree_id,)
        ).fetchone()
        if row is None:
            raise KeyError(localization.get_text("repository.get_tree_settings.error.tree_not_found", tree_id=tree_id))
        merged = dict(DEFAULT_TREE_SETTINGS)
        merged.update(self._loads(row["render_settings"], {}))
        return merged

    def update_tree_settings(self, tree_id: int, partial: Dict[str, Any]) -> None:
        """Merge-патч настроек: переданные ключи обновляются, остальные
        (включая неизвестные) сохраняются."""
        row = self.conn.execute(
            "SELECT render_settings FROM trees WHERE id = ?", (tree_id,)
        ).fetchone()
        if row is None:
            raise KeyError(localization.get_text("repository.update_tree_settings.error.tree_not_found", tree_id=tree_id))
        stored = self._loads(row["render_settings"], {})
        stored.update(partial)
        with self.conn:
            self.conn.execute(
                "UPDATE trees SET render_settings = ?, updated_at = ? WHERE id = ?",
                (self._dumps(stored), self._now(), tree_id),
            )

    @staticmethod
    def settings_from_params(params) -> Dict[str, Any]:
        """Снимок настроек из RenderPreviewParams для сохранения в дерево."""
        return {
            key: (list(value) if isinstance(value, (list, tuple)) else value)
            for key, value in ((k, getattr(params, k, None)) for k in DEFAULT_TREE_SETTINGS)
            if value is not None
        }

    @staticmethod
    def apply_settings_to_params(params, settings: Dict[str, Any]) -> None:
        """Применяет настройки дерева к RenderPreviewParams. Сигналы не
        эмитятся — вызывающий сам решает, когда звать emit_changed()."""
        for key, value in settings.items():
            if hasattr(params, key):
                setattr(params, key, value)

    #endregion
    # ------------------------------------------------------------------
    # Узлы: добавление / правка / перемещение / удаление / дублирование
    # ------------------------------------------------------------------
    #region
    def add_node(self, tree_id: int, parent_id: Optional[int], kind: str, *,
                 name: str = "", optype: Optional[int] = None,
                 operands: Optional[List[float]] = None,
                 unary_optype: Optional[int] = None,
                 unary_operands: Optional[List[float]] = None,
                 figure: Optional[Figure] = None,
                 texture_slots: Optional[Dict[str, Any]] = None,
                 index: Optional[int] = None) -> int:
        """Вставляет узел, затрагивая одну строку (+редкая перенумерация).

        parent_id=None создаёт корень — только если его ещё нет,
        единственность контролирует уникальный индекс idx_nodes_root.
        index — позиция среди siblings, None = в конец.
        """
        if kind not in (KIND_GROUP, KIND_FIGURE):
            raise ValueError(localization.get_text("repository.add_node.error.unknown_node_kind", kind=kind))
        if kind == KIND_FIGURE and figure is None:
            raise ValueError(localization.get_text("repository.add_node.error.missing_figure", KIND_FIGURE=KIND_FIGURE))
        if kind == KIND_GROUP and optype is None:
            optype = 0
        operands_json = self._dumps(
            [float(v) for v in operands] if operands is not None else [0.0] * 4
        )
        unary_operands_json = self._dumps(
            [float(v) for v in unary_operands] if unary_operands is not None else [0.0] * 4
        )
        figure_json = (
            self._dumps(self.figure_to_payload(figure, texture_slots))
            if figure is not None else None
        )
        with self.conn:
            rank = self._node_rank_for_insert(tree_id, parent_id, index)
            cur = self.conn.execute(
                "INSERT INTO nodes(tree_id, parent_id, rank, kind, name, optype, "
                "operands, unaryoptype, unaryoperands, figure) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (tree_id, parent_id, rank, kind, name, optype,
                 operands_json, unary_optype if unary_optype is not None else -1,
                 unary_operands_json, figure_json),
            )
            self._touch_tree(tree_id)
            return cur.lastrowid

    def update_node(self, node_id: int, *, name: Optional[str] = None,
                    optype: Optional[int] = None,
                    operands: Optional[List[float]] = None,
                    unary_optype: Optional[int] = None,
                    unary_operands: Optional[List[float]] = None,
                    figure: Optional[Figure] = None,
                    texture_slots: Optional[Dict[str, Any]] = None) -> None:
        """Точечное обновление параметров узла (одна строка).
        None-параметры не трогают соответствующие колонки.
        """
        sets: List[str] = []
        args: List[Any] = []
        if name is not None:
            sets.append("name = ?")
            args.append(name)
        if optype is not None:
            sets.append("optype = ?")
            args.append(optype)
        if operands is not None:
            sets.append("operands = ?")
            args.append(self._dumps([float(v) for v in operands]))
        if unary_optype is not None:
            sets.append("unaryoptype = ?")
            args.append(unary_optype)
        if unary_operands is not None:
            sets.append("unaryoperands = ?")
            args.append(self._dumps([float(v) for v in unary_operands]))
        if figure is not None:
            sets.append("figure = ?")
            args.append(self._dumps(self.figure_to_payload(figure, texture_slots)))
        if not sets:
            return
        tree_id = self._node_tree_id(node_id)
        args.append(node_id)
        with self.conn:
            self.conn.execute(
                f"UPDATE nodes SET {', '.join(sets)} WHERE id = ?", args
            )
            self._touch_tree(tree_id)

    def move_node(self, node_id: int, new_parent_id: int,
                  index: Optional[int] = None) -> None:
        """Перемещает узел со всем поддеревом к другому родителю (одна строка;
        дети ссылаются на узел и не затрагиваются). Перенос внутри того же
        родителя = переупорядочивание.
        """
        row = self.conn.execute(
            "SELECT tree_id, parent_id FROM nodes WHERE id = ?", (node_id,)
        ).fetchone()
        if row is None:
            raise KeyError(localization.get_text("repository.move_node.error.node_not_found", node_id=node_id))
        tree_id = row["tree_id"]
        if new_parent_id == node_id:
            raise ValueError(localization.get_text("repository.move_node.error.itself_move", node_id=node_id))
        parent = self.conn.execute(
            "SELECT tree_id FROM nodes WHERE id = ?", (new_parent_id,)
        ).fetchone()
        if parent is None:
            raise KeyError(localization.get_text("repository.move_node.error.parent_not_found", parent_id=new_parent_id))
        if parent["tree_id"] != tree_id:
            raise ValueError(localization.get_text("repository.move_node.error.cross_tree_move"))
        # Защита от перемещения узла в собственное поддерево
        ancestor: Optional[int] = new_parent_id
        while ancestor is not None:
            if ancestor == node_id:
                raise ValueError(localization.get_text("repository.move_node.error.itself_subtree_move"))
            r = self.conn.execute(
                "SELECT parent_id FROM nodes WHERE id = ?", (ancestor,)
            ).fetchone()
            ancestor = r["parent_id"] if r else None
        with self.conn:
            rank = self._node_rank_for_insert(tree_id, new_parent_id, index)
            self.conn.execute(
                "UPDATE nodes SET parent_id = ?, rank = ? WHERE id = ?",
                (new_parent_id, rank, node_id),
            )
            self._touch_tree(tree_id)

    def remove_node(self, node_id: int) -> None:
        """Удаляет узел и всё поддерево (CASCADE по parent_id).

        Внимание: удаление корня оставляет дерево пустым — следующий
        add_node(parent_id=None) создаст новый корень.
        """
        tree_id = self._node_tree_id(node_id)
        with self.conn:
            self.conn.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
            self._touch_tree(tree_id)

    def duplicate_node(self, node_id: int) -> int:
        """Глубокая копия узла/поддерева тем же родителем сразу после источника.
        Корню копии — имя + ' (copy)'. Возвращает id корня копии."""
        src = self.conn.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
        if src is None:
            raise KeyError(localization.get_text("repository.duplicate_node.error.node_not_found", node_id=node_id))
        tree_id, parent_id = src["tree_id"], src["parent_id"]
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE tree_id = ? ORDER BY id", (tree_id,)
        ).fetchall()
        # id всех потомков (BFS по parent_id)
        children_map: Dict[int, List[int]] = {}
        for r in rows:
            if r["parent_id"] is not None:
                children_map.setdefault(r["parent_id"], []).append(r["id"])
        subtree_ids, stack = set(), [node_id]
        while stack:
            cur_id = stack.pop()
            subtree_ids.add(cur_id)
            stack.extend(children_map.get(cur_id, ()))
        siblings = self.conn.execute(
            "SELECT id FROM nodes WHERE tree_id = ? AND parent_id IS ? ORDER BY rank",
            (tree_id, parent_id),
        ).fetchall()
        src_index = next(i for i, r in enumerate(siblings) if r["id"] == node_id)
        with self.conn:
            rank = self._node_rank_for_insert(tree_id, parent_id, src_index + 1)
            id_map: Dict[int, int] = {}
            pending = [r for r in rows if r["id"] in subtree_ids]
            while pending:  # топологический цикл, как в duplicate_tree
                progressed = False
                for r in list(pending):
                    is_root_copy = r["id"] == node_id
                    if not is_root_copy and r["parent_id"] not in id_map:
                        continue
                    cur = self.conn.execute(
                        "INSERT INTO nodes(tree_id, parent_id, rank, kind, name, "
                        "optype, operands, unaryoptype, unaryoperands, figure) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (tree_id,
                         parent_id if is_root_copy else id_map[r["parent_id"]],
                         rank if is_root_copy else r["rank"],
                         r["kind"],
                         (r["name"] + localization.get_text("repository.duplicate_node.name_suffix")) if (is_root_copy and r["name"]) else r["name"],
                         r["optype"], r["operands"],
                         r["unaryoptype"], r["unaryoperands"], r["figure"]),
                    )
                    id_map[r["id"]] = cur.lastrowid
                    pending.remove(r)
                    progressed = True
                if not progressed:
                    raise RuntimeError(localization.get_text("repository.duplicate_node.error.cyclic_tree", tree_id=tree_id))
            self._touch_tree(tree_id)
        return id_map[node_id]

    #endregion
    # ------------------------------------------------------------------
    # Загрузка дерева в объекты data_classes.py
    # ------------------------------------------------------------------
    #region
    def load_tree(self, tree_id: int) -> Optional[GroupOperation]:
        """Строит в памяти GroupOperation-дерево одним SELECT.

        Каждому объекту проставляется .db_id и .name; фигурам — также
        .texture_slots, .saved_extra_params, .material_db_id, .payload_extra.
        Возвращает None, если у дерева нет корня (например, его удалили).
        """
        rows = self.conn.execute(
            "SELECT * FROM nodes WHERE tree_id = ? ORDER BY rank", (tree_id,)
        ).fetchall()
        by_parent: Dict[int, List[Any]] = {}
        root_row = None
        for r in rows:
            if r["parent_id"] is None:
                root_row = r
            else:
                by_parent.setdefault(r["parent_id"], []).append(r)
        if root_row is None:
            return None
        id_to_index = self._material_id_to_index()
        return self._build_node(root_row, by_parent, id_to_index)

    def _build_node(self, row: Dict[str, Any], by_parent: Dict[int, List[Any]], id_to_index: Dict[int, int]):
        # Глобальный ORDER BY rank сохраняет относительный порядок детей
        # каждого родителя, поэтому by_parent-списки уже отсортированы.
        if row["kind"] == KIND_GROUP:
            node = GroupOperation(
                op_type=row["optype"] or 0,
                unary_op_type=row["unaryoptype"] if row["unaryoptype"] is not None else -1,
                operands=self._loads(row["operands"], [0.0] * 4),
                unary_operands=self._loads(row["unaryoperands"], [0.0] * 4),
            )
            node.db_id = row["id"]
            node.name = row["name"]
            for child_row in by_parent.get(row["id"], []):
                node.add_child(self._build_node(child_row, by_parent, id_to_index))
            return node
        fig = self.figure_from_payload(
            self._loads(row["figure"], {}), id_to_index
        )
        leaf = Operation(
            row["optype"] if row["optype"] is not None else 0,
            self._loads(row["operands"], [0.0] * 4),
            fig,
        )
        unary_optype = row["unaryoptype"] if row["unaryoptype"] is not None else -1
        unary_op = None
        if unary_optype != -1:
            unary_op = Operation(
                unary_optype,
                self._loads(row["unaryoperands"], [0.0] * 4),
            )
        wrapped = TreeObject(leaf, unary_op)
        # db_id/name дублируются на base_object: текстурная панель
        # коммитит его напрямую
        wrapped.db_id = row["id"]
        wrapped.name = row["name"]
        leaf.db_id = row["id"]
        leaf.name = row["name"]
        return wrapped

    def replace_tree(self, tree_id: int, root: GroupOperation) -> None:
        """Полная перезапись дерева из памяти. db_id объектов сохраняются:
        строки вставляются с теми же id, новые объекты получают id из БД."""
        with self.conn:
            self.conn.execute("DELETE FROM nodes WHERE tree_id = ?", (tree_id,))
            self._insert_subtree(tree_id, None, root, RANK_STEP, keep_ids=True)
            self._touch_tree(tree_id)
    
    def _insert_subtree(self, tree_id: int, parent_id: Optional[int],
                        node, rank: float, keep_ids: bool = False) -> int:
        """Вставляет поддерево. keep_ids=True: использовать node.db_id как id
        строки (None -> автоинкремент) и записывать выданный id обратно."""
        explicit_id = getattr(node, "db_id", None) if keep_ids else None
        if isinstance(node, GroupOperation):
            cur = self.conn.execute(
                "INSERT INTO nodes(id, tree_id, parent_id, rank, kind, name, optype, "
                "operands, unaryoptype, unaryoperands, figure) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)",
                (explicit_id, tree_id, parent_id, rank, KIND_GROUP,
                 getattr(node, "name", "") or "",
                 int(getattr(node, "op_type", 0)),
                 self._dumps([float(v) for v in (getattr(node, "operands", None) or [0.0] * 4)]),
                 int(getattr(node, "unary_op_type", -1)),
                 self._dumps([float(v) for v in (getattr(node, "unary_operands", None) or [0.0] * 4)])),
            )
            new_id = cur.lastrowid
            if keep_ids:
                node.db_id = new_id
            for i, child in enumerate(node.childs):
                self._insert_subtree(tree_id, new_id, child, (i + 1) * RANK_STEP, keep_ids)
            return new_id
    
        # Листья в дереве — TreeObject; голый Operation тоже принимается
        wrapper = node
        unary_op = None
        if isinstance(node, TreeObject):
            unary_op = node.unary_op
            node = node.base_object
        fig = node.figure
        # имя берём с обёртки: инспектор правит именно её, у base_object оно может быть устаревшим
        name = getattr(wrapper, "name", None) or getattr(node, "name", "") or ""
        cur = self.conn.execute(
            "INSERT INTO nodes(id, tree_id, parent_id, rank, kind, name, optype, "
            "operands, unaryoptype, unaryoperands, figure) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (explicit_id, tree_id, parent_id, rank, KIND_FIGURE, name,
             int(getattr(node, "op_type", 0)),
             self._dumps([float(v) for v in (getattr(node, "operands", None) or [0.0] * 4)]),
             int(unary_op.op_type) if unary_op is not None else -1,
             self._dumps([float(v) for v in unary_op.operands]) if unary_op is not None
             else self._dumps([0.0] * 4),
             self._dumps(self.figure_to_payload(fig)) if fig is not None else None),
        )
        new_id = cur.lastrowid
        if keep_ids:
            wrapper.db_id = new_id
            node.db_id = new_id          # base_object: его коммитит текстурная панель
            wrapper.name = name
            node.name = name
        return new_id
    
    #endregion
    # ------------------------------------------------------------------
    # Сериализация Figure
    # ------------------------------------------------------------------
    #region
    def figure_to_payload(self, fig: Figure,
                          texture_slots: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Figure -> JSON-payload.
    
        material_id (runtime-индекс в списке материалов) конвертируется
        в стабильный materials.id; текстуры сохраняются по имени файла;
        register_id, trajectory_fn не сохраняются —
        это runtime-данные (см. _RUNTIME_FIELDS); функция траектории
        восстанавливается из реестра по trajectory_fn_id.
        """
        index_to_id = self._material_index_to_id()
        mat_index = int(fig.material_id)
        material_db_id = index_to_id.get(mat_index)
        if material_db_id is None and index_to_id:
            log_debug(
                localization.get_text(
                    "repository.figure_to_payload.material_not_found.log_debug",
                    mat_index=mat_index
                )
            )
    
        payload = self._extract(fig, Figure)
        payload["material_id"] = material_db_id  # runtime-индекс перезаписан стабильным id
    
        # Страховка: объект собран вручную с функцией, но без id
        if fig.trajectory_fn is not None and not payload.get("trajectory_fn_id"):
            traj_id = trajectory_id_for(fig.trajectory_fn)
            if traj_id:
                payload["trajectory_fn_id"] = traj_id
    
        slots = texture_slots
        if slots is None:
            slots = getattr(fig, "texture_slots", None) or {}
        textures_payload = {}
        for slot_name in _TEXTURE_SLOTS:
            slot_payload = self._texture_slot_to_payload(slots.get(slot_name))
            if slot_payload is not None:
                textures_payload[slot_name] = slot_payload
        payload["textures"] = textures_payload
    
        extra = getattr(fig, "payload_extra", None)
        if extra:  # неизвестные ключи переживают цикл сохранения
            merged = dict(extra)
            merged.update(payload)
            payload = merged
        return payload

    def figure_from_payload(self, payload: Dict[str, Any],
                            id_to_index: Optional[Dict[int, int]] = None) -> Figure:
        """JSON-payload -> Figure. Толерантное чтение: дефолт на каждый ключ
        (из пробного экземпляра). trajectory_fn резолвится из реестра
        конструктором Figure. Текстуры здесь НЕ применяются: после загрузки
        вызовите apply_resolved_textures(). Мёртвая ссылка на материал ->
        индекс 0 с warning в логе."""
        if id_to_index is None:
            id_to_index = self._material_id_to_index()
        material_db_id = payload.get("material_id")
        mat_index = id_to_index.get(material_db_id)
        if mat_index is None:
            if material_db_id is not None:
                log_debug(
                    localization.get_text(
                        "repository.figure_from_payload.material_not_found.log_debug",
                        material_db_id=material_db_id
                    )
                )
            mat_index = 0

        # В payload material_id — стабильный id из БД, в конструктор уходит
        # runtime-индекс; "textures" — известный ключ, но не поле конструктора.
        patched = dict(payload)
        patched["material_id"] = mat_index
        fig, extra = self._construct(Figure, patched, extra_known=frozenset({"textures"}))
        fig.material_db_id = material_db_id
        fig.texture_slots = payload.get("textures") or {}
        fig.payload_extra = extra
        return fig

    @staticmethod
    def _texture_slot_to_payload(slot: Any) -> Optional[Dict[str, Any]]:
        """Слот текстуры -> payload. Принимает dict или FigureTexture/
        FigureHeightmap с навешанным атрибутом .filename (его проставляет UI).
        Ключи = имена атрибутов: offset, rotation, original_size, tiling_scale,
        depth_scale; единственное ручное поле — file."""
        if slot is None:
            return None
        if isinstance(slot, dict):
            return dict(slot)
        filename = getattr(slot, "filename", None) or getattr(slot, "file", None)
        if not filename:
            log_debug(localization.get_text("repository._texture_slot_to_payload.no_filename.log_debug"))
            return None
        cls = FigureHeightmap if isinstance(slot, FigureHeightmap) else FigureTexture
        payload = Repository._extract(slot, cls)
        payload["file"] = filename
        return payload

    def apply_resolved_textures(self, fig: Figure,
                                id_by_filename: Dict[str, int]) -> None:
        """Применяет к загруженной фигуре текстурные слоты: имена файлов ->
        runtime id (индексы в texture_images). Отсутствующий файл = слот
        выключен (id -1) с warning в логе — дерево при этом работает."""
        slots = getattr(fig, "texture_slots", {}) or {}
        albedo = self._slot_to_texture(slots.get("albedo"), id_by_filename, False)
        normal = self._slot_to_texture(slots.get("normal"), id_by_filename, False)
        height = self._slot_to_texture(slots.get("height"), id_by_filename, True)
        fig.node_textures = [albedo, normal, height]

    @staticmethod
    def _slot_to_texture(slot: Optional[Dict[str, Any]],
                         id_by_filename: Dict[str, int],
                         heightmap: bool):
        """payload слота -> FigureTexture/FigureHeightmap с runtime id.
        Слот без файла или с ненайденным файлом -> пустой объект (id=-1)."""
        cls = FigureHeightmap if heightmap else FigureTexture
        if not slot:
            return cls()
        filename = slot.get("file")
        tex_id = id_by_filename.get(filename)
        if tex_id is None:
            if filename:
                log_debug(localization.get_text("repository._slot_to_texture.file_not_found.log_debug", filename=filename))
            return cls()
        obj, _ = Repository._construct(cls, slot)  # "file" уходит в unknown
        obj.id = int(tex_id)
        return obj
    
    #endregion
    # ------------------------------------------------------------------
    # Материалы (глобальная библиотека)
    # ------------------------------------------------------------------
    #region
    def _material_id_to_index(self) -> Dict[int, int]:
        """Стабильный materials.id -> индекс в рантайм-списке."""
        rows = self.conn.execute(
            "SELECT id FROM materials ORDER BY order_index"
        ).fetchall()
        return {r["id"]: i for i, r in enumerate(rows)}

    def _material_index_to_id(self) -> Dict[int, int]:
        """Индекс в рантайм-списке -> стабильный materials.id."""
        rows = self.conn.execute(
            "SELECT id FROM materials ORDER BY order_index"
        ).fetchall()
        return {i: r["id"] for i, r in enumerate(rows)}

    @staticmethod
    def material_to_payload(mat: Material) -> Dict[str, Any]:
        payload = Repository._extract(mat, Material)
        extra = getattr(mat, "payload_extra", None)
        if extra:
            merged = dict(extra)
            merged.update(payload)
            payload = merged
        return payload

    @staticmethod
    def material_from_payload(payload: Dict[str, Any]) -> Material:
        """Толерантное чтение: известные ключи -> конструктор, неизвестные
        (поля будущих версий) -> атрибуты + payload_extra."""
        mat, extra = Repository._construct(Material, payload)
        for k, v in extra.items():
            setattr(mat, k, v)
        mat.payload_extra = extra
        return mat

    def list_materials(self) -> List[Dict[str, Any]]:
        """[{"id", "name", "material", "is_builtin"}], отсортировано по order_index —
        порядок совпадает с рантайм-списком params.materials. + is_builtin."""
        rows = self.conn.execute(
            "SELECT * FROM materials ORDER BY order_index"
        ).fetchall()
        return [
            {
                "id": r["id"],
                "name": r["name"],
                "material": self.material_from_payload(self._loads(r["params"], {})),
                "is_builtin": bool(r["is_builtin"]),
            }
            for r in rows
        ]

    def add_material(self, material: Material, name: str = "",
                     index: Optional[int] = None) -> int:
        with self.conn:
            order = self._library_order_for_insert("materials", index)
            cur = self.conn.execute(
                "INSERT INTO materials(name, order_index, params) VALUES (?, ?, ?)",
                (name, order, self._dumps(self.material_to_payload(material))),
            )
            return cur.lastrowid

    def update_material(self, material_id: int, *,
                        material: Optional[Material] = None,
                        name: Optional[str] = None) -> None:
        self._require_not_builtin("materials", material_id)
        sets: List[str] = []
        args: List[Any] = []
        if name is not None:
            sets.append("name = ?")
            args.append(name)
        if material is not None:
            sets.append("params = ?")
            args.append(self._dumps(self.material_to_payload(material)))
        if not sets:
            return
        args.append(material_id)
        with self.conn:
            self.conn.execute(
                f"UPDATE materials SET {', '.join(sets)} WHERE id = ?", args
            )

    def reorder_material(self, material_id: int, index: int) -> None:
        """Меняет позицию в библиотеке. Ссылки фигур не ломаются:
        они хранят стабильный id, а индекс пересчитывается при загрузке."""
        self._require_not_builtin("materials", material_id)
        with self.conn:
            order = self._library_order_for_insert("materials", index)
            self.conn.execute(
                "UPDATE materials SET order_index = ? WHERE id = ?",
                (order, material_id),
            )

    def remove_material(self, material_id: int) -> None:
        """Удаляет материал. Фигуры с мёртвой ссылкой при загрузке получат
        материал 0 с warning в логе — проверяйте is_material_in_use() в UI.
        """
        self._require_not_builtin("materials", material_id)
        with self.conn:
            self.conn.execute("DELETE FROM materials WHERE id = ?", (material_id,))

    def duplicate_material(self, material_id: int) -> int:
        """Копия материала: is_builtin=0, имя + ' (copy)'. Копия
        пользовательского материала встаёт сразу после источника, копия
        встроенного — в начало пользовательской секции (системный блок
        в голове списка не разбивается). Возвращает id копии."""
        src = self.conn.execute(
            "SELECT * FROM materials WHERE id = ?", (material_id,)
        ).fetchone()
        if src is None:
            raise KeyError(localization.get_text("repository.duplicate_material.error.material_not_found", material_id=material_id))
        rows = self.conn.execute(
            "SELECT id, is_builtin FROM materials ORDER BY order_index"
        ).fetchall()
        src_index = next(i for i, r in enumerate(rows) if r["id"] == material_id)
        if src["is_builtin"]:
            # конец системного блока = начало пользовательской секции
            insert_index = next(
                (i for i, r in enumerate(rows) if not r["is_builtin"]),
                len(rows),
            )
        else:
            insert_index = src_index + 1
        with self.conn:
            order = self._library_order_for_insert("materials", insert_index)
            cur = self.conn.execute(
                "INSERT INTO materials(name, order_index, params, is_builtin) "
                "VALUES (?, ?, ?, 0)",
                (src["name"] + localization.get_text("repository.duplicate_material.name_suffix") if src["name"] else "", order, src["params"]),
            )
            return cur.lastrowid

    def is_material_in_use(self, material_id: int) -> bool:
        mat_key = next(f.key for f in _spec_for(Figure) if f.ctor == "material_id")
        rows = self.conn.execute(
            "SELECT figure FROM nodes WHERE figure IS NOT NULL"
        ).fetchall()
        for r in rows:
            if self._loads(r["figure"], {}).get(mat_key) == material_id:
                return True
        return False
    #endregion
    # ------------------------------------------------------------------
    # Текстуры (глобальный реестр, стабильный якорь — имя файла)
    # ------------------------------------------------------------------
    #region
    def list_textures(self) -> List[Dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM textures ORDER BY order_index"
        ).fetchall()
        return [dict(r) for r in rows]

    def register_texture(self, filename: str, name: str = "") -> int:
        """Идемпотентно: повторная регистрация того же файла возвращает
        существующий id.
        """
        with self.conn:
            self.conn.execute(
                "INSERT INTO textures(filename, name, order_index) VALUES (?, ?, ?) "
                "ON CONFLICT(filename) DO NOTHING",
                (filename, name, self._library_order_for_insert("textures", None)),
            )
            row = self.conn.execute(
                "SELECT id FROM textures WHERE filename = ?", (filename,)
            ).fetchone()
            return row["id"]

    def rename_texture(self, texture_id: int, *, name: Optional[str] = None,
                       filename: Optional[str] = None) -> None:
        """
        Переименование текстуры. При этом не меняется order_index.
        """
        sets: List[str] = []
        args: List[Any] = []
        if name is not None:
            sets.append("name = ?")
            args.append(name)
        if filename is not None:
            sets.append("filename = ?")
            args.append(filename)
        if not sets:
            return
        args.append(texture_id)
        with self.conn:
            self.conn.execute(
                f"UPDATE textures SET {', '.join(sets)} WHERE id = ?", args
            )

    def reorder_texture(self, texture_id: int, index: int) -> None:
        """
        Переставить порядковый индекс текстуры."""
        with self.conn:
            order = self._library_order_for_insert("textures", index)
            self.conn.execute(
                "UPDATE textures SET order_index = ? WHERE id = ?",
                (order, texture_id),
            )

    def remove_texture(self, texture_id: int) -> None:
        """Удалить текстуру."""
        with self.conn:
            self.conn.execute("DELETE FROM textures WHERE id = ?", (texture_id,))
    #endregion
    # ------------------------------------------------------------------
    # SDF-типы (пока только встроенные; задел на пользовательские фигуры)
    # ------------------------------------------------------------------
    #region
    def sync_builtin_sdf_types(self, sdf_dict: Dict[str, str]) -> None:
        """sdf_dict: {имя: glsl} из SDFShaderManager.get_default_sdfs().

        id = позиция имени в словаре = case-индекс, который сгенерирует
        билдер. Вызывается при каждом старте: builtin восстанавливаются
        всегда (upsert), удалить их через remove_sdf_type нельзя."""
        with self.conn:
            for type_id, (name, code) in enumerate(sdf_dict.items()):
                self.conn.execute(
                    "INSERT INTO sdf_types(id, name, glsl_code, is_builtin) "
                    "VALUES (?, ?, ?, 1) "
                    "ON CONFLICT(id) DO UPDATE SET name = excluded.name, "
                    "glsl_code = excluded.glsl_code",
                    (type_id, name, code),
                )
            # Если дефолтный набор сократился — подчистить хвост builtin
            self.conn.execute(
                "DELETE FROM sdf_types WHERE is_builtin = 1 AND id >= ?",
                (len(sdf_dict),),
            )

    def list_sdf_types(self) -> List[Dict[str, Any]]:
        """
        Получить список всех SDF-типов, включая встроенные.
        """
        # ORDER BY id == порядок case в шейдере
        rows = self.conn.execute("SELECT * FROM sdf_types ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def upsert_custom_sdf_type(self, name: str, glsl_code: str) -> int:
        """id выдаём сами: следующий плотный. Возвращает назначенный id."""
        with self.conn:
            row = self.conn.execute("SELECT COALESCE(MAX(id), -1) + 1 AS nid "
                                    "FROM sdf_types").fetchone()
            type_id = int(row["nid"])
            self.conn.execute(
                "INSERT INTO sdf_types(id, name, glsl_code, is_builtin) "
                "VALUES (?, ?, ?, 0)",
                (type_id, name, glsl_code),
            )
        return type_id

    def remove_sdf_type(self, type_id: int) -> None:
        """Удалить пользовательский SDF-тип."""
        type_id = int(type_id)
        row = self.conn.execute(
            "SELECT is_builtin FROM sdf_types WHERE id = ?",
            (type_id,),
        ).fetchone()
        if row is None:
            raise KeyError(localization.get_text(
                "repository.remove_sdf_type.error.not_found", type_id=type_id
            ))
        if row["is_builtin"]:
            raise ValueError(localization.get_text(
                "repository.remove_sdf_type.error.is_builtin", type_id=type_id
            ))
        with self.conn:
            self.conn.execute(
                "DELETE FROM sdf_types WHERE id = ?",
                (type_id,),
            )
            # Держим id плотными: иначе позиция в словаре шейдера разъедется с id
            self.conn.execute(
                "UPDATE sdf_types SET id = id - 1 WHERE id > ? AND is_builtin = 0",
                (type_id,),
            )

    def sdf_dict_for_shader(self) -> Dict[str, str]:
        """Словарь для shader_manager.update_shader() / previewer.set_sdf_dict().

        Builtin гарантированы ВСЕГДА — берутся из билдера, а не из БД;
        custom дописываются следом в порядке id. Порядок ключей словаря
        == id == case-индексы в сгенерированном switch."""
        sdf = dict(SDFShaderManager.get_default_sdfs())
        custom = self.conn.execute(
            "SELECT name, glsl_code FROM sdf_types "
            "WHERE is_builtin = 0 ORDER BY id"
        ).fetchall()
        for row in custom:
            sdf[row["name"]] = row["glsl_code"]
        return sdf
    #endregion