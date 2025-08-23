from __future__ import annotations
import os
import sys
import io
import numpy as np
import ctypes
import cv2
import logging
import datetime
from typing import TYPE_CHECKING
from PIL import Image
from PySide6.QtOpenGLWidgets import QOpenGLWidget
from PySide6.QtGui import QKeyEvent, QImage, QPalette
from PySide6.QtCore import Signal, Qt, QPoint, QPointF, QTimer, QMetaObject, Q_ARG, Slot
from OpenGL.GL import *
from OpenGL.GL.shaders import compileShader, compileProgram

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow
    from app.core.events import EventBus

from app.core.cache import TextureCache


def resource_path(relative_path):
    """ Get absolute path to resource, works for dev and for PyInstaller """
    try:
        # PyInstallerが作成した一時フォルダのパスを取得
        base_path = sys._MEIPASS
    except Exception:
        # PyInstallerで実行されていない場合（開発環境）は、カレントディレクトリを基準にする
        base_path = os.path.abspath(".")

    return os.path.join(base_path, relative_path)


class OpenGLView(QOpenGLWidget):
    """OpenGLとシェーダーを使用してレンダリングするビュー。"""
    keyPressed = Signal(QKeyEvent)
    wheelScrolled = Signal(int)
    texture_prepared = Signal(str) # key
    request_load_image = Signal(int, int) # page_index, priority
    view_initialized = Signal()

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
        
        # GPUテクスチャキャッシュサイズを設定から読み込む
        n = self.settings_manager.get('gpu_max_prefetch_pages', 9)
        gpu_cache_size = (n * 2) + 2 # +2は現在表示中のページ分
        self.texture_cache = TextureCache(max_size=gpu_cache_size, app_state=self.app_state)
        self.prepare_queue = {}
        self.display_keys = []

        self.shader_program = None
        self.vao = None
        self.vbo = None
        self.zoom_level = 1.0
        self.pan_offset = QPointF(0.0, 0.0)

        # ルーペ機能用の状態変数
        self.loupe_active = False
        self.panning_active = False
        self.original_zoom_level = 1.0
        self.original_pan_offset = QPointF(0.0, 0.0)
        self.last_pan_pos = QPoint()

        # キーイベントを受け取るためにフォーカスポリシーを設定
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        # デフォルトの背景色を取得
        palette = self.palette()
        bg_color = palette.color(QPalette.ColorRole.Window)
        
        # OpenGLのクリアカラー用に正規化
        self.clear_color = (bg_color.redF(), bg_color.greenF(), bg_color.blueF(), bg_color.alphaF())

        # デバッグ用フラグ
        self.debug_save_render = False

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
        # Priority 0: Display
        self.request_load_image.emit(index, 0)

    @Slot(QImage, int)
    def on_image_loaded(self, qimage: QImage, index: int):
        """画像が非同期で読み込まれたときに呼び出されるスロット。"""
        if not qimage or qimage.isNull():
            return

        key = f"{self.app_state.file_loader.path}::{index}"
        
        # テクスチャの準備を行う
        self.prepare_texture(key, qimage)

        # 読み込まれた画像が現在表示すべきキーのリストに含まれているか確認
        if key in self.display_keys:
            # paintGLで新しいテクスチャが使われるように、再描画をスケジュールする
            self.update()

    def update_settings(self):
        """設定変更を動的に適用する。"""
        logging.debug("[OpenGLView] Updating settings...")
        n = self.settings_manager.get('gpu_max_prefetch_pages', 9)
        gpu_cache_size = (n * 2) + 2
        self.texture_cache.set_max_size(gpu_cache_size)

    def displayImage(self, keys: list[str]):
        """表示する画像のキーリストを受け取り、再描画をトリガーする。"""
        logging.debug(f"[DEBUG_HAYATE] OpenGLView.displayImage called with keys: {keys}")
        
        # 古いキーのピン留めを解除
        for old_key in self.display_keys:
            self.texture_cache.unpin(old_key)

        self.display_keys = keys
        
        # 新しいキーをピン留め
        for key in self.display_keys:
            self.texture_cache.pin(key)

        # 足りないテクスチャのロードをリクエスト
        for key in self.display_keys:
            if key not in self.texture_cache:
                try:
                    page_index = int(key.split('::')[-1])
                    # 優先度0でロードをリクエスト
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
        """
        テクスチャを即座に準備する。
        このメソッドはメインスレッドから呼び出されることを想定している。
        """
        # 起動時など、Prefetcherが範囲をまだ設定していない場合はフィルタリングしない
        if self.app_state.gpu_prefetch_range:
            try:
                page_index = int(key.split('::')[-1])

                # 表示対象キーリストに含まれているかチェック
                is_display_target = key in self.display_keys
                
                # プリフェッチ範囲内かどうかもチェック
                is_in_prefetch_range = page_index in self.app_state.gpu_prefetch_range
                
                # 表示対象でもなく、プリフェッチ範囲にも含まれていない場合のみスキップ
                if not is_display_target and not is_in_prefetch_range:
                    logging.debug(f"Page {page_index} is outside display ({self.display_keys}) and prefetch range ({self.app_state.gpu_prefetch_range}). Skipping texture preparation.")
                    return

            except (ValueError, IndexError):
                logging.warning(f"Could not parse page index from key: {key} in prepare_texture.")
                # キーがパースできない場合は、念のため準備を続行しない
                return

        if key in self.texture_cache or key in self.prepare_queue:
            return

        if not qimage or qimage.isNull():
            logging.warning(f"Cannot prepare texture for key '{key}', QImage is null.")
            return

        # デッドロックを避けるため、即時実行をやめ、キューに追加してpaintGLで処理させる
        self.prepare_queue[key] = qimage
        self.update() # paintGLの呼び出しをスケジュール

    def _prepare_texture_internal(self, key: str, image: QImage):
        """テクスチャを実際に生成する内部メソッド。paintGLから呼び出される。"""
        if key in self.texture_cache:
            return

        if not image or image.isNull():
            logging.warning(f"Cannot prepare texture for key '{key}', QImage is null.")
            return

        import time
        start_time = time.perf_counter()

        try:
            logging.debug(f"[{key}] _prepare_texture_internal: Generating texture...")
            texture_id = glGenTextures(1)
            logging.debug(f"[{key}] _prepare_texture_internal: Binding texture {texture_id}...")
            glBindTexture(GL_TEXTURE_2D, texture_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            resampling_mode = self.settings_manager.get('resampling_mode_gl', 'GL_LANCZOS3')
            texture_filter = GL_NEAREST if resampling_mode == 'GL_NEAREST' else GL_LINEAR
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, texture_filter)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, texture_filter)
            
            logging.debug(f"[{key}] _prepare_texture_internal: Converting QImage to RGBA format...")
            image = image.convertToFormat(QImage.Format.Format_RGBA8888)
            mv = memoryview(image.bits())
            logging.debug(f"[{key}] _prepare_texture_internal: Calling glTexImage2D...")
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, image.width(), image.height(), 0, GL_RGBA, GL_UNSIGNED_BYTE, mv)
            logging.debug(f"[{key}] _prepare_texture_internal: glTexImage2D finished.")
            
            self.texture_cache.set(key, {
                'texture_id': texture_id,
                'width': image.width(),
                'height': image.height()
            })
            glBindTexture(GL_TEXTURE_2D, 0)
            logging.debug(f"[{key}] _prepare_texture_internal: Texture generation complete.")
        except Exception as e:
            logging.error(f"Error preparing texture for key {key}: {e}", exc_info=True)
        
        end_time = time.perf_counter()
        page_number = key.split('::')[-1] if '::' in key else 'N/A'
        logging.info(f"Prepare texture time for page {page_number}: {(end_time - start_time) * 1000:.2f} ms")
        self.texture_prepared.emit(key)


    def initializeGL(self):
        glClearColor(*self.clear_color)
        glEnable(GL_FRAMEBUFFER_SRGB)
        self.shader_program = self._create_shader_program()
        self.vao, self.vbo = self._create_quad_vbo()
        self.view_initialized.emit()

    def paintGL(self):
        logging.info(f"[paintGL] Called. Display keys: {self.display_keys}")
        self._delete_pending_textures()

        # --- 要求キュー内のテクスチャを準備 ---
        if self.prepare_queue:
            queue_to_process = self.prepare_queue.copy()
            self.prepare_queue.clear()
            logging.debug(f"[paintGL] Processing prepare_queue with {len(queue_to_process)} items.")
            for key, qimage in queue_to_process.items():
                self._prepare_texture_internal(key, qimage)
            logging.debug("[paintGL] Finished processing prepare_queue.")
        
        glClear(GL_COLOR_BUFFER_BIT)
        if not self.display_keys or self.shader_program is None:
            logging.debug("[paintGL] No image or shader, returning.")
            return

        logging.debug("[paintGL] Using shader program and binding VAO.")
        if self.shader_program is not None:
            glUseProgram(self.shader_program)
        glBindVertexArray(self.vao)
        
        ratio = self.devicePixelRatio()
        view_w = int(self.width() * ratio)
        view_h = int(self.height() * ratio)
        glViewport(0, 0, view_w, view_h)

        # GPUキャッシュのヒット/ミスをログに出力
        for key in self.display_keys:
            page_number = key.split('::')[-1] if '::' in key else 'N/A'
            in_cache = key in self.texture_cache
            logging.info(f"[TIMING_PAINT] Checking L1 cache for key '{key}' (page {page_number}). In cache: {in_cache}")

        # 描画対象のすべてのページのテクスチャが準備完了しているかを確認
        if not all(self.texture_cache.get(key) is not None for key in self.display_keys):
            logging.warning(f"Not all textures are ready for display_keys: {self.display_keys}. Waiting for all textures to be prepared.")
            return

        images_to_render = [self.texture_cache.get(key) for key in self.display_keys]

        logging.info(f"[OpenGLView] paintGL: Rendering {len(images_to_render)} pages. Keys: {self.display_keys}")

        if len(images_to_render) == 1:
            # 単ページ表示
            img_data = images_to_render[0]
            img_w, img_h = img_data['width'], img_data['height']

            if view_w == 0 or view_h == 0 or img_w == 0 or img_h == 0:
                return

            view_aspect = view_w / view_h
            img_aspect = img_w / img_h

            if view_aspect > img_aspect:
                fit_scale = view_h / img_h
            else:
                fit_scale = view_w / img_w

            img_w_ndc = (img_w * fit_scale / view_w) * 2.0
            img_h_ndc = (img_h * fit_scale / view_h) * 2.0

            scale_matrix = np.array([[img_w_ndc / 2.0, 0, 0, 0], [0, img_h_ndc / 2.0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float32)
            pan_x_ndc = (self.pan_offset.x() / view_w) * 2.0
            pan_y_ndc = -(self.pan_offset.y() / view_h) * 2.0
            trans_matrix = np.array([[1, 0, 0, pan_x_ndc], [0, 1, 0, pan_y_ndc], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float32)
            zoom_matrix = np.array([[self.zoom_level, 0, 0, 0], [0, self.zoom_level, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float32)
            
            transform_matrix = zoom_matrix @ trans_matrix @ scale_matrix
            self._draw_with_transform(img_data['texture_id'], img_w, img_h, transform_matrix)

        elif len(images_to_render) == 2:
            # 見開き表示
            total_img_w = sum(img['width'] for img in images_to_render)
            max_img_h = max(img['height'] for img in images_to_render)

            if view_w == 0 or view_h == 0 or total_img_w == 0 or max_img_h == 0:
                return

            view_aspect = view_w / view_h
            total_img_aspect = total_img_w / max_img_h

            if view_aspect > total_img_aspect:
                fit_scale = view_h / max_img_h
            else:
                fit_scale = view_w / total_img_w
            
            scaled_total_w_ndc = (total_img_w * fit_scale / view_w) * 2.0
            current_x_offset_ndc = -scaled_total_w_ndc / 2.0

            for img_data in images_to_render:
                img_w, img_h = img_data['width'], img_data['height']
                
                img_w_ndc = (img_w * fit_scale / view_w) * 2.0
                img_h_ndc = (img_h * fit_scale / view_h) * 2.0

                scale_matrix = np.array([[img_w_ndc / 2.0, 0, 0, 0], [0, img_h_ndc / 2.0, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float32)
                center_x_ndc = current_x_offset_ndc + (img_w_ndc / 2.0)
                pan_x_ndc = (self.pan_offset.x() / view_w) * 2.0
                pan_y_ndc = -(self.pan_offset.y() / view_h) * 2.0
                trans_matrix = np.array([[1, 0, 0, center_x_ndc + pan_x_ndc], [0, 1, 0, pan_y_ndc], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float32)
                zoom_matrix = np.array([[self.zoom_level, 0, 0, 0], [0, self.zoom_level, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], dtype=np.float32)
                
                transform_matrix = zoom_matrix @ trans_matrix @ scale_matrix
                self._draw_with_transform(img_data['texture_id'], img_w, img_h, transform_matrix)
                current_x_offset_ndc += img_w_ndc

        logging.debug("[paintGL] Unbinding VAO and shader program.")
        glBindVertexArray(0)
        glUseProgram(0)
        
        if self.debug_save_render:
            self._save_rendered_frame()
            self.debug_save_render = False # 1フレームだけ保存してフラグをリセット

    def _save_rendered_frame(self):
        """現在のレンダリング結果をPNGファイルとして保存する。"""
        try:
            ratio = self.devicePixelRatio()
            w = int(self.width() * ratio)
            h = int(self.height() * ratio)
            data = glReadPixels(0, 0, w, h, GL_RGBA, GL_UNSIGNED_BYTE)
            
            image = Image.frombytes("RGBA", (w, h), data)
            # OpenGLの座標系は左下が原点なので、上下を反転させる
            image = image.transpose(Image.FLIP_TOP_BOTTOM)

            filename = "debug_output.png"
            image.save(filename)
        except Exception as e:
            logging.error(f"Failed to save rendered frame: {e}", exc_info=True)

    def _draw_with_transform(self, texture_id, width, height, transform_matrix):
        if texture_id is None:
            return
            
        transform_loc = glGetUniformLocation(self.shader_program, "transform")
        glUniformMatrix4fv(transform_loc, 1, GL_TRUE, transform_matrix)

        glActiveTexture(GL_TEXTURE0)
        glBindTexture(GL_TEXTURE_2D, texture_id)
        glUniform1i(glGetUniformLocation(self.shader_program, "textureSampler"), 0)
        glUniform2f(glGetUniformLocation(self.shader_program, "sourceTextureSize"), width, height)

        glDrawArrays(GL_TRIANGLE_FAN, 0, 4)

    def resizeGL(self, w, h):
        # 高DPIディスプレイに対応するため、論理ピクセルではなく物理ピクセルを使用する
        ratio = self.devicePixelRatio()
        physical_width = int(w * ratio)
        physical_height = int(h * ratio)
        glViewport(0, 0, physical_width, physical_height)

    def reload_shaders(self):
        self.makeCurrent()
        if self.shader_program is not None:
            glDeleteProgram(self.shader_program)
        
        self.shader_program = self._create_shader_program()
        self.doneCurrent()
        self.update()

    def _create_shader_program(self):
        try:
            vertex_shader_path = resource_path(os.path.join('app', 'shaders', 'vertex_shader.glsl'))
            
            resampling_mode = self.settings_manager.get('resampling_mode_gl', 'GL_LANCZOS3')
            shader_map = {
                'GL_NEAREST': 'nearest_fragment.glsl',
                'GL_BILINEAR': 'bilinear_fragment.glsl',
                'GL_LANCZOS3': 'lanczos3_fragment.glsl',
                'GL_LANCZOS4': 'lanczos4_fragment.glsl',
                'GL_QUINTIC': 'quintic_fragment.glsl',
            }
            fragment_shader_file = shader_map.get(resampling_mode, 'lanczos3_fragment.glsl')
            fragment_shader_path = resource_path(os.path.join('app', 'shaders', fragment_shader_file))

            if not os.path.exists(fragment_shader_path):
                logging.error(f"Fragment shader file not found: {fragment_shader_path}. Falling back to lanczos3.")
                fragment_shader_path = resource_path(os.path.join('app', 'shaders', 'lanczos3_fragment.glsl'))

            with open(vertex_shader_path, 'r', encoding='utf-8') as f:
                vertex_shader_source = f.read()
            with open(fragment_shader_path, 'r', encoding='utf-8') as f:
                fragment_shader_source = f.read()

            vs = compileShader(vertex_shader_source, GL_VERTEX_SHADER)
            fs = compileShader(fragment_shader_source, GL_FRAGMENT_SHADER)
            program = compileProgram(vs, fs)
            return program
        except Exception as e:
            logging.error(f"シェーダーのコンパイルに失敗: {e}", exc_info=True)
            return None

    def _create_quad_vbo(self):
        vertices = [
            # positions   # texture coords (Y flipped)
            -1.0,  1.0,  0.0, 0.0,
            -1.0, -1.0,  0.0, 1.0,
             1.0, -1.0,  1.0, 1.0,
             1.0,  1.0,  1.0, 0.0,
        ]
        vertices = np.array(vertices, dtype=np.float32)

        vao = glGenVertexArrays(1)
        vbo = glGenBuffers(1)
        glBindVertexArray(vao)
        glBindBuffer(GL_ARRAY_BUFFER, vbo)
        glBufferData(GL_ARRAY_BUFFER, vertices.nbytes, vertices, GL_STATIC_DRAW)
        
        # Position attribute
        glVertexAttribPointer(0, 2, GL_FLOAT, GL_FALSE, 4 * vertices.itemsize, ctypes.c_void_p(0))
        glEnableVertexAttribArray(0)
        # Texture coord attribute
        glVertexAttribPointer(1, 2, GL_FLOAT, GL_FALSE, 4 * vertices.itemsize, ctypes.c_void_p(2 * vertices.itemsize))
        glEnableVertexAttribArray(1)
        
        glBindBuffer(GL_ARRAY_BUFFER, 0)
        glBindVertexArray(0)
        return vao, vbo

    def _delete_pending_textures(self):
        deleted_ids = self.texture_cache.get_deleted_textures()
        if deleted_ids:
            logging.info(f"[OpenGLView] Deleting {len(deleted_ids)} textures: {deleted_ids}")
            glDeleteTextures(deleted_ids)

    def cleanup(self):
        self.makeCurrent()
        self.texture_cache.clear()
        self._delete_pending_textures()
        if self.shader_program:
            glDeleteProgram(self.shader_program)
        if self.vao:
            glDeleteVertexArrays(1, [self.vao])
        if self.vbo:
            glDeleteBuffers(1, [self.vbo])
        self.doneCurrent()

    def _clamp_pan_offset(self):
        if not self.display_keys:
            return

        ratio = self.devicePixelRatio()
        view_w = int(self.width() * ratio)
        view_h = int(self.height() * ratio)
        if view_w == 0 or view_h == 0:
            return

        images_to_render = [self.texture_cache.get(key) for key in self.display_keys]
        images_to_render = [img for img in images_to_render if img is not None]
        if not images_to_render:
            return

        total_img_w = sum(img['width'] for img in images_to_render)
        max_img_h = max(img['height'] for img in images_to_render)
        
        if total_img_w == 0 or max_img_h == 0:
            return

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

    def keyPressEvent(self, event: QKeyEvent):
        # このウィジェットで直接処理するキー
        if event.key() == Qt.Key.Key_F12:
            self.debug_save_render = True
            self.update()
            event.accept()
            return

        # その他のキーイベントはすべて上位のEventHandlerに委譲する
        self.keyPressed.emit(event)
        # super().keyPressEvent(event) # 重複してイベントが処理されるのを防ぐためコメントアウト

    def focusInEvent(self, event):
        super().focusInEvent(event)

    def focusOutEvent(self, event):
        super().focusOutEvent(event)

    def wheelEvent(self, event):
        event.accept()
        self.wheelScrolled.emit(event.angleDelta().y())

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.loupe_active = True
            self.original_zoom_level = self.zoom_level
            self.original_pan_offset = QPointF(self.pan_offset)
            self.last_pan_pos = event.pos()

            # ルーペ用のズーム処理
            self._zoom(2.0)

        elif event.button() == Qt.MouseButton.RightButton:
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
        if event.button() == Qt.MouseButton.LeftButton and self.loupe_active:
            self.loupe_active = False
            self.zoom_level = self.original_zoom_level
            self.pan_offset = self.original_pan_offset
            self.update()
        elif event.button() == Qt.MouseButton.RightButton and self.panning_active:
            self.panning_active = False
        super().mouseReleaseEvent(event)

    def zoom_in(self):
        self._zoom(1.15, pan_around_cursor=False)

    def zoom_out(self):
        self._zoom(1 / 1.15, pan_around_cursor=False)

    def _get_fit_scale(self):
        images_to_render = [self.texture_cache.get(key) for key in self.display_keys]
        images_to_render = [img for img in images_to_render if img is not None]
        if not images_to_render:
            return 1.0

        total_img_w = sum(img['width'] for img in images_to_render)
        max_img_h = max(img['height'] for img in images_to_render)
        ratio = self.devicePixelRatio()
        view_w = int(self.width() * ratio)
        view_h = int(self.height() * ratio)

        if view_w == 0 or view_h == 0 or total_img_w == 0 or max_img_h == 0:
            return 1.0

        view_aspect = view_w / view_h
        total_img_aspect = total_img_w / max_img_h
        if view_aspect > total_img_aspect:
            return view_h / max_img_h
        else:
            return view_w / total_img_w

    def _zoom(self, factor, pan_around_cursor=True):
        old_zoom = self.zoom_level
        new_zoom = self.zoom_level * factor
        if new_zoom == 0: return

        # 全てのズームはカーソル位置を基準に行う
        cursor_pos = QPointF(self.mapFromGlobal(self.cursor().pos()))
        view_center = QPointF(self.width() / 2.0, self.height() / 2.0)

        # 変換行列から導出された、カーソル位置を不動点とするための正しい計算式
        # pan_new = pan_old + (cursor - view_center) * (1/old_zoom - 1/new_zoom)
        if old_zoom != 0:
            # 補正ベクトルは、カーソル位置からビュー中心へ向かう方向でなければならない
            pan_delta = (view_center - cursor_pos) * (1/old_zoom - 1/new_zoom)
            self.pan_offset += pan_delta

        self.zoom_level = new_zoom
        
        self._clamp_pan_offset()
        self.update()

    def zoom_reset(self):
        self.zoom_level = 1.0
        self.pan_offset = QPointF(0.0, 0.0)
        self.update() # ズームリセット操作のたびに再描画を要求する

    def pil_to_qimage(self, pil_image):
        if pil_image is None: return None
        try:
            # If the image is a numpy array, convert it to a PIL Image
            if isinstance(pil_image, np.ndarray):
                # Assuming the numpy array is in BGR format from OpenCV
                if pil_image.shape[2] == 3: # BGR
                    pil_image = Image.fromarray(cv2.cvtColor(pil_image, cv2.COLOR_BGR2RGB))
                elif pil_image.shape[2] == 4: # BGRA
                    pil_image = Image.fromarray(cv2.cvtColor(pil_image, cv2.COLOR_BGRA2RGBA))
                else: # Grayscale or other formats
                    pil_image = Image.fromarray(pil_image)

            # Ensure image is in RGBA format for consistency
            if pil_image.mode != 'RGBA':
                pil_image = pil_image.convert('RGBA')

            # Create a QImage directly from the RGBA buffer from PIL.
            data = pil_image.tobytes()
            return QImage(data, pil_image.width, pil_image.height, QImage.Format.Format_RGBA8888)
        except Exception as e:
            logging.error(f"Failed to convert PIL/NumPy image to QImage: {e}", exc_info=True)
            return None

    @property
    def cached_page_count(self):
        return self.texture_cache.page_count

    def on_gpu_prefetch_request(self, index: int):
        """GPUプリフェッチ要求を処理するスロット。"""
        logging.info(f"==> [OpenGLView] Received GPU prefetch request for index: {index}")
        if not self.app_state or not self.app_state.file_loader:
            logging.warning("[OpenGLView] App state or file loader not available for prefetch.")
            return

        key = f"{self.app_state.file_loader.path}::{index}"

        # CPUキャッシュから画像データを取得
        pil_image = self.image_cache.get(index)
        if pil_image is not None:
            qimage = self.pil_to_qimage(pil_image)
            if qimage:
                self.prepare_texture(key, qimage)
            else:
                logging.warning(f"[OpenGLView] Could not convert PIL image to QImage for prefetch index {index}")
        else:
            logging.warning(f"[OpenGLView] Image not found in CPU cache for prefetch index {index}. Requesting load.")
            # Priority 1: Prefetch
            self.request_load_image.emit(index, 1)

    def on_page_cached(self, page_number: int):
        """
        CPUCacheからページがキャッシュされたときに呼び出されるスロット。
        テクスチャをバックグラウンドでGPUにアップロードする。
        """
        if not self.app_state or not self.app_state.file_loader:
            logging.warning(f"[OpenGLView] App state or file loader not available for page {page_number}")
            return

        key = f"{self.app_state.file_loader.path}::{page_number}"
        
        if key in self.texture_cache or key in self.prepare_queue:
            return # 既に処理中またはキャッシュ済み

        pil_image = self.image_cache.get(page_number)
        if pil_image is not None:
            qimage = self.pil_to_qimage(pil_image)
            if qimage:
                # メインスレッドをブロックしないように、QTimer.singleShotでテクスチャ準備をスケジュール
                QTimer.singleShot(0, lambda: self.prepare_texture(key, qimage))
            else:
                logging.warning(f"[OpenGLView] Could not convert PIL image to QImage for page {page_number}")
        else:
            # このケースは通常発生しないはず（シグナル発行直後のため）
            logging.warning(f"[OpenGLView] Image for page {page_number} disappeared from CPU cache before texture preparation.")

    @Slot()
    def on_resampling_mode_changed(self):
        """リサンプリングモードが変更されたときに呼び出されるスロット。"""
        logging.info("Resampling mode changed. Reloading shaders.")
        self.reload_shaders()
        self.update()
