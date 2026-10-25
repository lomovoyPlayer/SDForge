"""
SDForge: модель сцены и общие структуры данных.

Иерархия узлов: GroupOperation (группа: бинарная операция над потомками,
плюс опциональный унарный модификатор) -> TreeObject (обёртка листа:
base_object + унарный модификатор) -> Operation (операция наложения +
figure). Figure — SDF-примитив с материалом, трансформациями и
текстурными слотами.

Конвенция op_type общая с шейдером (OPERATIONSUBTYPE* в sdf_frag_high):
бинарные операции занимают 0..len(BINARY_OP_TYPE_NAMES)-1, унарные идут
следом; раскладка задаётся списками *_OP_TYPE_NAMES, числа в код не
зашивать.

Структуры Node и Figure повторяют раскладку данных, уходящих в шейдер
(см. compress_nodes_params_to_texture в renderer_mgl).
"""

from dataclasses import dataclass
from ..config import log_debug, DEBUG_LOG_ENABLED
from .trajectories import get_trajectory
from ..config import DEFAULT_TEXTURE_MAX_SIZE
from .. import localization

# Подписи типов операций (0..10) — как OPERATIONSUBTYPE* в шейдере
BINARY_OP_TYPE_NAMES = [
    "Union", "Subtract", "Intersect",
    "Smooth Union", "Smooth Subtract", "Smooth Intersect",
    "Chamber Union", "Chamber Subtract", "Chamber Intersect"]

UNARY_OP_TYPE_NAMES = [
    "Round", "Shell",
]

OP_TYPE_NAMES = BINARY_OP_TYPE_NAMES + UNARY_OP_TYPE_NAMES

UNARY_OP_TYPES = set(range(len(BINARY_OP_TYPE_NAMES), len(BINARY_OP_TYPE_NAMES) + len(UNARY_OP_TYPE_NAMES)))
BINARY_OP_TYPES = set(range(0, len(BINARY_OP_TYPE_NAMES)))

texture_array_size = float(DEFAULT_TEXTURE_MAX_SIZE)

@dataclass
class RGBA:
    r: float
    g: float
    b: float
    a: float

@dataclass
class Node:
    """
    Структура аналогичная структуре в шейдере
    """
    operation_type: int
    figure_type: int
    params: list


@dataclass
class FigureTexture:
    """
    Вспомогательная структура текстуры объекта сцены
    """
    id: int
    offset: list[float]
    rotation: float
    original_size: list[float]
    tiling_scale: float

    def __init__(self, id: int = -1, offset: list[float] = [0.0, 0.0], rotation: float = 0.0,
                 original_size: list[float] = [texture_array_size, texture_array_size], tiling_scale: float = 1.0):

        self.id = id
        self.offset = offset
        self.rotation = rotation

        if original_size is not None: # Ожидается, что рендерер ужмёт текстуру при слишком большом размере до максимального и данные не разойдутся
            w, h = original_size
            if w > texture_array_size or h > texture_array_size:
                k = min(texture_array_size / w, texture_array_size / h)
                original_size = [float(int(w * k + 0.5)), float(int(h * k + 0.5))]

        self.original_size = original_size or [1.0, 1.0]
        self.tiling_scale = tiling_scale


@dataclass
class FigureHeightmap(FigureTexture):
    """
    Расширенная структура текстуры для карты высот
    """
    depth_scale: float

    def __init__(self, id: int = -1, offset: list[float] = [0.0, 0.0], rotation: float = 0.0,
                 original_size: list[float] = [1000.0, 1000.0], tiling_scale: float = 1.0, depth_scale: float = 0.01):

        super().__init__(id, offset, rotation, original_size, tiling_scale)
        self.depth_scale = depth_scale


class Material:
    """
    Структура материала аналогичная структуре в шейдере
    """
    def __init__(
        self,
        roughness: float = 0.5,
        metallic: float = 0.0,
        transmission: float = 0.0,
        sss_color: list[float] = None,
        subsurface: float = 0.0,
        ior: float = 1.5,
        emission: float = 0.0,
    ):
        self.roughness = roughness or 0.5
        self.metallic = metallic or 0.0
        self.transmission = transmission or 0.0
        self.subsurface = subsurface or 0.0
        self.sss_color = sss_color or [1, 0.5, 0.25]
        self.ior = ior or 1.5
        self.emission = emission or 0.0

    def to_dict(self):
        return {
            "roughness": float(self.roughness),
            "metallic": float(self.metallic),
            "transmission": float(self.transmission),
            "subsurface": float(self.subsurface),
            "sssColor": self.sss_color,
            "ior": float(self.ior),
            "emission": float(self.emission),
        }


class Figure:
    """
    Класс фигуры узла сцены, которая должна быть привязана к операции
    """
    def __init__(
        self,
        figure_type: int,
        albedo: list[float] = None,
        material_id: int = 0,
        translation: list[float] = None,
        register_id: int = 0,
        rotation: list[float] = None,
        elongation: list[float] = None,
        scale: float = 1.0,
        extra_params: list[list[float]] = None,

        trajectory_fn_id: str = "",
        trajectory_params: list = None,
        trajectory_fn = None
    ):
        self.type = figure_type
        self.albedo = albedo or [1.0, 1.0, 1.0]
        self.material_id = material_id or 0
        self.translation = translation or [0.0, 0.0, 0.0]
        self.register_id = register_id or 0
        self.rotation = rotation or [0.0, 0.0, 0.0]
        self.elongation = elongation or [1.0, 1.0, 1.0]
        self.scale = scale or 0.001
        self.node_textures = [FigureTexture(), FigureTexture(), FigureHeightmap()]
        self.extra_params = extra_params or []

        self.trajectory_fn_id = trajectory_fn_id or ""
        self.trajectory_params = trajectory_params or {}
        # runtime-функция: передана явно либо резолвится из реестра по id;
        # None = без траектории (дефолт-лямбда упразднена, to_node проверяет callable)
        if trajectory_fn is None and self.trajectory_fn_id:
            spec = get_trajectory(self.trajectory_fn_id)
            if spec is None:  # мёртвая ссылка — как у материалов/текстур
                log_debug(f"SDForge: unknown trajectory {self.trajectory_fn_id!r}, ignored")
                self.trajectory_fn_id = ""
                self.trajectory_params = {}
            else:
                trajectory_fn = spec.fn
                # толерантное чтение: недостающие параметры добираются дефолтами реестра
                self.trajectory_params = {**spec.params, **self.trajectory_params}
        self.trajectory_fn = trajectory_fn

    def calculate_textures(self, albedo_texture: FigureTexture, normal_texture: FigureTexture, height_texture: FigureHeightmap)-> tuple[list[int], list[list[float]]]:
        """
        Вспомогательная функция для вычисления node_textures_id и texture_params
        """
        shift: int = 1
        albedo_shift: int
        normal_shift: int
        height_shift: int

        if albedo_texture.id >= 0:
            albedo_shift = shift
            shift += 1
        if normal_texture.id >= 0:
            normal_shift = shift
            shift += 1
        if height_texture.id >= 0:
            height_shift = shift

        node_textures_id = [-1, -1, -1]
        texture_params = []

        if albedo_texture.id >= 0:
            node_textures_id[0] = albedo_shift * 100 + albedo_texture.id
            texture_params.append([albedo_texture.offset[0], albedo_texture.offset[1], albedo_texture.rotation, 0])
            texture_params.append([albedo_texture.original_size[0], albedo_texture.original_size[1], albedo_texture.tiling_scale, 0])
        if normal_texture.id >= 0:
            node_textures_id[1] = normal_shift * 100 + normal_texture.id
            texture_params.append([normal_texture.offset[0], normal_texture.offset[1], normal_texture.rotation, 0])
            texture_params.append([normal_texture.original_size[0], normal_texture.original_size[1], normal_texture.tiling_scale, 0])
        if height_texture.id >= 0:
            node_textures_id[2] = height_shift * 100 + height_texture.id
            texture_params.append([height_texture.offset[0], height_texture.offset[1], height_texture.rotation, 0])
            texture_params.append([height_texture.original_size[0], height_texture.original_size[1], height_texture.tiling_scale, height_texture.depth_scale])

        return node_textures_id, texture_params

    def to_node(self, t:float|None = None)-> Node:
        """
        Преобразует Figure в Node
        """

        #region применение траектории
        pos: list[float] = self.translation
        rot: list[float] = self.rotation
        node_textures_id, texture_params = self.calculate_textures(self.node_textures[0], self.node_textures[1], self.node_textures[2])
        if t is not None and self.trajectory_fn is not None and callable(self.trajectory_fn):
            try:
                pos, rot = self.trajectory_fn(
                                t, self.translation, self.rotation, **self.trajectory_params
                            )
                
            except Exception as e:
                log_debug(f"Error in Figure trajectory_fn: {e}")
        #endregion

        params = [
            RGBA(self.albedo[0], self.albedo[1], self.albedo[2], float(self.material_id)),
            RGBA(pos[0], pos[1], pos[2], float(self.register_id)),
            RGBA(rot[0], rot[1], rot[2], 0.0),
            RGBA(self.elongation[0], self.elongation[1], self.elongation[2], float(self.scale)),
            RGBA(float(node_textures_id[0]), float(node_textures_id[1]), float(node_textures_id[2]), 0.0),
        ]
        for i, values in enumerate(texture_params):
            if len(values) != 4:
                log_debug(f"Warning: Figure texture_params[{i}] does not have 4 elements. Skipping.")
                continue
            params.append(RGBA(values[0], values[1], values[2], values[3]))
        for i, values in enumerate(self.extra_params):
            if len(values) != 4:
                log_debug(f"Warning: Figure extra_params[{i}] does not have 4 elements. Skipping.")
                continue
            params.append(RGBA(values[0], values[1], values[2], values[3]))
        return Node(0, self.type, params)

    def get_used_textures(self)->list[int]:
        """
        Получить список уникальных id используемых текстур
        """
        textures_ids = []
        for texture in self.node_textures:
            if(texture.id >= 0 and texture.id not in textures_ids):
                textures_ids.append(texture.id)
        return textures_ids
        

class Operation:
    """
    Класс узла-операции аналогичный логике шейдера
    """
    def __init__(self, op_type: int, operands: list[float] = None, figure: Figure = None):
        self.operands = operands or [0.0, 0.0, 0.0, 0.0]
        if len(self.operands) != 4:
            raise ValueError(localization.get_text("data_classes.operation.error.operands_count"))
        self.op_type = op_type

        self.figure = figure

    def to_node(self, t:float|None = None)-> Node:
        """
        Преобразует Operation в Node
        """
        params = [RGBA(self.operands[0], self.operands[1], self.operands[2], self.operands[3])]
        figure_type = -1
        if self.figure is not None:
            figure_type = self.figure.type if self.figure.type >= 0 else -1
            params.extend(self.figure.to_node(t).params)
        return Node(self.op_type, figure_type, params)

class TreeObject:
    """
    Особый класс, содержащий в себе бинарную операцию с фигурой и опциональную унарную операцию над фигурой
    """
    def __init__(self, base_object: "Operation", unary_op: "Operation" = None):
        if(unary_op is not None and unary_op.figure is not None):
            raise ValueError(localization.get_text("data_classes.tree_object.error.figure_must_be_none"))
        if(base_object is None or base_object.figure is None):
            raise ValueError(localization.get_text("data_classes.tree_object.error.base_object_required"))
        if(base_object.op_type not in BINARY_OP_TYPES):
            raise ValueError(localization.get_text("data_classes.tree_object.error.binary_op_type_missmatch"))
        if(unary_op is not None and unary_op.op_type not in UNARY_OP_TYPES):
            raise ValueError(localization.get_text("data_classes.tree_object.error.unary_op_type_missmatch"))
        self.base_object = base_object
        self.unary_op = unary_op

    def get_pair(self):
        """
        Возвращает пару (base_object, unary_op) как list с двумя элементами или с одним, если unary_op = None
        """
        return [self.base_object, self.unary_op] if self.unary_op is not None else [self.base_object]

class GroupOperation:
    def __init__(
        self,
        op_type: int = 0,
        unary_op_type: int = -1,
        operands: list[float] = None,
        unary_operands: list[float] = None,
        parent: "GroupOperation" = None,
        childs: list["TreeObject | GroupOperation | Operation"]  = None,
    ):
        if op_type not in BINARY_OP_TYPES:
            raise ValueError(localization.get_text("data_classes.group_operation.error.binary_op_type_missmatch", op_type=op_type))
        self.op_type = op_type
        self.operands = operands or [0.0, 0.0, 0.0, 0.0]
        if len(self.operands) != 4:
            raise ValueError(localization.get_text("data_classes.group_operation.init.error.operands_count_missmatch"))
        if unary_op_type != -1 and unary_op_type not in UNARY_OP_TYPES:
            raise ValueError(localization.get_text("data_classes.group_operation.error.unary_op_type_missmatch", unary_op_type=unary_op_type))
        self.unary_op_type = unary_op_type
        self.unary_operands = unary_operands or [0.0, 0.0, 0.0, 0.0]
        if len(self.unary_operands) != 4:
            raise ValueError(localization.get_text("data_classes.group_operation.init.error.operands_count_missmatch"))
        self.parent = parent
        
        self.childs: list["TreeObject | GroupOperation"] = []

        for child in (childs or []):
            self.add_child(child)

    #region Методы для работы с древом GroupOperation
    def get_ancestor(self):
        """
        Возвращает самого верхнего родителя в иерархии GroupOperation.
        """
        if self.parent is None:
            return self
        return self.parent.get_ancestor()

    def get_ancestors(self):
        """
        Возвращает путь от корня до текущего GroupOperation включительно.
        """
        result = []
        current = self
        while current is not None:
            result.append(current)
            current = current.parent
        return list(reversed(result))

    def get_child(self, index: int):
        """
        Возвращает дочернюю операцию по индексу.
        """
        return self.childs[index]

    def add_child(self, child: "Operation | GroupOperation | TreeObject"):
        """
        Добавляет дочернюю операцию в GroupOperation.
        """
        if isinstance(child, Operation):
            child = TreeObject(child)
        self.childs.append(child)
        if isinstance(child, GroupOperation):
            child.parent = self
        return self

    def remove_child(self, child: "Operation | GroupOperation"):
        """
        Удаляет дочернюю операцию из GroupOperation.
        """
        self.childs.remove(child)
        if isinstance(child, GroupOperation) and child.parent is self:
            child.parent = None
        return self

    def find_child(self, child: "Operation | GroupOperation"):
        """
        Находит дочернюю во всех вложенных GroupOperation.

        returns: tuple[parent, index]
        """
        for index, current_child in enumerate(self.childs):
            if current_child is child:
                return self, index

            if isinstance(current_child, GroupOperation):
                found_parent, found_index = current_child.find_child(child)
                if found_parent is not None:
                    return found_parent, found_index

        return None, -1

    #endregion

    def to_operation_sequence(
        self,
        registers_count: int = 8
    ) -> list[Operation]:
        """
        Преобразует дерево GroupOperation в плоскую последовательность Operation.

        Правила:
        1) Каждая группа выделяет регистр-аккумулятор; первый непустой ребёнок
           пишется сразу в него (INIT-операция сам с собой), остальные дети
           выделяют временный регистр правее и освобождают его после операции.
        2) Занятые регистры всегда непрерывны от нуля, поэтому аккумулятор
           родителя — это acc_register - 1.
        3) Корень не склеивается; унарный unary_op_type корня применяется ко всей сцене.
        """
        if registers_count < 2:
            raise ValueError(localization.get_text("data_classes.group_operation.to_operation_sequence.error.registers_count_missmatch", registers_count=registers_count)) # минимум один постоянный регистр и один временный регистр для вычислений

        INIT_OP_TYPE = 0 # Union

        operations: list[Operation] = []
        free_registers = list(range(registers_count))
        used_registers = []

        def validate_unary_op_type(op_type: int):
            if op_type not in UNARY_OP_TYPES:
                raise ValueError(localization.get_text("data_classes.group_operation.error.unary_op_type_missmatch", unary_op_type=op_type))

        def validate_binary_op_type(op_type: int):
            if op_type not in BINARY_OP_TYPES:
                raise ValueError(localization.get_text("data_classes.group_operation.error.binary_op_type_missmatch", op_type=op_type))

        def allocate_register() -> int:
            if len(free_registers) == 0:
                raise RuntimeError(localization.get_text("data_classes.group_operation.allocate_register.error.no_free_registers"))
            reg = free_registers.pop(0)
            used_registers.append(reg)
            used_registers.sort()
            return reg

        def free_register():
            if len(used_registers) == 0:
                raise RuntimeError(localization.get_text("data_classes.group_operation.free_register.error.no_used_registers"))
            reg = used_registers.pop()
            free_registers.append(reg)
            free_registers.sort()
            return reg

        def is_empty(item: "GroupOperation | TreeObject") -> bool:
            """Узел не даёт геометрии: лист без фигуры или группа, у которой
            все дети пустые (рекурсивно)."""
            if isinstance(item, TreeObject):
                return item.base_object.figure is None
            if isinstance(item, GroupOperation):
                return all(is_empty(c) for c in item.childs)
            return True

        def emit_expr(item: "TreeObject | GroupOperation", init_reg: int | None = None, is_root: bool = False) -> None:
            """
            Рекурсивно обходит дерево и формирует плоскую последовательность операций.
            init_reg — регистр, в который первый непустой ребёнок группы
            записывается напрямую (аккумулятор родительской группы).

            """
#region Пример: 
# A + round(B - C)
# GroupOperation (Union)
#   TreeObject: A (Union)
#   GroupOperation (Round)
#     TreeObject: B (Union)
#     TreeObject: C (Subtract)

# 1) reg 0 <- A
# 2) reg 1 <- B
# 3) reg 2 <- C
# 4) reg 1 <- (reg 1 - reg 2)
# 5) reg 1 <- round(reg 1)
# 6) reg 0 <- (reg 0 + reg 1)
#endregion
            if is_empty(item):
                return

            if isinstance(item, GroupOperation):
                validate_binary_op_type(item.op_type)
                if init_reg is not None:
                    #region Группа — первый ребёнок родителя: считаем прямо в его
                    # аккумулятор, свой регистр не выделяем, склейку не выполняем
                    acc_register = init_reg
                    owns_register = False
                    #endregion
                else:
                    acc_register = allocate_register()
                    owns_register = True

                first = True
                for child in item.childs:
                    if is_empty(child):
                        continue
                    emit_expr(child, init_reg=acc_register if first else None)
                    first = False

                if item.unary_op_type != -1:
                    validate_unary_op_type(item.unary_op_type)
                    # Если есть операция унарная, применяем её к результату группы
                    new_item = Operation(item.unary_op_type, list(item.unary_operands))
                    new_item.operands[0] = float(acc_register)
                    # new_item.operands[1] для унарной операции не используется
                    #region Пример:
                    # К этому моменту список операций уже не пустой, и acc_register -
                    # это регистр, в котором хранится результат накопления всей группы.
                    # Унарная операция записывается в тот же регистр
                    #endregion
                    operations.append(new_item)

                if owns_register and not is_root:
                    # Если группа сама выделила регистр (не первый ребёнок и не корень) — склеиваем результат с аккумулятором родителя
                    new_item = Operation(item.op_type, list(item.operands))
                    new_item.operands[0] = float(acc_register - 1) # аккумулятор родителя (инвариант п.2)
                    new_item.operands[1] = float(acc_register)     # аккумулированная группа
                    #region Пример:
                    # К этому моменту список операций уже не пустой, acc_register -
                    # регистр с результатом группы, acc_register - 1 - регистр с
                    # накопленным результатом родителя. Бинарная операция идёт над
                    # этими двумя регистрами, результат сохраняется в аккумулятор
                    # родителя, после чего регистр группы освобождается
                    #endregion
                    operations.append(new_item)
                    free_register()

            if isinstance(item, TreeObject):
                validate_binary_op_type(item.base_object.op_type)

                if init_reg is not None:
                    #region Первый непустой ребёнок группы: фигура пишется сразу в
                    # аккумулятор группы, регистр не выделяем и не освобождаем
                    alocated_register = init_reg
                    is_first = True
                    #endregion
                else:
                    alocated_register = allocate_register()
                    is_first = False
                # Для первого ребёнка a_operation = b_operation = его регистр
                # (INIT сам с собой), для остальных a_operation — аккумулятор
                # группы, он всегда на единицу левее (инвариант п.2)
                a_operation = alocated_register if is_first else alocated_register - 1
                b_operation = alocated_register

                if item.unary_op is not None:
                    # Если есть унарная операция над объектом, применяем её первой.
                    # Фигуру несёт только этот нод, базовый идёт с figure=None,
                    # чтобы SDF не считалась дважды и не затирала результат
                    validate_unary_op_type(item.unary_op.op_type)
                    new_item = Operation(item.unary_op.op_type, list(item.unary_op.operands), item.base_object.figure)
                    new_item.figure.register_id = alocated_register
                    new_item.operands[0] = float(alocated_register)
                    # new_item.operands[1] для унарной операции не используется
                    new_item.operands[2] = float(item.unary_op.operands[2])
                    new_item.operands[3] = float(item.unary_op.operands[3])
                    #region Пример:
                    # 1) Если это первый ребёнок группы, то init_reg — аккумулятор
                    # группы, унарная операция идёт над ним, результат в нём же
                    # 2) Если это не первый ребёнок, то выделяется временный регистр,
                    # унарная операция идёт над ним, далее он же будет b_operation
                    # для бинарной операции и освободится после неё
                    #endregion
                    operations.append(new_item)
                    base_figure = None
                else:
                    base_figure = item.base_object.figure
                    base_figure.register_id = alocated_register

                #region Пример:
                # 1) Первый ребёнок группы: op = INIT, a = b = его регистр,
                # union(d, d) = d — инициализация аккумулятора группы
                # 2) Не первый ребёнок: op = его бинарная операция,
                # a = аккумулятор группы, b = его временный регистр,
                # результат — в аккумулятор, временный регистр освобождается
                #endregion
                node = Operation(
                    INIT_OP_TYPE if is_first else item.base_object.op_type,
                    list(item.base_object.operands),
                    base_figure
                )
                node.operands[0] = float(a_operation)
                node.operands[1] = float(b_operation)
                operations.append(node)

                if not is_first:
                    free_register()

        emit_expr(self, is_root=True)
        return operations

    #region Подсчёт используемых текстур и материалов
    def get_used_textures(self) -> list[int]:
        """
        Возвращает отсортированный список id используемых текстур
        """
        res_raw: list[int] = []
        res: list[int] = []
        ## Рекурсивный сбор используемых текстур без проверки на уникальность
        for op in self.childs:
            if isinstance(op, TreeObject):
                op = op.base_object
            if isinstance(op, Operation) and op.figure is not None:
                res_raw.extend(op.figure.get_used_textures())
            if isinstance(op, GroupOperation):
                res_raw.extend(op.get_used_textures())

        ## Фильтр дубликатов
        for t in res_raw:
            if t not in res:
                res.append(t)
        res.sort()
        return res

    def get_used_materials(self):
        """
        Возвращает отсортированный список id используемых материалов
        """
        res_raw: list[int] = []
        res: list[int] = []
        ## Рекурсивный сбор используемых материалов без проверки на уникальность
        for op in self.childs:
            if isinstance(op, TreeObject):
                op = op.base_object
            if isinstance(op, Operation) and op.figure is not None:
                res_raw.append(op.figure.material_id)
            if isinstance(op, GroupOperation):
                res_raw.extend(op.get_used_materials())

        ## Фильтр дубликатов
        for m in res_raw:
            if m not in res:
                res.append(m)
        res.sort()
        return res

    #endregion

    #region Инструменты оптимизации через переиндексацию у ужатие диапозона используемых текстур и материалов
    @staticmethod
    def reindex_textures(operationGroup: "GroupOperation"):
        """
            Перенумерует текстуры в группе

            Возвращает Turple (reindexed_operation_group, reindexed_textures_plan)
            reindexed_operation_group - группа с перенумерованными текстурами
            reindexed_textures_plan - план перенумерованных текстур, чтобы создать новый список текстур для рендера без неиспользуемых текстур
        """
        reindexed_textures_plan: list[int] = operationGroup.get_used_textures()
        ## Т.к. список уже отсортирован - это уже готовый план перенумерованных текстур через texture.id = reindexed_textures_plan.index(texture.id)

        ## Рекурсивный по дереву; план единый для всего дерева
        def walk(group: "GroupOperation"):
            for op in group.childs:
                if isinstance(op, TreeObject):
                    op = op.base_object
                if isinstance(op, Operation) and op.figure is not None:
                    for texture in op.figure.node_textures:
                        if texture.id >= 0:  # пустой слот не перенумеровывается
                            texture.id = reindexed_textures_plan.index(texture.id)
                if isinstance(op, GroupOperation):
                    walk(op)

        walk(operationGroup)
        return operationGroup, reindexed_textures_plan

    @staticmethod
    def reindex_materials(operationGroup: "GroupOperation"):
        """
            Перенумерует материалы в группе

            Возвращает Turple (reindexed_operation_group, reindexed_material_plan)
            reindexed_operation_group - группа с перенумерованными материалами
            reindexed_material_plan - план перенумерованных материалов, чтобы создать новый список материалов для рендера без неиспользуемых материалов
        """
        reindexed_material_plan: list[int] = operationGroup.get_used_materials()
        ## Т.к. список уже отсортирован - это уже готовый план перенумерованных материалов через material_id = reindexed_material_plan.index(material_id)

        ## Рекурсивный по дереву; план единый для всего дерева
        def walk(group: "GroupOperation"):
            for op in group.childs:
                if isinstance(op, TreeObject):
                    op = op.base_object
                if isinstance(op, Operation) and op.figure is not None:
                    op.figure.material_id = reindexed_material_plan.index(op.figure.material_id)
                if isinstance(op, GroupOperation):
                    walk(op)

        walk(operationGroup)
        return operationGroup, reindexed_material_plan

    #endregion 
