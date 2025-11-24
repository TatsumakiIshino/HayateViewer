from __future__ import annotations
import logging
import cv2
import numpy as np
from typing import TYPE_CHECKING
from PIL import Image
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtWidgets import QGestureEvent, QPinchGesture, QPanGesture, QSwipeGesture
from PySide6.QtGui import QKeyEvent, QImage, QPalette
from PySide6.QtCore import Signal, Qt, QPoint, QPointF, QTimer, Slot, QEvent

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow
    from app.core.events import EventBus

from app.ui.opengl.texture_manager import TextureManager
from app.ui.opengl.shader_manager import ShaderManager
from app.ui.opengl.renderer import Renderer


class OpenGLView(QOpenGLWidget):
    """OpenGLとシェーダーを使用してレンダリングするビュー。"""
    keyPressed = Signal(QKeyEvent)
    wheelScrolled = Signal(int)
    texture_prepared = Signal(str) # key
    request_load_image = Signal(int, int) # page_index, priority
    view_initialized = Signal()
    swipeTriggered = Signal(str)

    def __init__(self, app_state, settings_manager, image_cache, event_bus: EventBus, parent: 'MainWindow' | None = None):
        super().__init__(parent)
        self.setObjectName("OpenGL")
        self.app_state = app_state
        self.settings_manager = settings_manager
        self.image_cache = image_cache # CPUキャッシュへの参照
        self.event_bus = event_bus
        self.image_cache.page_cached.connect(self.on_page_cached)
        
        self.event_bus.gpu_prefetch_request.connect(self.on_gpu_prefetch_request)
        self.event_bus.RESAMPLING_MODE_CHANGED.connect(self.on_resampling_mode_changed)
        
        # マネージャーの初期化
        n = self.settings_manager.get('gpu_max_prefetch_pages', 9)
        gpu_cache_size = (n * 2) + 2
        self.texture_manager = TextureManager(app_state, max_cache_size=gpu_cache_size)
        self.shader_manager = ShaderManager(settings_manager)
        self.renderer = Renderer(self.shader_manager, self.texture_manager)

        self.display_keys = []
        self.zoom_level = 1.0
        self.pan_offset = QPointF(0.0, 0.0)

        # ルーペ機能用の状態変数
        self.loupe_active = False
        self.panning_active = False
        self.original_zoom_level = 1.0
        self.original_pan_offset = QPointF(0.0, 0.0)
        self.last_pan_pos = QPoint()

        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # ジェスチャーの有効化
        self.grabGesture(Qt.GestureType.PinchGesture)
        self.grabGesture(Qt.GestureType.PanGesture)
        self.grabGesture(Qt.GestureType.SwipeGesture)

        palette = self.palette()
        bg_color = palette.color(QPalette.ColorRole.Window)
        self.renderer.set_clear_color((bg_color.redF(), bg_color.greenF(), bg_color.blueF(), bg_color.alphaF()))

    @property
    def texture_cache(self):
        """UIManager互換性のためのプロパティ"""
        return self.texture_manager.texture_cache

    def sizeHint(self):
        """ウィンドウの自動リサイズを防ぐため、推奨サイズとして親のサイズを返す"""
        from PySide6.QtCore import QSize
        # 親ウィジェットのサイズを返すことで、レイアウトマネージャーに
        # ウィンドウサイズを変更しないよう指示する
        if self.parentWidget():
            return self.parentWidget().size()
        return QSize(800, 600)  # デフォルトサイズ

    def update_image(self, file_path: str | None):
        if not file_path:
            self.displayImage([], [])
            return

        index = self.app_state.image_files.index(file_path)

        # Check CPU cache first
        cached_image = self.image_cache.get(index)
        if cached_image is not None:
            qimage = self.pil_to_qimage(cached_image)
            self.on_image_loaded(qimage, index)
            return

        # If not in cache, request load via signal
        self.request_load_image.emit(index, 0)

    @Slot(QImage, int)
    def on_image_loaded(self, qimage: QImage, index: int):
        """画像が非同期で読み込まれたときに呼び出されるスロット。"""
        if not qimage or qimage.isNull():
            return

        key = f"{self.app_state.file_loader.path}::{index}"
        
        self.prepare_texture(key, qimage)

        if key in self.display_keys:
            self.update()

    def update_settings(self):
        """設定変更を動的に適用する。"""
        logging.debug("[OpenGLView] Updating settings...")
        n = self.settings_manager.get('gpu_max_prefetch_pages', 9)
        gpu_cache_size = (n * 2) + 2
        self.texture_manager.set_max_cache_size(gpu_cache_size)

    def displayImage(self, keys: list[str]):
        """表示する画像のキーリストを受け取り、再描画をトリガーする。"""
        logging.debug(f"[DEBUG_HAYATE] OpenGLView.displayImage called with keys: {keys}")
        
        # 古いキーのピン留め解除と新しいキーのピン留め
        # TextureManager/Cacheがピン留めロジックを持つべきだが、
        # 現在のTextureCacheは明示的なunpinが必要。
        # ここではTextureCacheに直接アクセスして制御する（リファクタリングの余地あり）
        for old_key in self.display_keys:
            self.texture_manager.texture_cache.unpin(old_key)

        self.display_keys = keys
        
        for key in self.display_keys:
            self.texture_manager.texture_cache.pin(key)

        # 足りないテクスチャのロードをリクエスト
        for key in self.display_keys:
            if key not in self.texture_manager.texture_cache:
                try:
                    page_index = int(key.split('::')[-1])
                    self.request_load_image.emit(page_index, 0)
                except (ValueError, IndexError):
                    logging.warning(f"Could not parse page index from key: {key}")

        self.zoom_reset()
        self.update()

    def clear_view(self):
        """ビューをクリアし、表示キーをリセットする。"""
        self.display_keys = []
        self.update()

    def prepare_texture(self, key: str, qimage: QImage):
        """テクスチャを即座に準備する。"""
        # プリフェッチ範囲チェックなどのロジックはTextureManagerに移動するか、
        # ここに残すか。Viewのロジック（表示範囲）に依存するのでここに残すのが適切か。
        if self.app_state.gpu_prefetch_range:
            try:
                page_index = int(key.split('::')[-1])
                is_display_target = key in self.display_keys
                is_in_prefetch_range = page_index in self.app_state.gpu_prefetch_range
                
                if not is_display_target and not is_in_prefetch_range:
                    return
            except (ValueError, IndexError):
                return

        self.texture_manager.prepare_texture(key, qimage)
        self.update() # paintGLで処理させるために更新

    def initializeGL(self):
        self.renderer.initialize()
        self.shader_manager.load_shaders()
        self.view_initialized.emit()

    def paintGL(self):
        # キューにあるテクスチャ生成リクエストを処理
        self.texture_manager.process_prepare_queue(self.settings_manager)
        
        # 一旦描画
        self.renderer.render(
            self.width(), self.height(), self.devicePixelRatio(),
            self.display_keys, self.zoom_level, self.pan_offset
        )

    def resizeGL(self, w, h):
        # Renderer側でglViewportを設定するので、ここでは特に何もしなくて良いが、
        # QOpenGLWidgetの仕様としてオーバーライドしておく。
        pass

    def _clamp_pan_offset(self):
        # Rendererに計算ロジックを移譲するか、Viewに残すか。
        # ズーム/パンはViewの操作なので、Viewに残すのが自然。
        # ただし、画像のサイズ情報が必要。TextureManagerから取得する。
        if not self.display_keys: return

        images_to_render = [self.texture_manager.get_texture(key) for key in self.display_keys]
        images_to_render = [img for img in images_to_render if img is not None]
        if not images_to_render: return

        total_img_w = sum(img['width'] for img in images_to_render)
        max_img_h = max(img['height'] for img in images_to_render)
        
        ratio = self.devicePixelRatio()
        view_w = int(self.width() * ratio)
        view_h = int(self.height() * ratio)

        if view_w == 0 or view_h == 0 or total_img_w == 0 or max_img_h == 0: return

        view_aspect = view_w / view_h
        total_img_aspect = total_img_w / max_img_h
        if view_aspect > total_img_aspect:
            fit_scale = view_h / max_img_h
        else:
            fit_scale = view_w / total_img_w

        scaled_w = total_img_w * fit_scale * self.zoom_level
        scaled_h = max_img_h * fit_scale * self.zoom_level

        max_pan_x = max(0, (scaled_w - view_w) / 2.0)
        max_pan_y = max(0, (scaled_h - view_h) / 2.0)

        clamped_x = max(-max_pan_x, min(max_pan_x, self.pan_offset.x()))
        clamped_y = max(-max_pan_y, min(max_pan_y, self.pan_offset.y()))
        self.pan_offset.setX(clamped_x)
        self.pan_offset.setY(clamped_y)

    # --- イベントハンドラ ---
    def keyPressEvent(self, event: QKeyEvent):
        if event.key() == Qt.Key.Key_F12:
            self.renderer.debug_save_render = True
            self.update()
            event.accept()
            return
        self.keyPressed.emit(event)

    def wheelEvent(self, event):
        event.accept()
        self.wheelScrolled.emit(event.angleDelta().y())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton:
            self.loupe_active = True
            self.original_zoom_level = self.zoom_level
            self.original_pan_offset = QPointF(self.pan_offset)
            self.last_pan_pos = event.pos()
            self._zoom(2.0)
        elif event.button() == Qt.MouseButton.LeftButton:
            if self.zoom_level != 1.0:
                self.panning_active = True
                self.last_pan_pos = event.pos()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.loupe_active or self.panning_active:
            delta = event.pos() - self.last_pan_pos
            if self.zoom_level != 0:
                self.pan_offset += QPointF(delta)
            self.last_pan_pos = event.pos()
            self._clamp_pan_offset()
            self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.RightButton and self.loupe_active:
            self.loupe_active = False
            self.zoom_level = self.original_zoom_level
            self.pan_offset = self.original_pan_offset
            self.update()
        elif event.button() == Qt.MouseButton.LeftButton and self.panning_active:
            self.panning_active = False
        super().mouseReleaseEvent(event)

    def event(self, event: QEvent) -> bool:
        """イベントハンドラをオーバーライドしてジェスチャーイベントを処理する。"""
        if event.type() == QEvent.Type.Gesture:
            return self.gestureEvent(event)
        return super().event(event)

    def gestureEvent(self, event: QGestureEvent) -> bool:
        """ジェスチャーイベントの振り分け処理。"""
        if pinch := event.gesture(Qt.GestureType.PinchGesture):
            self.pinchTriggered(pinch)
        if pan := event.gesture(Qt.GestureType.PanGesture):
            self.panTriggered(pan)
        if swipe := event.gesture(Qt.GestureType.SwipeGesture):
            self.swipeTriggeredHandler(swipe)
        return True

    def panTriggered(self, gesture: QPanGesture):
        """パンジェスチャー（スクロール）の処理。"""
        # ズーム中はパン（移動）処理を行う
        # 既存のmouseMoveEventでの実装と競合しないように注意が必要。
        # QPanGestureを使うとよりスムーズな慣性スクロールなどが実装できるが、
        # ここでは既存のロジック（mouseMoveEvent）を優先し、
        # 必要であればここに実装を移す。
        pass

    def mouseDoubleClickEvent(self, event):
        """ダブルクリック（ダブルタップ）イベントの処理。"""
        if self.zoom_level == 1.0:
            if event.button() == Qt.MouseButton.LeftButton:
                # 画面の左側か右側かを判定
                if event.pos().x() < self.width() / 2:
                    logging.info("Double Tap Left Detected")
                    self.swipeTriggered.emit("left")
                else:
                    logging.info("Double Tap Right Detected")
                    self.swipeTriggered.emit("right")
        super().mouseDoubleClickEvent(event)

    def pinchTriggered(self, gesture: QPinchGesture):
        """ピンチジェスチャー（ズーム）の処理。"""
        change_flags = gesture.changeFlags()
        if change_flags & QPinchGesture.ChangeFlag.ScaleFactorChanged:
            scale_factor = gesture.scaleFactor()
            self._zoom(scale_factor)

    def swipeTriggeredHandler(self, gesture: QSwipeGesture):
        """スワイプジェスチャー（ページめくり）の処理。"""
        if gesture.state() == Qt.GestureState.GestureFinished:
            if gesture.horizontalDirection() == QSwipeGesture.SwipeDirection.Left:
                logging.info("Swipe Left Detected")
                self.swipeTriggered.emit("left")
            elif gesture.horizontalDirection() == QSwipeGesture.SwipeDirection.Right:
                logging.info("Swipe Right Detected")
                self.swipeTriggered.emit("right")
            elif gesture.verticalDirection() == QSwipeGesture.SwipeDirection.Up:
                self.swipeTriggered.emit("up")
            elif gesture.verticalDirection() == QSwipeGesture.SwipeDirection.Down:
                self.swipeTriggered.emit("down")

    def zoom_in(self):
        self._zoom(1.15)

    def zoom_out(self):
        self._zoom(1 / 1.15)

    def _zoom(self, factor):
        old_zoom = self.zoom_level
        new_zoom = self.zoom_level * factor
        if new_zoom == 0: return

        cursor_pos = QPointF(self.mapFromGlobal(self.cursor().pos()))
        view_center = QPointF(self.width() / 2.0, self.height() / 2.0)

        if old_zoom != 0:
            pan_delta = (view_center - cursor_pos) * (1/old_zoom - 1/new_zoom)
            self.pan_offset += pan_delta

        self.zoom_level = new_zoom
        self._clamp_pan_offset()
        self.update()

    def zoom_reset(self):
        self.zoom_level = 1.0
        self.pan_offset = QPointF(0.0, 0.0)
        self.update()

    def pil_to_qimage(self, pil_image):
        if pil_image is None: return None
        try:
            if isinstance(pil_image, np.ndarray):
                if pil_image.shape[2] == 3:
                    pil_image = Image.fromarray(cv2.cvtColor(pil_image, cv2.COLOR_BGR2RGB))
                elif pil_image.shape[2] == 4:
                    pil_image = Image.fromarray(cv2.cvtColor(pil_image, cv2.COLOR_BGRA2RGBA))
                else:
                    pil_image = Image.fromarray(pil_image)

            if pil_image.mode != 'RGBA':
                pil_image = pil_image.convert('RGBA')

            data = pil_image.tobytes()
            return QImage(data, pil_image.width, pil_image.height, QImage.Format.Format_RGBA8888)
        except Exception as e:
            logging.error(f"Failed to convert PIL/NumPy image to QImage: {e}", exc_info=True)
            return None

    def on_gpu_prefetch_request(self, index: int):
        logging.info(f"==> [OpenGLView] Received GPU prefetch request for index: {index}")
        if not self.app_state or not self.app_state.file_loader:
            return

        key = f"{self.app_state.file_loader.path}::{index}"
        pil_image = self.image_cache.get(index)
        if pil_image is not None:
            qimage = self.pil_to_qimage(pil_image)
            if qimage:
                self.prepare_texture(key, qimage)
        else:
            self.request_load_image.emit(index, 1)

    def on_page_cached(self, page_number: int):
        if not self.app_state or not self.app_state.file_loader:
            return

        key = f"{self.app_state.file_loader.path}::{page_number}"
        
        # TextureManagerがまだ初期化されていない場合はスキップ
        if not hasattr(self, 'texture_manager'):
            return

        if key in self.texture_manager.texture_cache or key in self.texture_manager.prepare_queue:
            return

        pil_image = self.image_cache.get(page_number)
        if pil_image is not None:
            qimage = self.pil_to_qimage(pil_image)
            if qimage:
                QTimer.singleShot(0, lambda: self.prepare_texture(key, qimage))

    @Slot()
    def on_resampling_mode_changed(self):
        logging.info("Resampling mode changed. Reloading shaders.")
        self.makeCurrent()
        self.shader_manager.load_shaders()
        self.doneCurrent()
        self.update()

    def cleanup(self):
        self.makeCurrent()
        self.texture_manager.clear()
        self.shader_manager.cleanup()
        self.renderer.cleanup()
        self.doneCurrent()
