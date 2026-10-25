"""
SDForge: реестр функций траекторий.

Функция траектории: fn(t, pos, rot, **params) -> (pos, rot).
Стабильный якорь в БД — строковый id, а не ссылка на функцию
(аналогия: текстуры по имени файла, материалы по стабильному id).
Панель настройки траекторий отложена; реестр уже даёт ей данные:
отображаемое имя и дефолты индивидуальных параметров.
"""

from typing import Any, Callable, Dict, NamedTuple, Optional

from .math_utils import ellipse_trajectory


class TrajectorySpec(NamedTuple):
    fn: Callable              # fn(t, pos, rot, **params) -> (pos, rot)
    name: str                 # отображаемое имя (для будущей панели)
    params: Dict[str, Any]    # дефолты параметров; ключи == имена kwargs функции


TRAJECTORIES: Dict[str, TrajectorySpec] = {
    "ellipse": TrajectorySpec(
        fn=ellipse_trajectory,
        name="Эллипс",
        params={"radius_a": 1.0, "radius_b": 0.5, "speed": 1.0, "rotate_figure": False},
    ),
}


def get_trajectory(traj_id: str) -> Optional[TrajectorySpec]:
    return TRAJECTORIES.get(traj_id)


def trajectory_id_for(fn: Callable) -> Optional[str]:
    """Обратный поиск id по identity функции (для сериализации)."""
    for traj_id, spec in TRAJECTORIES.items():
        if spec.fn is fn:
            return traj_id
    return None