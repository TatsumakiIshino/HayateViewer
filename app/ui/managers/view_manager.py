from __future__ import annotations
from typing import TYPE_CHECKING
import logging
from PySide6.QtCore import QObject, Signal
from PySide6.QtGui import QImage
from PIL import Image as PILImage

from app.ui.views.default_view import DefaultGraphicsView
from app.ui.views.opengl_view import OpenGLView
from app.image.resampler_mt import MultiThreadedImageResampler

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow
    from app.core.state import AppState
    from app.ui.views import ImageViewer

class ViewManager(QObject):
    """ビューの切り替えと更新を担当するクラス。"""
    
    def __init__(self, main_window: MainWindow, app_state: AppState):
        super().__init__()
        self.main_window = main_window
        self.app_state = app_state
        self.resampler = self._create_resampler()
        
        # リサンプリングモード変更の監視
        self.main_window.controller.event_bus.RESAMPLING_MODE_CHANGED.connect(self.on_resampling_mode_changed)

    def _create_resampler(self) -> MultiThreadedImageResampler:
        """設定に基づいてCPUリサンプラーを生成する。"""
        mode = self.main_window.settings_manager.get('resampling_mode_cpu', 'PIL_BILINEAR')
        workers = self.main_window.settings_manager.get('parallel_decoding_workers', 1)
        logging.info(f"Creating CPU resampler with mode: {mode}, workers: {workers}")
        return MultiThreadedImageResampler(mode=mode, max_threads=workers)

    def on_resampling_mode_changed(self):
        """リサンプリングモード変更のイベントハンドラ。"""
        backend = self.main_window.settings_manager.get('rendering_backend')
        if backend == 'opengl':
            return
        
        logging.info("CPU resampling mode changed. Re-creating resampler and updating view.")
        self.resampler = self._create_resampler()
        # UIManager経由で更新をトリガーするか、ここで直接更新するか
        # ここでは直接更新せず、UIManagerがイベントを受け取ってupdate_viewを呼ぶ流れを維持する方が安全かもしれないが、
        # ViewManagerがupdate_viewを持つならここで呼べる
        self.update_view()

    def switch_to_opengl_view(self):
        """DefaultGraphicsViewからOpenGLViewに切り替える。"""
        if isinstance(self.main_window.view, DefaultGraphicsView):
            logging.info("Switching to OpenGLView")
            new_view = OpenGLView(
                self.app_state,
                self.main_window.settings_manager,
                self.main_window.controller.image_cache,
                self.main_window.controller.event_bus,
                self.main_window
            )
            # OpenGLView固有のシグナル接続
            new_view.texture_prepared.connect(self.main_window.on_texture_prepared)
            new_view.request_load_image.connect(self.main_window.controller.on_request_load_image)

            self._replace_view(new_view)

    def _replace_view(self, new_view):
        """現在のビューを新しいビューに置き換える共通処理。"""
        old_view = self.main_window.image_viewer.current_view
        self.main_window.image_viewer.layout.removeWidget(old_view)
        old_view.deleteLater()
        
        self.main_window.image_viewer.layout.addWidget(new_view)
        self.main_window.image_viewer.current_view = new_view
        self.main_window.view = new_view
        
        # イベント転送の再接続
        new_view.keyPressed.connect(self.main_window.image_viewer.keyPressed)
        new_view.wheelScrolled.connect(self.main_window.image_viewer.wheelScrolled)

    def update_view(self, indices_to_display: list[int] = None):
        """現在のビューを更新する。"""
        if indices_to_display is None:
            # 引数がなければUIManagerから渡されるべきだが、
            # 循環参照を避けるため、呼び出し元（UIManager）がindicesを計算して渡す設計にするのが良い。
            # しかし、ViewManagerが自律的に動くなら、indicesの計算ロジックもここに持つべきか？
            # 一旦、UIManagerが計算ロジックを持っているので、引数必須とするか、
            # ここでは何もしない（エラーログ）
            logging.warning("ViewManager.update_view called without indices.")
            return

        view = self.main_window.view
        file_loader = self.app_state.file_loader

        if not self.app_state.is_content_loaded or not file_loader:
            if hasattr(view, 'displayImage'):
                view.displayImage([])
            return

        keys_to_display = [f"{file_loader.path}::{index}" for index in indices_to_display]
        logging.info(f"[ViewManager.update_view] Updating view with keys: {keys_to_display}")

        if isinstance(view, OpenGLView):
            view.displayImage(keys_to_display)
        elif isinstance(view, DefaultGraphicsView):
            self._update_default_view(view, indices_to_display)

    def _update_default_view(self, view: DefaultGraphicsView, indices: list[int]):
        """DefaultGraphicsViewの更新ロジック。"""
        images = []
        target_size = view.size()

        for index in indices:
            np_image = self.main_window.controller.image_cache.get(index)
            
            if np_image is not None:
                try:
                    pil_image = PILImage.fromarray(np_image)
                    
                    img_w, img_h = pil_image.size
                    view_w, view_h = target_size.width(), target_size.height()
                    
                    if len(indices) == 2:
                        view_w //= 2

                    if img_w == 0 or img_h == 0: continue

                    aspect_ratio = img_w / img_h
                    view_aspect_ratio = view_w / view_h if view_h > 0 else 0

                    if view_aspect_ratio > aspect_ratio:
                        new_h = view_h
                        new_w = int(new_h * aspect_ratio)
                    else:
                        new_w = view_w
                        new_h = int(new_w / aspect_ratio)

                    if new_w > 0 and new_h > 0:
                        resized_pil_image = self.resampler.resize(pil_image, (new_w, new_h))
                        data = resized_pil_image.tobytes("raw", "RGB")
                        bytes_per_line = resized_pil_image.width * 3
                        q_image = QImage(data, resized_pil_image.width, resized_pil_image.height, bytes_per_line, QImage.Format.Format_RGB888).rgbSwapped().copy()
                        images.append(q_image)

                except Exception as e:
                    logging.error(f"Failed to resample or convert image for index {index}: {e}", exc_info=True)

        view.displayImage(images)

    def zoom_in(self):
        if self.main_window.view:
            self.main_window.view.zoom_in()

    def zoom_out(self):
        if self.main_window.view:
            self.main_window.view.zoom_out()

    def zoom_reset(self):
        if self.main_window.view:
            self.main_window.view.zoom_reset()
