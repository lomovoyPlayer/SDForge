"""
SDForge: панель экспорта кадра — рендер одного кадра в слой Krita или файл.

Независимый блок: получает (params, controller, previewer). Поля пишут
прямо в params.frame_export_* (имена совпадают с ключами
DEFAULT_TREE_SETTINGS); в БД настройки попадают при переключении дерева /
закрытии диалога (controller.close_current_tree), как у RenderSettingsPanel.
На превью frame_export_* не влияют, поэтому commit — локальный (только
пересчёт подписи размера), params.emit_changed не зовём.

Рендер идёт напрямую через previewer (render_frame_export, export_progress).
Панель перечитывает params по tree_loaded.

Прогресс: воркер шлёт сырые чанки (при накоплении total = чанки × кадры),
панель переводит в проценты + счётчик кадров «k/M» + прошедшее время.
"""

from __future__ import annotations

import time
from typing import Callable

from PyQt5 import QtCore, QtWidgets
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QImage

from krita import Krita

from .forms import Form, FieldSpec, FormField
from ..ui_style import tune_layout

from ..scene_controller import SceneController
from ..render_previewer import RenderPreviewParams, SDFRenderPreviewer

from ... import localization

FRAME_EXPORT_SPECS: list[FieldSpec] = [
	FieldSpec("float", localization.get_text("frame_export_panel.frame_export_resolution_scale"), "frame_export_resolution_scale", minimum=0.05, maximum=16.0, step=0.25, decimals=2, suffix=" ×"),
	FieldSpec("bool", localization.get_text("frame_export_panel.frame_export_use_viewport_base"), "frame_export_use_viewport_base"),
	FieldSpec("int", localization.get_text("frame_export_panel.frame_export_base_width"), "frame_export_base_width", minimum=1, maximum=16384, step=10),
	FieldSpec("int", localization.get_text("frame_export_panel.frame_export_base_height"), "frame_export_base_height", minimum=1, maximum=16384, step=10),
	FieldSpec("bool", localization.get_text("frame_export_panel.frame_export_show_floor"), "frame_export_show_floor"),
	FieldSpec("bool", localization.get_text("frame_export_panel.frame_export_transparent_background"), "frame_export_transparent_background"),
	FieldSpec("int", localization.get_text("frame_export_panel.frame_export_max_reflect_count"), "frame_export_max_reflect_count", minimum=0, maximum=32, step=1),
	FieldSpec("bool", localization.get_text("frame_export_panel.frame_export_phys_glass"), "frame_export_phys_glass"),
	FieldSpec("float", localization.get_text("frame_export_panel.frame_export_dispersion"), "frame_export_dispersion", minimum=0.0, maximum=1.0, step=0.05),
	FieldSpec("int", localization.get_text("frame_export_panel.frame_export_area_ray_count"), "frame_export_area_ray_count", minimum=0, maximum=12, step=1),
	FieldSpec("int", localization.get_text("frame_export_panel.frame_export_top_light_count"), "frame_export_top_light_count", minimum=0, maximum=4, step=1),
	FieldSpec("int", localization.get_text("frame_export_panel.frame_export_accum_frames"), "frame_export_accum_frames", minimum=1, maximum=64, step=1),
]


class FrameExportPanel(QtWidgets.QWidget):
	"""
	Панель экспорта кадра — рендер одного кадра или семплирование нескольких кадров и экспорт в слой Krita или файл."""
	def __init__(self,
		params: RenderPreviewParams,
		controller: SceneController,
		previewer: SDFRenderPreviewer,
		parent: QtWidgets.QWidget | None = None) -> None:
		super().__init__(parent)
		self.params: RenderPreviewParams = params
		self.controller: SceneController = controller
		self.previewer: SDFRenderPreviewer = previewer
		self._export_in_progress: bool = False
		self._export_started_at: float | None = None

		layout = QtWidgets.QVBoxLayout(self)
		layout.setAlignment(Qt.AlignTop)
		tune_layout(layout)

		self.form = Form(self, sync=False)
		self.form.add_header(localization.get_text("frame_export_panel.frame_export_header"))

		# add_specs возвращает поля в порядке спеков — складываем в dict по
		# attr, чтобы связи между полями не зависели от индексов
		fields: list[FormField] = self.form.add_specs(
			FRAME_EXPORT_SPECS, target=params, commit=self._commit)
		self._fields: dict[str, FormField] = {
			spec.attr: field for spec, field in zip(FRAME_EXPORT_SPECS, fields)}

		self._size_label = QtWidgets.QLabel()
		self.form.add_widget(self._size_label)

		# прогресс виден только во время экспорта
		self._progress = QtWidgets.QProgressBar()
		self._progress.setRange(0, 100)
		self._progress.setValue(0)
		self._progress.setFormat("%p%")
		self._progress.setVisible(False)
		self.form.add_widget(self._progress)

		self._btn_layer = QtWidgets.QPushButton(localization.get_text("frame_export_panel.export_to_layer"))
		self._btn_file = QtWidgets.QPushButton(localization.get_text("frame_export_panel.export_to_file.button"))
		self._btn_layer.clicked.connect(self._export_to_layer)
		self._btn_file.clicked.connect(self._export_to_file)
		buttons = QtWidgets.QWidget()
		row = QtWidgets.QHBoxLayout(buttons)
		row.setContentsMargins(0, 0, 0, 0)
		row.addWidget(self._btn_layer)
		row.addWidget(self._btn_file)
		self.form.add_widget(buttons)

		layout.addWidget(self.form)

		# поля базового размера активны только при ручной базе
		self._fields["frame_export_use_viewport_base"].changed.connect(
			self._on_viewport_base_toggled)
		self._on_viewport_base_toggled(self.params.frame_export_use_viewport_base)

		self.previewer.export_progress.connect(self._on_export_progress)

		# «в слой» активна только при открытом документе
		notifier = Krita.instance().notifier()
		for name in ("imageCreated", "imageClosed", "viewCreated", "viewClosed"):
			signal = getattr(notifier, name, None)
			if signal is not None:
				signal.connect(self._update_layer_button)
		self._watch_timer = QtCore.QTimer(self)
		self._watch_timer.setInterval(700)
		self._watch_timer.timeout.connect(self._update_layer_button)
		self._watch_timer.start()

		# синхронизация с БД: открыли другое дерево — настройки уже в params
		controller.tree_loaded.connect(lambda _id: self.rebuild())

		self._update_layer_button()

	# ------------------------------------------------------------------
	# Синхронизация с БД / коммит
	# ------------------------------------------------------------------
	# region
	def rebuild(self) -> None:
		self.form.refresh()
		self._on_viewport_base_toggled(self.params.frame_export_use_viewport_base)

	def _commit(self) -> None:
		self._update_size_label()

	# endregion
	# ------------------------------------------------------------------
	# Служебное
	# ------------------------------------------------------------------
	# region
	def _on_viewport_base_toggled(self, value: object) -> None:
		manual: bool = not bool(value)
		self._fields["frame_export_base_width"].setEnabled(manual)
		self._fields["frame_export_base_height"].setEnabled(manual)
		self._update_size_label()

	def _compute_base_size(self) -> tuple[int, int]:
		"""Базовый размер кадра БЕЗ масштаба — масштаб применяет render_frame_export."""
		p = self.params
		if p.frame_export_use_viewport_base:
			return max(1, self.previewer.width()), max(1, self.previewer.height())
		return (max(1, int(p.frame_export_base_width)),
			max(1, int(p.frame_export_base_height)))

	def _compute_export_size(self) -> tuple[int, int]:
		"""Итоговый размер (база × масштаб) — только для подписи."""
		base_w, base_h = self._compute_base_size()
		scale = max(0.01, float(self.params.frame_export_resolution_scale))
		return (max(1, int(round(base_w * scale))),
			max(1, int(round(base_h * scale))))

	def _update_size_label(self) -> None:
		w: int
		h: int
		w, h = self._compute_export_size()
		self._size_label.setText(localization.get_text("frame_export_panel.frame_export_resolution_size", w=w, h=h))

	def _update_layer_button(self, *args: object) -> None:
		has_doc: bool = Krita.instance().activeDocument() is not None
		self._btn_layer.setEnabled(has_doc and not self._export_in_progress)
		self._btn_layer.setToolTip(
			"" if has_doc else localization.get_text("frame_export_panel.has_no_open_document"))
		self._update_size_label()

	def _elapsed_text(self) -> str:
		if self._export_started_at is None:
			return ""
		elapsed = time.perf_counter() - self._export_started_at
		return f"{int(elapsed)} с"

	def _on_export_progress(self, done: int, total: int) -> None:
		if total <= 0:
			return
		# воркер шлёт сырые чанки; при накоплении total = чанки_кадра × кадры
		frames = max(1, int(getattr(self.params, "frame_export_accum_frames", 1)))
		pct = min(100, int(done * 100 / total))
		self._progress.setMaximum(100)
		self._progress.setValue(pct)
		elapsed = self._elapsed_text()
		if frames > 1 and total % frames == 0:
			per_frame = max(1, total // frames)
			cur_frame = min(frames, done // per_frame + 1)
			self._progress.setFormat(localization.get_text("frame_export_panel.export_progress_sampling", pct=pct, cur_frame=cur_frame, total_frames=frames, elapsed=elapsed))
		else:
			self._progress.setFormat(localization.get_text("frame_export_panel.export_progress_default", pct=pct, elapsed=elapsed))

	def _reset_progress(self) -> None:
		if not self._export_in_progress:
			self._progress.setValue(0)
			self._progress.setFormat("%p%")
			self._progress.setVisible(False)

	def _warn(self, text: str) -> None:
		QtWidgets.QMessageBox.warning(self, localization.get_text("frame_export_panel.frame_export_header"), text)

	# endregion
	# ------------------------------------------------------------------
	# Экспорт
	# ------------------------------------------------------------------
	# region
	def _start_export(self, on_image_ready: Callable[[QImage], None]) -> None:
		if self._export_in_progress:
			return
		if not self.previewer._renderer_initialized:
			self._warn(localization.get_text("frame_export_panel.warning.renderer_not_initialized"))
			return
		self._export_in_progress = True
		self._export_started_at = time.perf_counter()
		self._btn_layer.setEnabled(False)
		self._btn_file.setEnabled(False)
		self._progress.setMaximum(100)
		self._progress.setValue(0)
		self._progress.setFormat("Подготовка…")
		self._progress.setVisible(True)

		base_size: tuple[int, int] = self._compute_base_size()

		def _done(image):
			self._export_in_progress = False
			self._export_started_at = None
			self._btn_file.setEnabled(True)
			self._update_layer_button()
			if image is None or image.isNull():
				self._reset_progress()
				self._warn(localization.get_text("frame_export_panel.warning.render_no_image"))
				return
			# «Готово» держим 700 мс, затем бар прячется
			self._progress.setValue(100)
			self._progress.setFormat(localization.get_text("frame_export_panel.render_done"))
			QtCore.QTimer.singleShot(700, self._reset_progress)
			on_image_ready(image)

		self.previewer.render_frame_export(on_finished=_done, base_size=base_size)

	def _export_to_layer(self) -> None:
		if Krita.instance().activeDocument() is None:
			return
		self._start_export(self._insert_as_layer)

	def _export_to_file(self) -> None:
		path: str
		path, _flt = QtWidgets.QFileDialog.getSaveFileName(
			self, localization.get_text("frame_export_panel.export_to_file.dialog.caption"), "frame.png",
			"PNG (*.png);;JPEG (*.jpg);;Все файлы (*)")
		if not path:
			return
		if not path.lower().endswith((".png", ".jpg", ".jpeg")):
			path += ".png"
		self._start_export(lambda image: self._save_image(image, path))

	def _save_image(self, image: QImage, path: str) -> None:
		if not image.save(path):
			self._warn(localization.get_text("frame_export_panel.warning.save_failed", path=path))

	def _insert_as_layer(self, image: QImage) -> None:
		doc = Krita.instance().activeDocument()
		if doc is None:
			self._warn(localization.get_text("frame_export_panel.warning.no_active_document"))
			return
		if doc.colorModel() != "RGBA" or doc.colorDepth() != "U8":
			self._warn(localization.get_text("frame_export_panel.warning.document_not_rgba-8-bit", colorModel=doc.colorModel(), colorDepth=doc.colorDepth()))
			return

		# ARGB32 на little-endian в памяти = B,G,R,A — порядок каналов Krita
		img: QImage = image.convertToFormat(QImage.Format_ARGB32)
		bits = img.constBits()
		bits.setsize(img.byteCount())
		pixel_bytes: bytes = bytes(bits)

		selected = doc.activeNode()
		parent = selected.parentNode() if selected is not None else None
		if parent is None:
			parent = doc.rootNode()

		layer = doc.createNode(localization.get_text("frame_export_panel.layer_name", width=img.width(), height=img.height()), "paintlayer")
		# addChildNode(child, above): второй аргумент — узел, НАД которым встанет новый
		parent.addChildNode(layer, selected)

		x: int = (doc.width() - img.width()) // 2
		y: int = (doc.height() - img.height()) // 2
		layer.setPixelData(pixel_bytes, x, y, img.width(), img.height())

		doc.setActiveNode(layer)
		doc.refreshProjection()

	# endregion
