# Руководство по forms.py

Библиотека полей ввода (`ui/widgets/forms.py`): привязки к данным,
опциональная живая синхронизация, 9 типов полей + декларативные спеки.

---

## Часть 0. Ментальная модель (прочитать один раз)

Поле формы — это **не виджет со своим значением**, а «окно» в данные:

```
значение живёт в объекте (params / узел / материал / dict)
        ↑  get()                    ↓ set() + commit()
     Binding  ——————————————————  Binding
        ↑  refresh() (сверка)     ↓ _emit() (правка пользователя)
     FormField (спинбокс, комбо, свотч...)
```

Три вывода из этой модели:

1. **Синхронизация = сверка.** Данные ссылочные, поэтому «подтянуть
   изменения» — это просто перечитать источник и сравнить с виджетом.
   Обновляем виджет только при расхождении — ввод не дёргается.
2. **Привязка = два направления + коммит.** Любое поле задаётся вопросом:
   «откуда читать, куда писать, что вызвать после записи».
3. **Коммит — снаружи.** forms.py не знает про контроллер и БД.
   `commit` — это ваша функция (`controller.commit_node`,
   `params.emit_changed` и т.п.).

### Три вопроса перед любой работой

| Вопрос | Варианты ответа |
|---|---|
| 1. Где живут данные? | атрибут стабильного объекта (`params`) / атрибут пересоздаваемого объекта (узел, материал) / элемент списка или словаря (`operands[2]`, `payload["rotation"]`) |
| 2. Что является коммитом? | `params.emit_changed` (настройки) / `controller.commit_node(узел)` / `controller.commit_material(id, ...)` / свой метод |
| 3. Меняются ли данные мимо этой формы? | да (камера и трансформации — мышь во вьюпорте) → `Form(sync=True)`; нет → `sync=False` (умолчание) |

---

## Часть А. Захожу в существующий виджет: что менять, добавлять, удалять

### А0. Сначала классифицируйте: это вообще форма?

- **Форма** (поля ввода, редактирующие данные): inspector, render
  settings, редактор материалов, ряды текстурных слотов → переносим.
- **Не форма** (список, дерево, диалог-галерея): scene_tree_panel,
  projects_panel, TexturePickerDialog → **не трогаем**; встраиваем их
  в формы целиком через `form.add_widget(...)`, как TextureSlotsWidget.

### А1. Найдите «красные флаги» ручной механики

Поиск по файлу — всё найденное будет удалено или заменено:

| Паттерн для поиска | Что это | Замена |
|---|---|---|
| `_loading`, `_guard` | защита от рекурсии сигналов | внутри FormField |
| `blockSignals` | то же | внутри FormField (`_silence`) |
| `valueChanged.connect(lambda ... setter ...)` | ручная запись в данные | привязка (Binding) |
| циклы `setValue`/`setChecked`/`setText` | ручное заполнение виджетов | `form.refresh()` / живая сверка |
| `_labeled(...)`, `_add_*_field` | хелперы построения рядов | `form.add_*` |
| `style_color_button`, `add_axis_labels` в панели | стилизация | внутри полей |
| ручная очистка layout в rebuild | перестройка формы | `form.clear()` |

### А2. Инвентаризация полей

Выпишите каждое поле панели строкой: `подпись | где данные | тип виджета`.
По таблице выберите `add_*`:

| Тип данных в модели | Метод формы | Примечание |
|---|---|---|
| строка | `add_text` | коммит по Enter/потере фокуса |
| float | `add_float` | `minimum/maximum/step/decimals/suffix` |
| int | `add_int` | то же, целые |
| float 0..1 (материалы) | `add_slider` | слайдер + точный спинбокс |
| углы, «крутилка» | `add_dial` | `steps=N` — скорость драга |
| bool | `add_bool` | чекбокс |
| enum / id / индекс | `add_combo(label, items)` | items = [(подпись, данные)] |
| цвет [r,g,b] | `add_color` | `title=` для диалога |
| [x,y,z] / [x,y] | `add_vec3` / `add_vec2` | метки осей встроены |
| секция-заголовок | `add_header` | |
| чужой виджет | `add_widget` / `add_labeled` | без сверки |

### А3. Привязки: выбор конструкции

```
данные — атрибут? ── да ── объект стабилен (params)? ── да ── AttrBinding(params, "attr", commit=...)
                 │                              └── нет ── AttrBinding(provider, "attr", ...)  # provider = lambda/метод
                 └── нет ── Binding(get, set_, commit=...)   # список/dict/конверсия единиц
```

- **Provider** (метод `self._node()`, `lambda: self._material`) обязателен
  для объектов, которые контроллер пересоздаёт: захваченная ссылка
  протухает после add/move/remove. Симптом: правки «перестают
  сохраняться» после структурных изменений.
- **`transform=list`** добавляйте для списков (vec3, цвет): поле получает
  копию, а не живой list — иначе сравнение в сверке будет с самим собой.
- **Ручной Binding** — для всего, что не `getattr`:

```python
# элемент списка
Binding(get=lambda: (list(node.operands) + [0.0]*4)[2],
        set_=..., commit=...)
# dict-payload с конверсией единиц (радианы <-> градусы)
Binding(get=lambda: math.degrees(payload.get("rotation", 0.0)),
        set_=lambda v: payload.__setitem__("rotation", math.radians(v)),
        commit=self.commit)
```

### А4. Скелет класса

```python
# __init__:
self.form = Form(self, sync=<True если мышь во вьюпорте трогает эти данные>)
# либо, если класс — QScrollArea: self.setWidget(self.form)
controller.<сигнал_смены_контекста>.connect(lambda _id: self.rebuild())  # как было

# rebuild (было: ручная чистка layout + построение виджетов):
def rebuild(self):
    self.form.clear()
    ...поля через self.form.add_*...
```

Правило: **один проход по старому коду = одна строка `add_*`**.
Если старый метод после переноса схлопнулся в одну строку — удалите метод.

### А5. Связи между полями и динамические списки

- Зависимость «поле активно только при значении другого»:
  ```python
  smooth.setEnabled(node.op_type >= 3)                       # начальное
  op_field.changed.connect(lambda v: smooth.setEnabled(v >= 3))
  ```
  Ручной `setEnabled` сверка не затирает (она трогает enabled только при
  смене валидности данных).
- Динамический список в комбо (материалы, типы фигур): сохраните поле
  (`self._mat_field = form.add_combo(...)`), по сигналу —
  `self._mat_field.set_items(new_items)`; оберните в try/except
  RuntimeError (форму могли перестроить).

### А6. Чеклист удалений (после переноса)

- [ ] `_loading`, `_guard`, все `blockSignals`
- [ ] методы-коммиты (`_commit_name` и т.п.) — их роль у commit в привязке
- [ ] обработчики `valueChanged/currentIndexChanged/toggled` — их роль у _emit поля
- [ ] методы заполнения виджетов (`_fill_editor`, `refresh` с setValue)
- [ ] хелперы построения рядов (`_labeled`, `_add_*`)
- [ ] лишние импорты: `QColor`, `Qt`, `ui_style`-хелперы, `typing.List`
- [ ] **проверьте лямбды со старыми ссылками**: если `self._node` стал
  методом-provider'ом, старый `commit_node(self._node)` передаёт метод!
  Заменить на `commit=self._commit`.

### А7. Чеклист проверки

1. Форма строится, значения совпадают с данными.
2. Правка каждого поля доезжает до данных и в БД/рендер.
3. sync=True: внешние изменения (мышь) появляются за ~150 мс; поле под
   фокусом не дёргается.
4. Перестройка (смена выделения/дерева) — без NameError/AttributeError;
   provider'ы возвращают актуальные объекты.
5. Динамические списки обновляются по сигналам без rebuild.

---

## Часть Б. Добавляю с нуля

### Б1. Новое поле в существующую форму — 2 строки

```python
self.form.add_float("Масштаб:",
                    AttrBinding(self._figure, "scale", commit=self._commit),
                    minimum=0.001, maximum=1000.0, step=0.01)
```

Порядок мысли: какой тип виджета (таблица А2) → где данные (схема А3) →
какой commit. Всё.

### Б2. Новая панель с нуля — шаблон

```python
from PyQt5 import QtWidgets
from .forms import Form, AttrBinding

class MyPanel(QtWidgets.QWidget):
    def __init__(self, params, controller, parent=None):
        super().__init__(parent)
        self.params, self.controller = params, controller

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.form = Form(self, sync=False)   # sync=True, если данные двигает мышь
        layout.addWidget(self.form)

        self.form.add_header("Секция")
        self.form.add_float("Параметр:",
            AttrBinding(self.params, "my_value", commit=params.emit_changed),
            minimum=0.0, maximum=10.0, step=0.05)
```

Декларативный вариант для однотипных панелей — см. `MATERIAL_SPECS` в
materials_panel: список `FieldSpec(kind, label, attr, ...)` +
`form.add_specs(specs, target=..., commit=...)`.

### Б3. Новый ТИП поля (наследник FormField)

Контракт — 4 метода:

```python
class PathField(FormField):
    """Строка пути + кнопка обзора."""

    def _build(self, row):                    # 1. создать виджеты
        self.edit = QtWidgets.QLineEdit()
        btn = QtWidgets.QPushButton("…")
        btn.setFixedWidth(28)
        btn.clicked.connect(self._browse)
        self.edit.editingFinished.connect(
            lambda: self._emit(self.edit.text()))
        row.addWidget(self.edit, 1)
        row.addWidget(btn)

    def _browse(self):
        path = QtWidgets.QFileDialog.getOpenFileName(self, "Файл")[0]
        if path:
            self.set_value(path)              # показать
            self._emit(path)                  # записать + commit + changed

    def value(self):                          # 2. прочитать из виджета
        return self.edit.text()

    def set_value(self, v):                   # 3. записать без эмита
        with self._silence(self.edit):
            self.edit.setText(v or "")

    def focus_widgets(self):                  # 4. чей фокус = «редактируется»
        return [self.edit]
```

Бесплатно получаете: сверку, `syncable`, enabled по доступности данных,
сигнал `changed`. Опционально: `_values_differ` (если `!=` не подходит —
float'ы сравнивайте с порогом `_half_step(decimals)`), `is_editing`
(если «занятость» — не только фокус, см. `isSliderDown` у слайдера).

### Б4. Жёсткие правила (на них уже наступали)

1. **Qt-правило инициализации**: в `__init__` наследника НЕЛЬЗЯ
   обращаться к `self` (ни `self.x = ...`, ни `getattr(self, ...)`) до
   `super().__init__()`. Конфигурацию — через `_store_range(...)`
   (он вызывается до super) или атрибуты класса (`_count = 3`).
   Нарушение → `RuntimeError: super-class __init__() was never called`.
2. **Не захватывайте пересоздаваемые объекты** — только provider'ы.
3. **`transform=list` для списков** — иначе сверка сравнивает виджет
   с тем же объектом, который виджет и показывает.
4. **Программное обновление виджетов — только через `_silence`**
   (или готовый `set_value`), иначе рекурсия сигналов.
5. **Поля, удалённые без form.clear()**, сверка прощает (RuntimeError
   ловится в FormSync), но правильно — всегда перестраивать через clear().
6. **commit не должен перестраивать форму**, которой принадлежит поле,
   иначе правка сотрёт виджет под рукой. Структурные изменения — только
   через сигналы контроллера → rebuild.

---

## Шпаргалка по API

| Приём | Код |
|---|---|
| Форма с живой сверкой | `Form(self, sync=True, sync_interval_ms=150)` |
| Вкл/выкл сверку в рантайме | `form.sync.set_enabled(False)` |
| Разовая сверка | `form.refresh()` (напр. по `tree_loaded`) |
| Сверка по сигналу | `form.sync.attach_signal(params.changed)` |
| Перестройка | `form.clear()` + add_* (состояние sync сохраняется) |
| Поле без сверки | `add_text(..., syncable=False)` |
| Реакция на правку пользователя | `field.changed.connect(lambda v: ...)` |
| Перезаполнить комбо | `field.set_items([(подпись, данные), ...])` |
| Скорость драга диала | `add_dial(..., steps=100)` (меньше = быстрее) |
| Колесо мыши у диала | `field.knob.setPageStep(10)` |
| Доступ к спинбоксу поля | `field.spin` / `field.knob` / `field.combo` / `field.edit` |
