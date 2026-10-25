"""
SDForge: слой базы данных (SQLite).

Файл БД: sdforge.db в каталоге данных из config.DATA_DIR (путь собирается
в config.DB_FILE).

Принципы:
- схема версионируется через meta.schema_version, миграции выполняются
  цепочкой 1 -> 2 -> ... -> N, поэтому обновление через несколько версий
  работает, но не обратная совместимость не поддерживается;
- параметры объектов (фигуры, материалы, настройки дерева) хранятся
  JSON-payload, поэтому добавление/удаление полей классов данных НЕ
  требует миграций — толерантное чтение реализовано в repository.py;
- структура дерева — adjacency list (parent_id) + дробный rank,
  изменения узлов затрагивают только их собственные строки;
- id удалённых записей никогда не переиспользуются (AUTOINCREMENT).
"""

import os
import sqlite3
from ... import localization

from ...config import DB_FILE, log_debug

SCHEMA_VERSION = 2

_SCHEMA_V1 = """
-- Глобальные настройки
CREATE TABLE meta (
    key   TEXT PRIMARY KEY, -- В _SCHEMA_V2 это колонка будет удалена т.к. в этой таблице всего одна запись
    control_settings  TEXT NOT NULL DEFAULT '{}', -- JSON Общие настройки управления для всего плагина
    misc TEXT NOT NULL DEFAULT '{}' -- JSON Другие настройки и состояния, которые надо сохранять между сессиями
);

-- Глобальная библиотека материалов
CREATE TABLE materials (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL DEFAULT '', -- Имя материала, хранится только в базе данных и интерфейсе, а для внутренней логики не существует
    order_index REAL NOT NULL, -- Дробное индексирование, чтобы не перечитывать весь список при каждом обновлении
    params      TEXT NOT NULL DEFAULT '{}', -- JSON список параметров материала
    is_builtin  INTEGER NOT NULL DEFAULT 0 -- Флаг, что материал является встроенным, предустановленным в SDForge
);

-- Реестр текстур: стабильный якорь — имя файла, а не runtime id
CREATE TABLE textures (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    filename    TEXT NOT NULL UNIQUE, -- Путь к файлу внутри самой Krita, текстуры берутся только из ресурсов самой Krita
    name        TEXT NOT NULL DEFAULT '', -- Имя текстуры внутри самой Krita
    order_index REAL NOT NULL -- Дробное индексирование, чтобы не перечитывать весь список при каждом обновлении
);

-- SDF-типы фигур (id == figuretype). Задел на пользовательские фигуры.
CREATE TABLE sdf_types (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL, -- Имя фигуры, в отличии от материалов названия берутся из кода
    glsl_code  TEXT NOT NULL, -- GLSL код фигуры для встраивания в шейдер
    is_builtin INTEGER NOT NULL DEFAULT 0 -- Флаг, что тип является встроенным, предустановленным в SDForge
);

-- Деревья = "проекты". Камера и параметры рендера — индивидуальные, JSON.
CREATE TABLE trees (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    name       TEXT NOT NULL UNIQUE, -- Имя проекта, хранится только в базе данных и интерфейсе
    created_at TEXT NOT NULL, -- Дата создания проекта
    updated_at TEXT NOT NULL, -- Дата последнего обновления проекта
    render_settings   TEXT NOT NULL DEFAULT '{}', -- JSON Параметры рендера и сцены
    is_builtin INTEGER NOT NULL DEFAULT 0 -- Флаг, что дерево является встроенным, предустановленным в SDForge
);

-- Узлы дерева. kind без CHECK намеренно: SQLite не умеет ALTER CONSTRAINT,
-- валидация значений выполняется в репозитории.
CREATE TABLE nodes (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    tree_id   INTEGER NOT NULL REFERENCES trees(id) ON DELETE CASCADE,  -- id дерева которому принадлежит узел, при удалении дерева узлы удаляются
    parent_id INTEGER REFERENCES nodes(id) ON DELETE CASCADE,           -- NULL = корень
    rank      REAL NOT NULL,                                            -- порядок среди siblings (дробный, чтобы не перечитывать весь список при каждом обновлении)
    kind      TEXT NOT NULL,                                            -- 'group' | 'figure'
    name      TEXT NOT NULL DEFAULT '',                                 -- Имя узла, хранится только в базе данных и интерфейсе, а для внутренней логики не существует
    optype    INTEGER,                                                  -- Бинарная операция: как склеить группу/как наложить лист(узел без наследника) 
                                                                                                                                -- с уже посчитанной частью сцены
    operands  TEXT NOT NULL DEFAULT '[0,0,0,0]',                        -- JSON vec4 бинарной операции
    unaryoptype   INTEGER NOT NULL DEFAULT -1,                          -- Унарный модификатор (-1 = нет)
    unaryoperands TEXT NOT NULL DEFAULT '[0,0,0,0]',                    -- JSON vec4 унарного модификатора
    figure    TEXT                                                      -- JSON фигуры, если kind == 'figure', т.е. если к операции привязана фигура
);

CREATE INDEX idx_nodes_tree ON nodes(tree_id, parent_id, rank); 
CREATE UNIQUE INDEX idx_nodes_root ON nodes(tree_id) WHERE parent_id IS NULL; 
CREATE UNIQUE INDEX idx_materials_builtin_name ON materials(name) WHERE is_builtin = 1;
"""

_SCHEMA_V2 = """
-- Общие настройки плагина: ровно одна строка, без key/id.
-- Содержимое старой meta сознательно сбрасываем - на момент миграции формы изменения настроек управления ещё не создано, 
-- а сохранением соотношения виджетов в окне можно пренебречь.
DROP TABLE meta;

CREATE TABLE meta (
    control_settings TEXT NOT NULL DEFAULT '{}',
    misc TEXT NOT NULL DEFAULT '{}'
);

CREATE UNIQUE INDEX idx_meta_singleton ON meta ((1));

INSERT INTO meta DEFAULT VALUES;
"""

# Номер версии -> SQL-скрипт миграции.
# Старые скрипты не редактируем: существующая БД проходит только новые версии. На текущий момент не обратная совместимость не поддерживается.
_MIGRATIONS = {
    1: _SCHEMA_V1,
    2: _SCHEMA_V2,
}

def get_schema_version(conn: sqlite3.Connection) -> int:
    """
    Возвращает номер текущей версии схемы БД.
    """
    return int(conn.execute("PRAGMA user_version").fetchone()[0])

def migrate(conn: sqlite3.Connection):
    """
    Миграция базы данных в соответствии с текущей версией, от старой к новой, но не наоборот.
    """
    if conn.in_transaction:
        raise RuntimeError(localization.get_text("db.migrate.conn.in_transaction.error"))

    version = get_schema_version(conn)

    if version > SCHEMA_VERSION:
        raise RuntimeError(
            localization.get_text("db.migrate.version.error", version=version, SCHEMA_VERSION=SCHEMA_VERSION)
        )

    # Циклическая миграция от текущей к новейшей версии, проходя по всем версиям последовательно.
    while version < SCHEMA_VERSION:
        target_version = version + 1

        # Получаем скрипт миграции БД от version до version + 1.
        script = _MIGRATIONS[target_version]

        log_debug(localization.get_text("db.migrate.migrating_schema.log_debug", target_version=target_version))

        # executescript() не наследует транзакцию от `with conn`.
        # Поэтому BEGIN, вся миграция, user_version и COMMIT — один скрипт.
        try:
            conn.executescript(
                "BEGIN IMMEDIATE;\n"
                + script
                + f"\nPRAGMA user_version = {target_version};\n"
                + "COMMIT;"
            )
        except Exception:
            conn.rollback()
            raise

        version = target_version

def connect(db_path:str=None):
    """Создаёт новое подключение и применяет миграции."""
    path = db_path or DB_FILE
    if path != ":memory:":
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    migrate(conn)
    return conn

_connection = None

def get_connection():
    """Общее подключение плагина к БД."""
    global _connection
    if _connection is None:
        _connection = connect()
    return _connection


def close_connection():
    """Закрывает подключение плагина к БД."""
    global _connection
    if _connection is not None:
        try:
            _connection.close()
        finally:
            _connection = None
