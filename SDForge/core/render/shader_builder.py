import hashlib
import json
import re
from typing import Dict, List, Tuple, Optional

from ...config import log_debug
from ... import localization


class SDFShaderManager:
    """
    Собирает итоговый фрагментный шейдер из базового кода и словаря SDF-фигур.

    Словарь SDF-фигур имеет вид {имя_фигуры: glsl_код}, например:
        {
            "sphere": "return length(p) - 1.0;",
            "torus":  "...",
        }

    Так как GLSL-функция evalSDF использует целочисленный sdfType,
    менеджер нумерует фигуры по порядку их следования в словаре
    (insertion order) и генерирует switch по этим номерам.
    Соответствие имя -> номер доступно через get_sdf_index() / name_to_index.
    """

    def __init__(self, base_shader: str, fast_render_code: str = ""):
        self.base_shader = base_shader
        self.fast_render_code = fast_render_code
        self.last_hash = None
        self.cached_shader = None

        self.current_sdf_dict = self.get_default_sdfs()
        self.name_to_index: Dict[str, int] = {}
        self._rebuild_index(self.current_sdf_dict)

    # ------------------------------------------------------------------
    # Маппинг имя -> int sdfType
    # ------------------------------------------------------------------
    #region
    def _rebuild_index(self, sdf_dict: Dict[str, str]) -> None:
        """Перенумеровывает фигуры по порядку следования ключей словаря."""
        self.name_to_index = {name: i for i, name in enumerate(sdf_dict.keys())}

    def get_sdf_index(self, name: str) -> int:
        """Возвращает целочисленный sdfType для фигуры по её названию."""
        if name not in self.name_to_index:
            raise KeyError(localization.get_text("shader_builder.sdf_shader_manager.get_sdf_index.error.unknown_figure", 
                                                 name=name, available=list(self.name_to_index.keys())))
        return self.name_to_index[name]

    def get_sdf_names(self) -> List[str]:
        """Список названий всех зарегистрированных фигур (в порядке нумерации)."""
        return list(self.name_to_index.keys())

    #endregion
    # ------------------------------------------------------------------
    # Словарь фигур по умолчанию
    # ------------------------------------------------------------------
    #region
    @staticmethod
    def get_default_sdfs() -> Dict[str, str]:
        """
        Возвращает словарь SDF-фигур с именованными ключами.

        Принцип отбора: это фигуры, которые НЕЛЬЗЯ получить из других
        бинарными/унарными операциями над дистанцией (union, subtract,
        intersect, round, shell, smooth/chamfer-варианты) или вытягиванием
        (elongation). Каждая из них требует собственной формулы дистанции:

          - sphere, box, octahedron — атомарные примитивы (базис CSG,
            сами не выводимы);
          - torus         — продукт вращения окружности, вне CSG-алгебры;
          - cylinder      — elongation сферы даёт капсулу, а не цилиндр
                            с плоскими торцами;
          - cone          — не выражается через CSG над сферой/боксом;
          - ellipsoid     — масштабирование пространства даёт лишь грубую
                            оценку дистанции, нужна собственная формула;
          - hemisphere    — полусфера, не получить через CSG/elongation;
          - hex_prism, star_prism, cross_prism, tri_prism — вытягивание
                            2D-профилей, не получить из других примитивов;
          - mandelbulb    — фрактал (Escape-Time оценка дистанции);
          - tesseract     — 4D-гиперкуб, недостижим в 3D-CSG.
        """
        return {
    # --- Атомарные примитивы ---

    "sphere": """// Сфера (атомарный примитив)
float radius = 1.0;
return length(p) - radius;""",

    "box": """// Куб (атомарный примитив)
vec3 b = vec3(1.0);
vec3 q = abs(p) - b;
return length(max(q, 0.0)) + min(max(q.x, max(q.y, q.z)), 0.0);""",

    "octahedron": """// Октаэдр (атомарный примитив): вершины на осях, |x|+|y|+|z| = 1.
// Не получить вытягиванием. Классическая быстрая формула: точна внутри
// и на гранях, чуть занижена у рёбер (для реймарчинга это безопасно).
return (abs(p.x) + abs(p.y) + abs(p.z) - 1.0) * 0.57735027; // / sqrt(3)""",

    # --- Тела вращения и профильные ---

    "torus": """// Тор: продукт вращения окружности вокруг оси Y.
// Не выражается через CSG/elongation над сферой или боксом.
// R + r = 1.0: вписан в единичный куб.
vec2 t = vec2(0.75, 0.25); // большой и малый радиусы
vec2 q = vec2(length(p.xz) - t.x, p.y);
return length(q) - t.y;""",

    "cylinder": """// Цилиндр с плоскими торцами вдоль оси Y.
// Elongation сферы вдоль оси дал бы капсулу, а не плоские торцы.
// r = h = 1.0: вписан в куб, диаметр равен высоте.
float h = 1.0;  // полувысота
float r = 1.0;  // радиус
vec2 d = abs(vec2(length(p.xz), p.y)) - vec2(r, h);
return min(max(d.x, d.y), 0.0) + length(max(d, 0.0));""",

    "cone": """// Усечённый конус вдоль оси Y (r1 снизу, r2 сверху).
// Основание вписано в единичный куб.
float r1 = 1.0;
float r2 = 0.25;
float h = 1.0; // полувысота
vec2 q = vec2(length(p.xz), p.y);
vec2 k1 = vec2(r2, h);
vec2 k2 = vec2(r2 - r1, 2.0 * h);
vec2 ca = vec2(q.x - min(q.x, (q.y < 0.0) ? r1 : r2), abs(q.y) - h);
vec2 cb = q - k1 + k2 * clamp(dot(k1 - q, k2) / dot(k2, k2), 0.0, 1.0);
float s = (cb.x < 0.0 && ca.y < 0.0) ? -1.0 : 1.0;
return s * sqrt(min(dot(ca, ca), dot(cb, cb)));""",

    "ellipsoid": """// Эллипсоид с полуосями r (вписан в куб).
// Масштабирование p перед вызовом SDF сферы даёт только приближение,
// ломающееся при сильной анизотропии — поэтому своя формула (оценка iq).
vec3 r = vec3(1.0, 0.6, 0.4);
float k0 = length(p / r);
float k1 = length(p / (r * r));
return k0 * (k0 - 1.0) / k1;""",

    # --- Прочее ---

    "hemisphere": """// Полусфера
float r = 1.0; // радиус
return max(length(p) - r, -p.y);""",


    # --- Вытягивание 2D-профилей (семейство extrusion) ---

    "hex_prism": """// Шестиугольная призма вдоль оси Y (атомарный примитив, iq).
// Шестиугольное сечение не получить вытягиванием других примитивов.
const vec3 k = vec3(-0.8660254, 0.5, 0.57735); // -sqrt(3)/2, 1/2, 1/sqrt(3)
vec2 h = vec2(0.9, 1.0); // радиус шестиугольника, полувысота
vec3 a = abs(p);
vec2 q = a.xz - 2.0 * min(dot(k.xy, a.xz), 0.0) * k.xy;
vec2 d = vec2(length(q - vec2(clamp(q.x, -k.z * h.x, k.z * h.x), h.x))
                  * sign(q.y - h.x),
              a.y - h.y);
return min(max(d.x, d.y), 0.0) + length(max(d, 0.0));""",

    "star_prism": """// Звёздная призма: вытягивание 2D-пятиконечной звезды
// (формула iq sdStar5, инлайн) вдоль оси Y. Вписана: радиус 0.95, высота 0.7.
vec2 sp = p.xz;
const vec2 k1 = vec2(0.809016994, -0.587785252);
const vec2 k2 = vec2(-0.809016994, -0.587785252);
sp.x = abs(sp.x);
sp -= 2.0 * max(dot(k1, sp), 0.0) * k1;
sp -= 2.0 * max(dot(k2, sp), 0.0) * k2;
sp.x = abs(sp.x);
sp.y -= 0.95;                       // внешний радиус звезды
vec2 ba = 0.45 * vec2(0.587785252, 0.809016994) - vec2(0.0, 1.0); // rf=0.45
float hh = clamp(dot(sp, ba) / dot(ba, ba), 0.0, 0.95);
float d2d = length(sp - ba * hh) * sign(sp.y * ba.x - sp.x * ba.y);
// Экструзия профиля (стандартная формула opExtrusion)
vec2 w = vec2(d2d, abs(p.y) - 0.35);
return min(max(w.x, w.y), 0.0) + length(max(w, 0.0));""",

    "cross_prism": """// Крестовая призма: вытягивание 2D-креста (iq sdCross, инлайн)
// вдоль оси Y. Углы слегка скруглены. Вписана точно: 0.95 + 0.05 = 1.0.
vec2 cp = abs(p.xz);
cp = (cp.y > cp.x) ? cp.yx : cp;
vec2 b = vec2(0.95, 0.30);          // полудлина и полуширина луча
vec2 cq = cp - b;
float k = max(cq.y, cq.x);
vec2 cw = (k > 0.0) ? cq : vec2(b.y - cp.x, -k);
float d2d = sign(k) * length(max(cw, 0.0)) + 0.05;
vec2 w = vec2(d2d, abs(p.y) - 1.0); // по высоте — весь куб
return min(max(w.x, w.y), 0.0) + length(max(w, 0.0));""",

    "tri_prism": """// Треугольная призма: вытягивание равностороннего треугольника
// (iq sdEquilateralTriangle, инлайн) вдоль оси Y.
// Вписана: описанный радиус 0.85, высота 1.8.
vec2 tp = p.xz;
const float k = 1.7320508;          // sqrt(3)
float r = 0.85;                     // радиус описанной окружности
tp.x = abs(tp.x) - r;
tp.y = tp.y + r / k;
if (tp.x + k * tp.y > 0.0) tp = vec2(tp.x - k * tp.y, -k * tp.x - tp.y) / 2.0;
tp.x -= clamp(tp.x, -2.0 * r, 0.0);
float d2d = -length(tp) * sign(tp.y);
vec2 w = vec2(d2d, abs(p.y) - 0.9);
return min(max(w.x, w.y), 0.0) + length(max(w, 0.0));""",

    # --- Фрактальные ---

    "mandelbulb": """// Мандельбулб: 3D-фрактал, Escape-Time оценка дистанции.
// Принципиально итеративная фигура, вне CSG-алгебры.
// Масштаб 1.15 вписывает фрактал в единичный куб.
vec3 q = p * 1.15;
float power = 8.0;
vec3 z = q;
float dr = 1.0;
float r = 0.0;
for (int i = 0; i < 8; i++) {
    r = length(z);
    if (r > 2.0) break;
    float theta = acos(clamp(z.z / r, -1.0, 1.0)) * power;
    float phi = atan(z.y, z.x) * power;
    dr = pow(r, power - 1.0) * power * dr + 1.0;
    float zr = pow(r, power);
    z = zr * vec3(sin(theta) * cos(phi),
                  sin(theta) * sin(phi),
                  cos(theta)) + q;
}
return 0.5 * log(r) * r / dr / 1.15;""",

    # --- Зависящая от времени ---

    "tesseract": """// Тессеракт: сечение 4D-гиперкуба гиперплоскостью w = 0.
// Пример фигуры, зависящей от времени. Гиперкуб вращается сразу в ДВУХ
// плоскостях (XW и ZW) с некратными скоростями, поэтому сечение непрерывно
// перетекает: куб -> скошенные призмы -> октаэдро-подобные формы.
// Сечение всегда проходит через центр — фигура НИКОГДА не исчезает.
float t1 = u_time * 0.40;
float t2 = u_time * 0.23;
vec4 p4 = vec4(p, 0.0);
float c1 = cos(t1), s1 = sin(t1);
float c2 = cos(t2), s2 = sin(t2);
p4.xw = mat2(c1, -s1, s1, c1) * p4.xw;  // вращение в плоскости XW
p4.zw = mat2(c2, -s2, s2, c2) * p4.zw;  // вращение в плоскости ZW
vec4 q = abs(p4) - vec4(0.85);
float d = length(max(q, 0.0)) + min(max(q.x, max(q.y, max(q.z, q.w))), 0.0);
return d - 0.05;  // лёгкое скругление рёбер — приятнее под светом""",
}

    #endregion
    # ------------------------------------------------------------------
    # Обновление и сборка шейдера
    # ------------------------------------------------------------------
    #region
    def update_shader(self, sdf_dict: Optional[Dict[str, str]] = None) -> Tuple[bool, str]:
        """
        Обновляет и возвращает шейдер, если словарь функций изменился.
        Возвращает (Изменился_ли_шейдер, Итоговый_код_шейдера).
        """
        if sdf_dict is not None:
            self._validate_sdf_dict(sdf_dict)
            self.current_sdf_dict = sdf_dict
            self._rebuild_index(sdf_dict)

        state = {
            "sdf": self.current_sdf_dict,
            "fast_code": self.fast_render_code
        }
        state_str = json.dumps(state, sort_keys=True, ensure_ascii=False)
        current_hash = hashlib.md5(state_str.encode('utf-8')).hexdigest()

        if current_hash == self.last_hash and self.cached_shader:
            return False, self.cached_shader

        self.last_hash = current_hash
        self.cached_shader = self._build_shader(self.current_sdf_dict)

        return True, self.cached_shader

    @staticmethod
    def _validate_sdf_dict(sdf_dict: Dict[str, str]) -> None:
        """Проверяет, что ключи — непустые строковые названия, а значения —
        GLSL-строки, содержащие return."""
        for name, code in sdf_dict.items():
            if not isinstance(name, str) or not name.strip():
                raise ValueError(localization.get_text("shader_builder.sdf_shader_manager._validate_sdf_dict.error.invalid_name", name=name))
            if not isinstance(code, str) or "return" not in code:
                raise ValueError(localization.get_text("shader_builder.sdf_shader_manager._validate_sdf_dict.error.invalid_code", name=name))

    def _build_shader(self, sdf_dict: Dict[str, str]) -> str:
        # 1. Генерируем тело функции evalSDF.
        #    Числовые case-метки берём из name_to_index (порядок словаря),
        #    имя оставляем в комментарии для читаемости сгенерированного кода.
        eval_sdf_lines = [
            "float evalSDF(vec3 p, int sdfType, int paramsStart) {",
            "    switch(sdfType) {"
        ]

        for name, code in sdf_dict.items():
            sdf_type = self.name_to_index[name]
            eval_sdf_lines.append(f"    case {sdf_type}: {{ // {name}")
            indented_code = "\n".join(
                [f"        {line}" for line in code.strip().split("\n")]
            )
            eval_sdf_lines.append(indented_code)
            eval_sdf_lines.append("    }")

        eval_sdf_lines.append("    default:")
        eval_sdf_lines.append("        return 1000.0;")
        eval_sdf_lines.append("    }")
        eval_sdf_lines.append("}")

        new_eval_sdf = "\n".join(eval_sdf_lines)
        result_shader = self.base_shader

        # 2. Заменяем предварительное объявление на полноценную функцию
        # Ищем строку: float evalSDF(vec3 p, int sdfType, int paramsStart);
        declaration_pattern = (
            r'float\s+evalSDF\s*\(\s*vec3\s+p\s*,\s*int\s+sdfType\s*,'
            r'\s*int\s+paramsStart\s*\)\s*;'
        )

        if re.search(declaration_pattern, result_shader):
            result_shader = re.sub(declaration_pattern, new_eval_sdf, result_shader)
        else:
            # Если объявления вдруг нет, добавляем в конец файла
            result_shader += f"\n\n{new_eval_sdf}\n"

        # 3. Подставляем fast_render_code
        placeholder = "// ___FAST_RENDER_CODE___"
        if self.fast_render_code:
            if placeholder in result_shader:
                result_shader = result_shader.replace(placeholder, self.fast_render_code)
            else:
                log_debug(localization.get_text("shader_builder._build_shader.fast_placeholder_missing.log_debug", placeholder=placeholder))

        return result_shader

    #endregion

# Пример использования:
# manager = SDFShaderManager(base_shader=код_из_файла)
# is_updated, shader_code = manager.update_shader()
#
# # Целочисленный sdfType для ноды сцены теперь берём по имени:
# node.figureType = manager.get_sdf_index("torus")
#
# # Свой набор фигур (ключи — названия, порядок задаёт нумерацию):
# manager.update_shader({
#     "sphere": "return length(p) - 1.0;",
#     "torus":  "vec2 q = vec2(length(p.xz) - 1.0, p.y); return length(q) - 0.25;",
# })
