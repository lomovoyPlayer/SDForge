"""
Утилиты для 3D-графики (PyQt5 / OpenGL / SDF raymarching)

Особенности:
- Без numpy, только math + PyQt5
- Совместимо с камерой raymarching вида:
      uv = localPos / resolution.y
      rayDir = normalize(vec3(uv, -1.0))
- Вращения камеры и объектов хранятся как Euler XYZ в радианах,
  как и в шейдере с u_camRot / NODE_ROTATION.

Содержит:
- Преобразования систем координат
- Векторные утилиты
- Кватернионы
- Матрицы для шейдеров
- Функции траектории
- 7 задач управления камерой/объектом(ами)
"""

import math
from PyQt5.QtGui import QVector3D, QVector4D, QQuaternion, QMatrix4x4

from .. import localization


EPS = 1e-12
PI = math.pi
RAD_TO_DEG = 180.0 / math.pi
DEG_TO_RAD = math.pi / 180.0


# ══════════════════════════════════════════════════════════════
#  БАЗОВЫЕ ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ══════════════════════════════════════════════════════════════
#region
def _clamp(x, a, b):
    return max(a, min(b, x))

def _to_vec3(v):
    if isinstance(v, QVector3D):
        return QVector3D(v.x(), v.y(), v.z())
    return QVector3D(float(v[0]), float(v[1]), float(v[2]))

def _vec3_add(a, b):
    a = _to_vec3(a)
    b = _to_vec3(b)
    return QVector3D(a.x() + b.x(), a.y() + b.y(), a.z() + b.z())

def _vec3_sub(a, b):
    a = _to_vec3(a)
    b = _to_vec3(b)
    return QVector3D(a.x() - b.x(), a.y() - b.y(), a.z() - b.z())

def _vec3_scale(v, s):
    v = _to_vec3(v)
    s = float(s)
    return QVector3D(v.x() * s, v.y() * s, v.z() * s)

def _vec3_length(v):
    v = _to_vec3(v)
    return math.sqrt(v.x() * v.x() + v.y() * v.y() + v.z() * v.z())

def _vec3_normalize(v):
    v = _to_vec3(v)
    n = _vec3_length(v)
    if n < EPS:
        return QVector3D(0.0, 0.0, 0.0)
    inv = 1.0 / n
    return QVector3D(v.x() * inv, v.y() * inv, v.z() * inv)

def _dot(a, b):
    return QVector3D.dotProduct(_to_vec3(a), _to_vec3(b))

def _cross(a, b):
    return QVector3D.crossProduct(_to_vec3(a), _to_vec3(b))

def vec3(x=0.0, y=0.0, z=0.0):
    return QVector3D(float(x), float(y), float(z))

def vec3_to_tuple(v):
    v = _to_vec3(v)
    return (v.x(), v.y(), v.z())

def vec3_from_tuple(v):
    return _to_vec3(v)

def quat_to_tuple(q):
    return (q.scalar(), q.x(), q.y(), q.z())

def _row4_to_tuple(v4):
    return (v4.x(), v4.y(), v4.z(), v4.w())

#endregion

# ══════════════════════════════════════════════════════════════
#  ПРЕОБРАЗОВАНИЯ СИСТЕМ КООРДИНАТ
# ══════════════════════════════════════════════════════════════
#region
def cartesian_to_polar(x, y):
    """Декартова (x, y) → полярная (r, theta)."""
    r = math.sqrt(x * x + y * y)
    theta = math.atan2(y, x)
    return r, theta

def polar_to_cartesian(r, theta):
    """Полярная (r, theta) → декартова (x, y)."""
    x = r * math.cos(theta)
    y = r * math.sin(theta)
    return x, y

def cartesian_to_spherical(x, y, z):
    """Декартова (x, y, z) → сферическая (r, theta, phi).

    Конвенция:
      r     — радиус
      theta — азимут в плоскости xy от оси X
      phi   — угол от +Z
    """
    r = math.sqrt(x * x + y * y + z * z)
    theta = math.atan2(y, x)
    phi = math.atan2(math.sqrt(x * x + y * y), z)
    return r, theta, phi

def spherical_to_cartesian(r, theta, phi):
    """Сферическая (r, theta, phi) → декартова (x, y, z)."""
    x = r * math.sin(phi) * math.cos(theta)
    y = r * math.sin(phi) * math.sin(theta)
    z = r * math.cos(phi)
    return x, y, z

#endregion
# ══════════════════════════════════════════════════════════════
#  ROTATE / EULER В СТИЛЕ ШЕЙДЕРА
# ══════════════════════════════════════════════════════════════
#region
def rotate2(x, y, angle):
    """Поворот 2D-вектора на угол angle (радианы)."""
    c = math.cos(angle)
    s = math.sin(angle)
    return x * c - y * s, x * s + y * c

def rotate_vec3_euler(v, angles):
    """Поворот 3D-вектора в стиле шейдера.

    Порядок такой же, как в GLSL-функции rotate3D:
      1) вокруг X: поворот yz
      2) вокруг Y: поворот xz
      3) вокруг Z: поворот xy

    angles = (x, y, z) в радианах.
    """
    v = _to_vec3(v)
    angles = _to_vec3(angles)

    x, y, z = v.x(), v.y(), v.z()

    y, z = rotate2(y, z, angles.x())
    x, z = rotate2(x, z, angles.y())
    x, y = rotate2(x, y, angles.z())

    return QVector3D(x, y, z)

def inverse_rotate_vec3_euler(v, angles):
    """Обратный поворот 3D-вектора в стиле шейдера."""
    v = _to_vec3(v)
    angles = _to_vec3(angles)

    x, y, z = v.x(), v.y(), v.z()

    x, y = rotate2(x, y, -angles.z())
    x, z = rotate2(x, z, -angles.y())
    y, z = rotate2(y, z, -angles.x())

    return QVector3D(x, y, z)

#endregion
# ══════════════════════════════════════════════════════════════
#  КВАТЕРНИОНЫ
# ══════════════════════════════════════════════════════════════
#region
def quat_normalize(q):
    n = math.sqrt(q.scalar() * q.scalar() + q.x() * q.x() + q.y() * q.y() + q.z() * q.z())
    if n < EPS:
        return QQuaternion(1.0, 0.0, 0.0, 0.0)
    inv = 1.0 / n
    return QQuaternion(q.scalar() * inv, q.x() * inv, q.y() * inv, q.z() * inv)

def quat_conjugate(q):
    return QQuaternion(q.scalar(), -q.x(), -q.y(), -q.z())

def quat_mul(q1, q2):
    w1, x1, y1, z1 = q1.scalar(), q1.x(), q1.y(), q1.z()
    w2, x2, y2, z2 = q2.scalar(), q2.x(), q2.y(), q2.z()
    return QQuaternion(
        w1 * w2 - x1 * x2 - y1 * y2 - z1 * z2,
        w1 * x2 + x1 * w2 + y1 * z2 - z1 * y2,
        w1 * y2 - x1 * z2 + y1 * w2 + z1 * x2,
        w1 * z2 + x1 * y2 - y1 * x2 + z1 * w2,
    )

def axis_angle_to_quat(axis, angle):
    axis = _vec3_normalize(axis)
    if _vec3_length(axis) < EPS:
        return QQuaternion(1.0, 0.0, 0.0, 0.0)
    half = angle * 0.5
    s = math.sin(half)
    return QQuaternion(
        math.cos(half),
        axis.x() * s,
        axis.y() * s,
        axis.z() * s,
    )

def quat_rotate_vector(q, v):
    return quat_normalize(q).rotatedVector(_to_vec3(v))

def euler_to_quat(pitch, yaw, roll):
    """Euler XYZ (радианы) → кватернион.

    Композиция совпадает с порядком вращений шейдера:
    X, затем Y, затем Z.
    """
    qx = axis_angle_to_quat(vec3(1.0, 0.0, 0.0), pitch)
    qy = axis_angle_to_quat(vec3(0.0, 1.0, 0.0), yaw)
    qz = axis_angle_to_quat(vec3(0.0, 0.0, 1.0), roll)
    return quat_normalize(quat_mul(qz, quat_mul(qy, qx)))

def quat_to_euler(q):
    """Кватернион → Euler XYZ (pitch, yaw, roll) в радианах."""
    q = quat_normalize(q)
    w, x, y, z = q.scalar(), q.x(), q.y(), q.z()

    sin_yaw = _clamp(2.0 * (w * y - z * x), -1.0, 1.0)
    yaw = math.asin(sin_yaw)

    roll = math.atan2(
        2.0 * (w * z + x * y),
        1.0 - 2.0 * (y * y + z * z)
    )

    pitch = math.atan2(
        2.0 * (w * x + y * z),
        1.0 - 2.0 * (x * x + y * y)
    )

    return pitch, yaw, roll

def euler_vec_to_quat(rotation):
    rotation = _to_vec3(rotation)
    return euler_to_quat(rotation.x(), rotation.y(), rotation.z())

def quat_to_euler_vec(q):
    p, y, r = quat_to_euler(q)
    return QVector3D(p, y, r)

#endregion
# ══════════════════════════════════════════════════════════════
#  МАТРИЦЫ ДЛЯ ШЕЙДЕРОВ / OPENGL
# ══════════════════════════════════════════════════════════════
#region
def translate_matrix(t):
    t = _to_vec3(t)
    m = QMatrix4x4()
    m.setToIdentity()
    m.translate(t)
    return m

def scale_matrix(s):
    m = QMatrix4x4()
    m.setToIdentity()
    if isinstance(s, (int, float)):
        m.scale(float(s))
    else:
        s = _to_vec3(s)
        m.scale(s.x(), s.y(), s.z())
    return m

def look_at(eye, target, up):
    eye = _to_vec3(eye)
    target = _to_vec3(target)
    up = _to_vec3(up)
    m = QMatrix4x4()
    m.setToIdentity()
    m.lookAt(eye, target, up)
    return m

def perspective(fov_y, aspect, near, far):
    """Перспективная матрица. fov_y в радианах."""
    m = QMatrix4x4()
    m.setToIdentity()
    m.perspective(fov_y * RAD_TO_DEG, float(aspect), float(near), float(far))
    return m

def view_matrix_from_pose(position, rotation_euler):
    """Матрица вида из позиции и Euler-поворота камеры."""
    position = _to_vec3(position)
    right, up, forward = camera_basis_vectors(rotation_euler)

    right = _vec3_normalize(right)
    up = _vec3_normalize(up)
    forward = _vec3_normalize(forward)

    return QMatrix4x4(
        right.x(),    right.y(),    right.z(),    -_dot(right, position),
        up.x(),       up.y(),       up.z(),       -_dot(up, position),
        -forward.x(), -forward.y(), -forward.z(),  _dot(forward, position),
        0.0,          0.0,          0.0,           1.0,
    )

def to_gl_uniform(m):
    """QMatrix4x4 → flat list[16] в column-major порядке."""
    r0 = _row4_to_tuple(m.row(0))
    r1 = _row4_to_tuple(m.row(1))
    r2 = _row4_to_tuple(m.row(2))
    r3 = _row4_to_tuple(m.row(3))
    rows = (r0, r1, r2, r3)
    return [
        rows[0][0], rows[1][0], rows[2][0], rows[3][0],
        rows[0][1], rows[1][1], rows[2][1], rows[3][1],
        rows[0][2], rows[1][2], rows[2][2], rows[3][2],
        rows[0][3], rows[1][3], rows[2][3], rows[3][3],
    ]

#endregion
# ══════════════════════════════════════════════════════════════
#  КАМЕРА В СТИЛЕ ШЕЙДЕРА
# ══════════════════════════════════════════════════════════════
#region
def shader_plane_half_extents(viewport_width, viewport_height):
    """Полуразмеры виртуальной screen plane для камеры из шейдера.

    Для формулы:
        uv = localPos / resolution.y
        rayDir = normalize(vec3(uv, -1.0))

    получаем:
        plane_half_height = 0.5
        plane_half_width  = viewport_width / (2 * viewport_height)
    """
    viewport_width = float(viewport_width)
    viewport_height = float(viewport_height)
    return viewport_width / (2.0 * viewport_height), 0.5

def camera_basis_vectors(camera_rotation):
    """Вернуть right, up, forward камеры в мировом пространстве.

    camera_rotation — Euler XYZ в радианах.
    Локальный взгляд камеры направлен вдоль -Z.
    """
    camera_rotation = _to_vec3(camera_rotation)
    right = rotate_vec3_euler(vec3(1.0, 0.0, 0.0), camera_rotation)
    up = rotate_vec3_euler(vec3(0.0, 1.0, 0.0), camera_rotation)
    forward = rotate_vec3_euler(vec3(0.0, 0.0, -1.0), camera_rotation)
    return _vec3_normalize(right), _vec3_normalize(up), _vec3_normalize(forward)

def camera_ray_direction_from_uv(uv_x, uv_y, camera_rotation):
    """Направление луча камеры из uv в стиле шейдера."""
    local_dir = _vec3_normalize(vec3(uv_x, uv_y, -1.0))
    return _vec3_normalize(rotate_vec3_euler(local_dir, camera_rotation))

def camera_ray_direction_from_pixel(px, py, viewport_width, viewport_height,
                                    camera_rotation, preview_scale=1.0):
    """Направление луча по пикселю, полностью повторяя идею камеры из шейдера.

    px, py — координаты пикселя внутри viewport.
    preview_scale — аналог uniform u_previewScale.
    """
    frag_x = float(px) * float(preview_scale)
    frag_y = float(py) * float(preview_scale)
    res_x = float(viewport_width) * float(preview_scale)
    res_y = float(viewport_height) * float(preview_scale)

    local_x = frag_x - 0.5 * res_x
    local_y = frag_y - 0.5 * res_y

    uv_x = local_x / res_y
    uv_y = local_y / res_y

    return camera_ray_direction_from_uv(uv_x, uv_y, camera_rotation)

def world_units_per_pixel(depth, viewport_height, fov_deg):
    """Сколько мировых единиц соответствует одному пикселю по вертикали.

    Модель камеры:
    - vertical FOV задаётся явно через fov_deg
    - высота видимой плоскости на глубине depth равна:
      2 * depth * tan(fov / 2)
    - один пиксель по Y — это эта высота, делённая на viewport_height

    Параметры:
    depth — расстояние до рабочей плоскости вдоль направления взгляда
    viewport_height — высота области рендера в пикселях
    fov_deg — vertical FOV в градусах

    Возвращает:
    world units / pixel
    """
    depth = max(1e-6, float(depth))
    viewport_height = max(1.0, float(viewport_height))
    fov_rad = math.radians(float(fov_deg))
    return (2.0 * depth * math.tan(0.5 * fov_rad)) / viewport_height

#endregion
# ══════════════════════════════════════════════════════════════
# ГРУППА ФИГУР: ОБЩИЙ ЦЕНТР И ПРЯМЫЕ ТРАНСФОРМАЦИИ
# ══════════════════════════════════════════════════════════════
#region
def figures_center(positions, weights=None):
    """Общий центр координат позиций группы фигур.

    По умолчанию — среднее арифметическое позиций
    (центр масс при равных весах фигур).
    Если передать weights (например, массы фигур), получится
    взвешенный центр масс.

    Параметры:
    positions — список позиций (QVector3D или tuple/list[3])
    weights — необязательные веса фигур той же длины

    Возвращает QVector3D. Для пустого списка — (0, 0, 0).
    """
    positions = list(positions)
    if not positions:
        return QVector3D(0.0, 0.0, 0.0)

    if weights is None:
        weights = [1.0] * len(positions)
    else:
        weights = [float(w) for w in weights]
        if len(weights) != len(positions):
            raise ValueError(localization.get_text("math_utils.figures_center.error.weights_count_missmatch") )

    total = sum(weights)
    if abs(total) < EPS:
        weights = [1.0] * len(positions)
        total = float(len(positions))

    sx = sy = sz = 0.0
    for p, w in zip(positions, weights):
        p = _to_vec3(p)
        sx += p.x() * w
        sy += p.y() * w
        sz += p.z() * w

    inv = 1.0 / total
    return QVector3D(sx * inv, sy * inv, sz * inv)

def translate_figures(positions, delta):
    """Прямая трансформация: сдвинуть всю группу на один вектор.

    positions — список позиций фигур
    delta — мировой вектор смещения (QVector3D или tuple/list[3])

    Возвращает новый список позиций (list[QVector3D]).
    Входной список не изменяется.
    """
    delta = _to_vec3(delta)
    return [_vec3_add(p, delta) for p in positions]

def rotate_positions_around_point(positions, center, delta_quat):
    """Прямая трансформация: повернуть позиции вокруг точки.

    Каждая позиция p преобразуется как:
        p' = center + delta_quat * (p - center)

    Параметры:
    positions — список позиций
    center — точка, вокруг которой выполняется поворот
    delta_quat — кватернион поворота в мировом пространстве

    Возвращает новый список позиций (list[QVector3D]).
    """
    center = _to_vec3(center)
    dq = quat_normalize(delta_quat)

    result = []
    for p in positions:
        local = _vec3_sub(p, center)
        rotated = quat_rotate_vector(dq, local)
        result.append(_vec3_add(center, rotated))
    return result

def rotate_figures_around_point(positions, rotations, center, delta_quat):
    """Прямая трансформация: повернуть группу фигур вокруг точки.

    Группа поворачивается как твёрдое тело:
    - каждая позиция поворачивается вокруг center:
        p' = center + delta_quat * (p - center)
    - каждая фигура доворачивается на тот же мировой поворот:
        q' = delta_quat * q
      поэтому ориентация фигур меняется вместе с группой

    Параметры:
    positions — список позиций фигур
    rotations — список Euler XYZ поворотов фигур в радианах;
                если None, поворачиваются только позиции
    center — точка, вокруг которой выполняется поворот
    delta_quat — кватернион поворота в мировом пространстве

    Возвращает (new_positions, new_rotations):
    new_positions — list[QVector3D]
    new_rotations — list[QVector3D] Euler XYZ в радианах
                    (None, если rotations был None)
    """
    new_positions = rotate_positions_around_point(positions, center, delta_quat)

    if rotations is None:
        return new_positions, None

    dq = quat_normalize(delta_quat)
    new_rotations = []
    for r in rotations:
        q = euler_vec_to_quat(r)
        new_q = quat_normalize(quat_mul(dq, q))
        new_rotations.append(quat_to_euler_vec(new_q))

    return new_positions, new_rotations

def rotate_figures_around_point_euler(positions, rotations, center, delta_euler):
    """То же, что rotate_figures_around_point, но поворот задан Euler XYZ.

    delta_euler — Euler XYZ в радианах в мировом пространстве.
    """
    return rotate_figures_around_point(
        positions, rotations, center, euler_vec_to_quat(delta_euler)
    )

#endregion
# ══════════════════════════════════════════════════════════════
#  ЗАДАЧА 1: МЫШЬ → ПОВОРОТ КАМЕРЫ
# ══════════════════════════════════════════════════════════════
#region
def mouse_to_camera_rotation(dx, dy, camera_rotation,
                             sensitivity=0.005,
                             pitch_limit=math.pi * 0.499):
    """Перевести движение мыши в поворот камеры без завала по roll.

    Поведение в стиле FPS / editor camera:
    dx → yaw вокруг мировой вертикали
    dy → pitch вокруг локальной горизонтали камеры

    ВАЖНО:
    Эта версия сознательно НЕ накапливает roll.
    Значение roll всегда принудительно равно 0.0, поэтому камера
    не "заваливается" при диагональном движении мыши.

    Параметры:
    dx, dy — смещение мыши в пикселях виджета
    camera_rotation — текущий Euler XYZ камеры в радианах
    sensitivity — чувствительность мыши
    pitch_limit — ограничение pitch, чтобы не упираться в сингулярности

    Возвращает новый Euler XYZ в радианах (QVector3D).
    """
    camera_rotation = _to_vec3(camera_rotation)

    pitch = camera_rotation.x() - dy * sensitivity
    yaw = camera_rotation.y() + dx * sensitivity

    pitch = _clamp(pitch, -float(pitch_limit), float(pitch_limit))
    roll = 0.0

    return QVector3D(pitch, yaw, roll)

def drag_to_camera_rotation(total_dx, total_dy, start_camera_rotation,
                            sensitivity=0.005,
                            pitch_limit=math.pi * 0.499):
    """Поворот камеры от начала drag без накопления roll.

    Логика:
    - total_dx / total_dy считаются от точки нажатия мыши
    - yaw и pitch меняются относительно стартового Euler
    - roll всегда принудительно равен 0.0

    Это устраняет:
    - завал камеры по диагонали
    - зависимость результата от числа mouseMoveEvent
    - накопление ошибки из-за Euler -> quat -> Euler на каждом шаге

    Возвращает новый Euler XYZ в радианах (QVector3D).
    """
    start_camera_rotation = _to_vec3(start_camera_rotation)

    pitch = start_camera_rotation.x() - total_dy * sensitivity
    yaw = start_camera_rotation.y() + total_dx * sensitivity
    pitch = _clamp(pitch, -float(pitch_limit), float(pitch_limit))

    return QVector3D(pitch, yaw, 0.0)

#endregion
# ══════════════════════════════════════════════════════════════
#  ЗАДАЧА 2: МЫШЬ → ПОВОРОТ ОБЪЕКТА
# ══════════════════════════════════════════════════════════════
#region
def mouse_to_object_rotation(dx, dy, camera_rotation, object_rotation,
                             sensitivity=0.005):
    """Интуитивный поворот объекта по экранным осям.

    Оси вращения параллельны плоскости рендера:
      dx → вокруг vertical screen axis  (camera up)
      dy → вокруг horizontal screen axis (camera right)

    Возвращает новый Euler XYZ в радианах (QVector3D).
    """
    camera_rotation = _to_vec3(camera_rotation)
    object_rotation = _to_vec3(object_rotation)

    right, up, _ = camera_basis_vectors(camera_rotation)
    current_q = euler_vec_to_quat(object_rotation)

    rot_vertical = axis_angle_to_quat(up, dx * sensitivity)
    rot_horizontal = axis_angle_to_quat(right, -dy * sensitivity)

    delta_world = quat_mul(rot_vertical, rot_horizontal)
    new_q = quat_normalize(quat_mul(delta_world, current_q))
    return quat_to_euler_vec(new_q)

def drag_to_object_rotation(total_dx, total_dy,
                            drag_camera_rotation,
                            start_object_rotation,
                            sensitivity=0.005):
    """Интуитивный screen-space поворот объекта от начала drag.

    ВАЖНО:
    - total_dx / total_dy считаются от точки нажатия мыши
    - оси экрана берутся из camera_rotation в момент начала drag
    - дельта вращения применяется к стартовому повороту объекта,
      а не к уже обновлённому на предыдущем mouseMoveEvent

    Это устраняет накопление "кренов" и делает результат
    стабильным при диагональном движении мыши.

    Возвращает новый Euler XYZ в радианах (QVector3D).
    """
    drag_camera_rotation = _to_vec3(drag_camera_rotation)
    start_object_rotation = _to_vec3(start_object_rotation)

    right, up, _ = camera_basis_vectors(drag_camera_rotation)
    start_q = euler_vec_to_quat(start_object_rotation)

    rot_vertical = axis_angle_to_quat(up, total_dx * sensitivity)
    rot_horizontal = axis_angle_to_quat(right, -total_dy * sensitivity)

    delta_world = quat_mul(rot_vertical, rot_horizontal)
    new_q = quat_normalize(quat_mul(delta_world, start_q))

    return quat_to_euler_vec(new_q)

def drag_to_object_rotation_3axis(start_mouse_pos, current_mouse_pos,
                                  object_screen_center,
                                  drag_camera_rotation,
                                  start_object_rotation,
                                  rotate_sensitivity=0.005,
                                  twist_sensitivity=1.0):
    """Трёхосевой screen-space поворот объекта.

    Компоненты:
    - horizontal drag -> вращение вокруг camera up
    - vertical drag -> вращение вокруг camera right
    - орбитальное движение курсора вокруг screen-center объекта ->
      вращение вокруг camera forward
    """
    drag_camera_rotation = _to_vec3(drag_camera_rotation)
    start_object_rotation = _to_vec3(start_object_rotation)

    total_dx = float(current_mouse_pos.x() - start_mouse_pos.x())
    total_dy = float(current_mouse_pos.y() - start_mouse_pos.y())

    right, up, forward = camera_basis_vectors(drag_camera_rotation)
    start_q = euler_vec_to_quat(start_object_rotation)

    rot_vertical = axis_angle_to_quat(up, total_dx * rotate_sensitivity)
    rot_horizontal = axis_angle_to_quat(right, -total_dy * rotate_sensitivity)

    sx = float(start_mouse_pos.x() - object_screen_center.x())
    sy = float(start_mouse_pos.y() - object_screen_center.y())
    cx = float(current_mouse_pos.x() - object_screen_center.x())
    cy = float(current_mouse_pos.y() - object_screen_center.y())

    start_len2 = sx * sx + sy * sy
    curr_len2 = cx * cx + cy * cy

    twist_angle = 0.0
    min_radius2 = 25.0

    if start_len2 > min_radius2 and curr_len2 > min_radius2:
        start_angle = math.atan2(sy, sx)
        curr_angle = math.atan2(cy, cx)
        twist_angle = curr_angle - start_angle

        while twist_angle > math.pi:
            twist_angle -= 2.0 * math.pi
        while twist_angle < -math.pi:
            twist_angle += 2.0 * math.pi

    rot_twist = axis_angle_to_quat(forward, -twist_angle * twist_sensitivity)

    delta_world = quat_mul(rot_twist, quat_mul(rot_vertical, rot_horizontal))
    new_q = quat_normalize(quat_mul(delta_world, start_q))
    return quat_to_euler_vec(new_q)

#endregion
# ══════════════════════════════════════════════════════════════
#  ЗАДАЧА 3: КОЛЁСИКО → DOLLY КАМЕРЫ
# ══════════════════════════════════════════════════════════════
#region
def wheel_to_camera_dolly(wheel_delta, camera_position, camera_rotation,
                          speed=0.1, wheel_step=120.0):
    """Переместить камеру вдоль направления взгляда.

    wheel_delta обычно берётся из wheelEvent.angleDelta().y().
    В Qt один стандартный щелчок колеса часто равен 120.

    speed — мировых единиц на один wheel_step.
    """
    camera_position = _to_vec3(camera_position)
    _, _, forward = camera_basis_vectors(camera_rotation)

    amount = float(wheel_delta) / float(wheel_step)
    return _vec3_add(camera_position, _vec3_scale(forward, amount * speed))

#endregion
# ══════════════════════════════════════════════════════════════
#  ЗАДАЧА 4: МЫШЬ → ПЕРЕМЕЩЕНИЕ ОБЪЕКТА
# ══════════════════════════════════════════════════════════════
#region
def mouse_to_object_translation(dx, dy, camera_rotation, object_position,
                                camera_position, viewport_width,
                                viewport_height, fov_deg,
                                sensitivity=1.0):
    """Перемещение объекта в плоскости экрана под FOV-камеру.

    Объект двигается параллельно экрану:
    dx → вдоль camera right
    dy → вдоль camera up

    ВАЖНО:
    Эта функция НЕ использует preview_scale и НЕ пускает лучи на CPU.
    Она повторяет геометрию новой камеры через vertical FOV и глубину
    объекта относительно камеры.

    Логика такая:
    - берём вектор от камеры до объекта
    - проецируем его на направление взгляда камеры
    - получаем depth — глубину объекта вдоль forward
    - по этой глубине считаем world_units_per_pixel(...)
    - превращаем экранный сдвиг мыши в world-space offset по right/up

    Почему depth считается через dot(to_obj, forward):
    - нам нужна не евклидова дистанция до объекта,
      а именно расстояние до плоскости, перпендикулярной взгляду камеры
    - это даёт правильный screen-space масштаб при перспективе

    Параметры:
    dx, dy — смещение мыши в пикселях виджета
    camera_rotation — Euler XYZ камеры в радианах
    object_position — позиция объекта в мире
    camera_position — позиция камеры в мире
    viewport_width — ширина области рендера в пикселях
    viewport_height — высота области рендера в пикселях
    fov_deg — vertical FOV камеры в градусах
    sensitivity — дополнительный множитель

    Возвращает новую позицию объекта (QVector3D).
    """
    object_position = _to_vec3(object_position)
    camera_position = _to_vec3(camera_position)

    right, up, forward = camera_basis_vectors(camera_rotation)

    to_obj = _vec3_sub(object_position, camera_position)
    depth = _dot(to_obj, forward)
    if depth < 1e-6:
        depth = 1e-6

    world_per_pixel = world_units_per_pixel(depth, viewport_height, fov_deg)

    offset = _vec3_add(
        _vec3_scale(right, dx * world_per_pixel),
        _vec3_scale(up, -dy * world_per_pixel)
    )

    return _vec3_add(object_position, _vec3_scale(offset, sensitivity))

#endregion
# ══════════════════════════════════════════════════════════════
#  ЗАДАЧА 5: МЫШЬ → ПЕРЕМЕЩЕНИЕ КАМЕРЫ (ПАНИРОВКА)
# ══════════════════════════════════════════════════════════════
#region
def mouse_to_camera_pan(dx, dy, camera_rotation, camera_position,
                         depth, viewport_height, fov_deg,
                         sensitivity=1.0):
    """Перемещение камеры параллельно плоскости экрана.

    Камера сдвигается по её текущим screen-aligned осям:
    dx → вдоль camera right
    dy → вдоль camera up

    ВАЖНО:
    Эта функция рассчитана под камеру с явным vertical FOV.
    Она НЕ зависит от preview_scale, потому что preview_scale теперь
    должен влиять только на разрешение рендера / производительность,
    но не на оптику камеры и не на ощущение управления.

    Геометрия такая:
    - на глубине depth видимая высота кадра равна
      2 * depth * tan(fov / 2)
    - один экранный пиксель по Y соответствует world_units_per_pixel(...)
    - тот же масштаб используется и по X, потому что движение идёт
      в базисе screen plane (right/up), а не в сырых clip-space координатах

    Параметры:
    dx, dy — смещение мыши в пикселях виджета
    camera_rotation — Euler XYZ камеры в радианах
    camera_position — позиция камеры в мире
    depth — глубина рабочей плоскости панорамирования вдоль forward
    viewport_height — высота области рендера в пикселях
    fov_deg — vertical FOV камеры в градусах
    sensitivity — дополнительный множитель

    Возвращает новую позицию камеры (QVector3D).
    """
    camera_position = _to_vec3(camera_position)

    right, up, _ = camera_basis_vectors(camera_rotation)
    world_per_pixel = world_units_per_pixel(depth, viewport_height, fov_deg)

    offset = _vec3_add(
        _vec3_scale(right, -dx * world_per_pixel),
        _vec3_scale(up, dy * world_per_pixel)
    )

    return _vec3_add(camera_position, _vec3_scale(offset, sensitivity))

#endregion
# ══════════════════════════════════════════════════════════════
# ЗАДАЧА 6: МЫШЬ → ПЕРЕМЕЩЕНИЕ ГРУППЫ ФИГУР
# ══════════════════════════════════════════════════════════════
#region
def mouse_to_figures_translation(dx, dy, camera_rotation, positions,
                                 camera_position, viewport_width,
                                 viewport_height, fov_deg,
                                 sensitivity=1.0):
    """Перемещение группы фигур мышью в плоскости экрана.

    Вся группа сдвигается на один и тот же мировой вектор,
    вычисленный по экранному смещению мыши:
    dx → вдоль camera right
    dy → вдоль camera up

    Масштаб (world units / pixel) считается по глубине общего
    центра группы относительно камеры — так же, как в
    mouse_to_object_translation, но для центра позиций фигур.

    Параметры:
    dx, dy — смещение мыши в пикселях виджета
    camera_rotation — Euler XYZ камеры в радианах
    positions — список текущих позиций фигур
    camera_position — позиция камеры в мире
    viewport_width — ширина области рендера в пикселях
    viewport_height — высота области рендера в пикселях
    fov_deg — vertical FOV камеры в градусах
    sensitivity — дополнительный множитель

    Возвращает новый список позиций (list[QVector3D]).
    """
    camera_position = _to_vec3(camera_position)
    center = figures_center(positions)

    right, up, forward = camera_basis_vectors(camera_rotation)

    to_center = _vec3_sub(center, camera_position)
    depth = _dot(to_center, forward)
    if depth < 1e-6:
        depth = 1e-6

    world_per_pixel = world_units_per_pixel(depth, viewport_height, fov_deg)

    offset = _vec3_add(
        _vec3_scale(right, dx * world_per_pixel),
        _vec3_scale(up, -dy * world_per_pixel)
    )
    offset = _vec3_scale(offset, sensitivity)

    return translate_figures(positions, offset)

def drag_to_figures_translation(total_dx, total_dy,
                                drag_camera_rotation,
                                start_positions,
                                camera_position,
                                viewport_width, viewport_height,
                                fov_deg,
                                sensitivity=1.0):
    """Перемещение группы фигур от начала drag.

    ВАЖНО:
    - total_dx / total_dy считаются от точки нажатия мыши
    - смещение применяется к позициям на момент начала drag
    - базис экрана и глубина берутся из camera_rotation и
      центра группы в момент нажатия

    Это устраняет накопление ошибок между mouseMoveEvent.

    Возвращает новый список позиций (list[QVector3D]).
    """
    camera_position = _to_vec3(camera_position)
    center = figures_center(start_positions)

    right, up, forward = camera_basis_vectors(drag_camera_rotation)

    to_center = _vec3_sub(center, camera_position)
    depth = _dot(to_center, forward)
    if depth < 1e-6:
        depth = 1e-6

    world_per_pixel = world_units_per_pixel(depth, viewport_height, fov_deg)

    offset = _vec3_add(
        _vec3_scale(right, total_dx * world_per_pixel),
        _vec3_scale(up, -total_dy * world_per_pixel)
    )
    offset = _vec3_scale(offset, sensitivity)

    return translate_figures(start_positions, offset)

#endregion
# ══════════════════════════════════════════════════════════════
# ЗАДАЧА 7: МЫШЬ → ПОВОРОТ ГРУППЫ ФИГУР ВОКРУГ ОБЩЕГО ЦЕНТРА
# ══════════════════════════════════════════════════════════════
#region
def mouse_to_figures_rotation(dx, dy, camera_rotation, positions, rotations,
                              sensitivity=0.005, center=None):
    """Поворот группы фигур мышью вокруг общего центра.

    Оси вращения параллельны плоскости рендера, как в
    mouse_to_object_rotation:
    dx → вокруг vertical screen axis (camera up)
    dy → вокруг horizontal screen axis (camera right)

    Группа поворачивается как твёрдое тело:
    - позиции фигур поворачиваются вокруг общего центра координат
    - каждая фигура доворачивается на тот же мировой поворот,
      поэтому фигуры разворачиваются вместе с группой

    Параметры:
    dx, dy — смещение мыши в пикселях виджета
    camera_rotation — Euler XYZ камеры в радианах
    positions — список текущих позиций фигур
    rotations — список текущих Euler XYZ поворотов фигур (или None)
    sensitivity — чувствительность мыши
    center — точка поворота; если None, берётся общий центр
             координат figures_center(positions)

    Возвращает (new_positions, new_rotations).
    """
    if center is None:
        center = figures_center(positions)

    right, up, _ = camera_basis_vectors(camera_rotation)

    rot_vertical = axis_angle_to_quat(up, dx * sensitivity)
    rot_horizontal = axis_angle_to_quat(right, -dy * sensitivity)
    delta_world = quat_mul(rot_vertical, rot_horizontal)

    return rotate_figures_around_point(positions, rotations, center, delta_world)

def drag_to_figures_rotation(total_dx, total_dy,
                             drag_camera_rotation,
                             start_positions, start_rotations,
                             sensitivity=0.005, center=None):
    """Поворот группы фигур от начала drag вокруг общего центра.

    ВАЖНО:
    - total_dx / total_dy считаются от точки нажатия мыши
    - оси экрана берутся из camera_rotation в момент начала drag
    - дельта применяется к позициям и поворотам на момент нажатия,
      а не к уже обновлённым на предыдущем mouseMoveEvent
    - центр поворота фиксирован: если center не задан, он
      вычисляется по start_positions и не дрейфует во время drag

    Это устраняет накопление ошибок и делает результат
    независимым от числа mouseMoveEvent.

    Возвращает (new_positions, new_rotations).
    """
    if center is None:
        center = figures_center(start_positions)

    right, up, _ = camera_basis_vectors(drag_camera_rotation)

    rot_vertical = axis_angle_to_quat(up, total_dx * sensitivity)
    rot_horizontal = axis_angle_to_quat(right, -total_dy * sensitivity)
    delta_world = quat_mul(rot_vertical, rot_horizontal)

    return rotate_figures_around_point(
        start_positions, start_rotations, center, delta_world
    )

def drag_to_figures_rotation_3axis(start_mouse_pos, current_mouse_pos,
                                   group_screen_center,
                                   drag_camera_rotation,
                                   start_positions, start_rotations,
                                   rotate_sensitivity=0.005,
                                   twist_sensitivity=1.0,
                                   center=None):
    """Трёхосевой screen-space поворот группы фигур от начала drag.

    Компоненты:
    - horizontal drag -> вращение группы вокруг camera up
    - vertical drag -> вращение группы вокруг camera right
    - орбитальное движение курсора вокруг screen-center группы ->
      вращение вокруг camera forward

    Поворот выполняется вокруг общего центра координат позиций
    (или вокруг явно заданного center), фигуры доворачиваются
    вместе с группой.

    group_screen_center — экранные координаты центра группы
    (например, проекция figures_center(start_positions) на экран).

    Возвращает (new_positions, new_rotations).
    """
    if center is None:
        center = figures_center(start_positions)

    total_dx = float(current_mouse_pos.x() - start_mouse_pos.x())
    total_dy = float(current_mouse_pos.y() - start_mouse_pos.y())

    right, up, forward = camera_basis_vectors(drag_camera_rotation)

    rot_vertical = axis_angle_to_quat(up, total_dx * rotate_sensitivity)
    rot_horizontal = axis_angle_to_quat(right, -total_dy * rotate_sensitivity)

    sx = float(start_mouse_pos.x() - group_screen_center.x())
    sy = float(start_mouse_pos.y() - group_screen_center.y())
    cx = float(current_mouse_pos.x() - group_screen_center.x())
    cy = float(current_mouse_pos.y() - group_screen_center.y())

    start_len2 = sx * sx + sy * sy
    curr_len2 = cx * cx + cy * cy

    twist_angle = 0.0
    min_radius2 = 25.0

    if start_len2 > min_radius2 and curr_len2 > min_radius2:
        start_angle = math.atan2(sy, sx)
        curr_angle = math.atan2(cy, cx)
        twist_angle = curr_angle - start_angle

        while twist_angle > math.pi:
            twist_angle -= 2.0 * math.pi
        while twist_angle < -math.pi:
            twist_angle += 2.0 * math.pi

    rot_twist = axis_angle_to_quat(forward, -twist_angle * twist_sensitivity)

    delta_world = quat_mul(rot_twist, quat_mul(rot_vertical, rot_horizontal))

    return rotate_figures_around_point(
        start_positions, start_rotations, center, delta_world
    )

#endregion
# ══════════════════════════════════════════════════════════════
#  ДОПОЛНИТЕЛЬНЫЕ УТИЛИТЫ ДЛЯ МОДЕЛЬНЫХ МАТРИЦ
# ══════════════════════════════════════════════════════════════
#region
def model_matrix(position, rotation_euler, scale_value=1.0):
    """Собрать модельную матрицу из position / rotation / scale.

    rotation_euler — Euler XYZ в радианах, как в шейдере.
    """
    position = _to_vec3(position)
    rotation_euler = _to_vec3(rotation_euler)

    m = QMatrix4x4()
    m.setToIdentity()
    m.translate(position)
    m.rotate(euler_vec_to_quat(rotation_euler))

    if isinstance(scale_value, (int, float)):
        m.scale(float(scale_value))
    else:
        s = _to_vec3(scale_value)
        m.scale(s.x(), s.y(), s.z())

    return m

def camera_target_from_pose(camera_position, camera_rotation, distance=1.0):
    """Точка перед камерой на расстоянии distance."""
    camera_position = _to_vec3(camera_position)
    _, _, forward = camera_basis_vectors(camera_rotation)
    return _vec3_add(camera_position, _vec3_scale(forward, distance))

#endregion
# ══════════════════════════════════════════════════════════════
#  ТРАЕКТОРИИ (совместимы с Figure.trajectory_fn)
# ══════════════════════════════════════════════════════════════
#region
def ellipse_trajectory(time, translation, rotation,
                       radius_a=1.0, radius_b=0.5,
                       speed=1.0, rotate_figure=False,
                       *args, **kwargs):
    """Движение по эллипсу вокруг исходной позиции фигуры.

    Сигнатура совместима с Figure.traejctory_fn:
        (time, translation, rotation, *traejctory_params)
            -> (new_translation, new_rotation)

    Эллипс строится в локальной плоскости XZ с центром в translation,
    после чего смещение поворачивается на оригинальный rotation фигуры
    (Euler XYZ, радианы) вокруг её центра.

    Параметры:
    time — текущее время (секунды)
    translation — базовая позиция фигуры = центр эллипса (list[3] или QVector3D)
    rotation — базовый поворот фигуры, Euler XYZ в радианах
    radius_a — полуось/радиус эллипса вдоль локальной оси X
    radius_b — полуось/радиус эллипса вдоль локальной оси Z
    speed — скорость изменения углового параметра (рад/с);
            отрицательное значение — движение в обратную сторону
    rotate_figure — если True, фигура дополнительно доворачивается
                    по направлению движения (по касательной к эллипсу)

    Возвращает (new_translation, new_rotation) — два списка по 3 float.
    Входные translation/rotation не изменяются.

    ВАЖНО: угловой параметр меняется линейно по времени, поэтому линейная
    скорость на эллипсе не постоянна (максимальна у концов малой оси).
    """
    angle = float(time) * float(speed)

    # 1. Точка и касательная на эллипсе в локальной плоскости XZ
    local_point = vec3(
        float(radius_a) * math.cos(angle),
        0.0,
        float(radius_b) * math.sin(angle),
    )
    local_tangent = vec3(
        -float(radius_a) * math.sin(angle),
        0.0,
        float(radius_b) * math.cos(angle),
    )

    # 2. Поворот смещения на оригинальный угол фигуры
    base_rot = _to_vec3(rotation)
    rotated_offset = rotate_vec3_euler(local_point, base_rot)

    # 3. Итоговая позиция в мировых координатах
    new_position = _vec3_add(_to_vec3(translation), rotated_offset)

    # 4. Поворот фигуры (базовый либо с доворотом по касательной)
    if rotate_figure and _vec3_length(local_tangent) > EPS:
        direction = 1.0 if float(speed) >= 0.0 else -1.0
        tangent = _vec3_scale(_vec3_normalize(local_tangent), direction)
        local_yaw = math.atan2(-tangent.x(), tangent.z())

        q_base = euler_vec_to_quat(base_rot)
        q_yaw = axis_angle_to_quat(vec3(0.0, 1.0, 0.0), local_yaw)
        q_total = quat_normalize(quat_mul(q_base, q_yaw))
        new_rotation = quat_to_euler_vec(q_total)
    else:
        new_rotation = base_rot

    return list(vec3_to_tuple(new_position)), list(vec3_to_tuple(new_rotation))

#endregion

