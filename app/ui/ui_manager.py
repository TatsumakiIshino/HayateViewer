from __future__ import annotations
import logging
import cv2
from typing import TYPE_CHECKING
from PySide6.QtCore import QObject, QTimer, QMetaObject, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QMessageBox
from PIL import Image as PILImage

from app.image.resampler_mt import MultiThreadedImageResampler
from app.ui.views.default_view import DefaultGraphicsView
from app.ui.views.opengl_view import OpenGLView
from app.constants import RESAMPLING_MODES_CPU, RESAMPLING_MODES_GL

if TYPE_CHECKING:
    from app.core.state import AppState
    from app.ui.main_window import MainWindow
    from app.io.loader import FileLoader


class UIManager(QObject):
    """UIの更新と管理を専門に扱うクラス。"""
    first_image_ready = Signal()

    def __init__(self, main_window: MainWindow, app_state: AppState):
        """
        UIManagerのコンストラクタ。

        Args:
            main_window (MainWindow): メインウィンドウのインスタンス。
            app_state (AppState): アプリケーションの状態を管理するインスタンス。
        """
        super().__init__()
        self.main_window = main_window
        self.app_state = app_state
        self.app_state.file_list_changed.connect(self._on_content_loaded)
        self.app_state.page_index_changed.connect(self._on_page_index_changed)
        self.app_state.view_mode_changed.connect(self.update_view)
        self.file_loader: FileLoader | None = None
        self.is_first_image_handled = False

        # リサンプラーの初期化
        self.resampler = self._create_resampler()
        self.main_window.controller.event_bus.RESAMPLING_MODE_CHANGED.connect(self.on_resampling_mode_changed)

        # キャッシュ変更シグナルを遅延更新メソッドに接続
        self.main_window.controller.image_cache.cache_changed.connect(self._schedule_status_update)
        # PySide6モードでのプリフェッチ開始トリガー
        self.main_window.controller.image_cache.page_cached.connect(self.on_page_cached)

        if hasattr(self.main_window.view, 'texture_cache'):
             self.main_window.view.texture_cache.cache_changed.connect(self._schedule_status_update)
        
        if isinstance(self.main_window.view, OpenGLView):
            self.main_window.view.texture_prepared.connect(self.on_texture_prepared)

    def reset_view(self):
        """ビューの表示状態をリセットする。"""
        view = self.main_window.view
        if isinstance(view, OpenGLView):
            view.clear_view()
        # DefaultGraphicsViewにも同様のクリア処理が必要な場合はここに追加

    def _on_content_loaded(self):
        """コンテンツの読み込みが完了したときに呼び出されるスロット。"""
        self.on_file_list_changed()
        self.main_window.update_seek_widget_state()

    def _on_page_index_changed(self, index: int, is_spread: bool):
        """ページインデックスが変更されたときに呼び出されるスロット。"""
        self.main_window._on_app_state_page_changed(index, is_spread)
        self.update_view()

    def on_file_list_changed(self):
        """ファイルリストが変更されたときに呼び出される。"""
        self.file_loader = self.main_window.controller.file_loader
        self.is_first_image_handled = False # 新しいファイルリストでリセット
        if self.file_loader and self.file_loader.load_type != 'archive':
            # 書庫でない場合は、すぐに最初の画像を表示
            # self.update_view() # AppControllerの新しいロジックで制御されるためコメントアウト
            pass

    def handle_first_file_extracted(self, path: str):
        """最初のファイルが展開されたときに呼び出されるスロット。"""
        self._switch_to_opengl_view()
        if self.main_window.view and hasattr(self.main_window.view, 'update_image'):
            self.main_window.view.update_image(path)

    def _schedule_status_update(self):
        """UIの更新を現在のイベントサイクルの直後にスケジュールする。"""
        QTimer.singleShot(0, self.update_dynamic_status_info)

    @Slot(int)
    def on_page_cached(self, page_index: int):
        """
        ページがCPUキャッシュに保存されたときのハンドラ。
        PySide6モードでのプリフェッチ開始トリガーとして機能する。
        """
        # OpenGLモードでは texture_prepared を使うので、ここでは何もしない
        if isinstance(self.main_window.view, OpenGLView):
            return

        if not self.is_first_image_handled and page_index == 0:
            logging.info(f"[UIManager] First image (page 0) is cached for DefaultView. Emitting first_image_ready.")
            self.is_first_image_handled = True
            # AppControllerに通知して、UI更新とプリフェッチを開始させる
            self.first_image_ready.emit()

    @Slot(str)
    def on_texture_prepared(self, key: str):
        """最初のページのテクスチャが準備できたことを検知してシグナルを発行する。"""
        if self.is_first_image_handled:
            return
        
        try:
            page_index_str = key.rsplit('::', 1)[1]
            page_index = int(page_index_str)
            if page_index == 0:
                logging.info(f"[UIManager] First image texture (page 0) is ready. Emitting signal.")
                self.is_first_image_handled = True
                self.first_image_ready.emit()
        except (ValueError, IndexError):
            logging.warning(f"[UIManager] Could not parse page index from texture key: {key}")

    def handle_texture_prepared(self, key: str):
        """テクスチャ準備完了時のハンドラ。"""
        logging.info(f"[UIManager] Texture prepared for key: {key}. Updating status bar.")
        self.update_status_bar() # 静的情報を更新

    def _switch_to_opengl_view(self):
        """DefaultGraphicsViewからOpenGLViewに切り替える。"""
        if isinstance(self.main_window.view, DefaultGraphicsView):
            new_view = OpenGLView(
                self.app_state,
                self.main_window.settings_manager,
                self.main_window.controller.image_cache,
                self.main_window
            )
            old_view = self.main_window.image_viewer.current_view
            self.main_window.image_viewer.layout.removeWidget(old_view)
            old_view.deleteLater()
            self.main_window.image_viewer.layout.addWidget(new_view)
            self.main_window.image_viewer.current_view = new_view
            self.main_window.view = new_view
            new_view.keyPressed.connect(self.main_window.image_viewer.keyPressed)
            new_view.wheelScrolled.connect(self.main_window.image_viewer.wheelScrolled)

    def update_view(self, *args):
        """AppStateからのシグナルに基づいてビューを更新する。"""
        indices = self._get_page_indices_to_display()
        logging.debug(f"[DEBUG_HAYATE] UIManager.update_view called. Displaying indices: {indices}")
        self.update_status_bar()
        if not self.app_state.is_content_loaded or not self.file_loader:
            # コンテンツがロードされていない場合はビューをクリア
            if hasattr(self.main_window.view, 'displayImage'):
                self.main_window.view.displayImage([])
            return

        view = self.main_window.view
        keys_to_display = [f"{self.file_loader.path}::{index}" for index in indices]
        logging.info(f"[UIManager.update_view] Updating view with keys: {keys_to_display}")

        # 現在のレンダリングモードに応じて処理を分岐
        if isinstance(view, OpenGLView):
            # OpenGLViewはキーのリストを直接受け取る
            view.displayImage(keys_to_display)
        elif isinstance(view, DefaultGraphicsView):
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

    def _get_page_indices_to_display(self) -> list[int]:
        """
        現在のアプリケーション状態に基づいて、表示すべきページのインデックスリストを計算します。
        このメソッドは、表示ロジックの中核を担い、app_stateのみを信頼できる情報源とします。
        """
        current_index = self.app_state.current_page_index
        total_pages = self.app_state.total_pages

        # 1. 基本的なバリデーション
        if not (0 <= current_index < total_pages):
            return []

        # 2. 単ページ表示モードの処理 (要件1)
        if not self.app_state.is_spread_view:
            return [current_index]

        # --- ここから下は見開き表示モードのロジック ---

        is_first_page_single = self.app_state.spread_view_first_page_single

        # 3. 最初のページが単独表示される特別ケース (要件3)
        if is_first_page_single and current_index == 0:
            return [0]

        # 4. 見開きペアの計算 (要件2)
        # 「最初のページを単独表示」が有効な場合、ページのナンバリングが1つずれると考える。
        # このオフセットを考慮して、ペア計算の基準となるインデックスを調整する。
        offset = 1 if is_first_page_single else 0
        
        # current_indexがどのペアに属するかを計算する。
        # 調整済みインデックスを2で割ることで、ペアの最初のページを特定する。
        # 例 (offset=1): current_index=1,2 -> adj=0,1 -> start=1
        # 例 (offset=0): current_index=0,1 -> adj=0,1 -> start=0
        adjusted_index = current_index - offset
        if adjusted_index < 0: # current_index=0 かつ offset=1 のケース
            # このケースは要件3で処理済みだが、念のため。
            return [0]

        start_of_pair = (adjusted_index // 2) * 2 + offset
        page1 = start_of_pair
        page2 = start_of_pair + 1

        # 5. 最後のページが単独表示されるケース (要件4)
        if page2 >= total_pages:
            return [current_index]

        # 6. 綴じ方向の適用 (要件5)
        # Viewは渡されたリストの0番目を左、1番目を右のページとして描画することを想定している。
        if self.app_state.binding_direction == 'right':
            # 右綴じの場合、左側に大きいインデックス(page2)、右側に小さいインデックス(page1)が来る
            return [page2, page1]
        else:
            # 左綴じの場合、左側に小さいインデックス(page1)、右側に大きいインデックス(page2)が来る
            return [page1, page2]

    def handle_image_loaded(self, image, page_index):
        """
        ワーカから画像が読み込まれた際の統一的なハンドラ。
        現在のビューに応じて適切なメソッドを呼び出す。
        """
        view = self.main_window.view
        if isinstance(view, OpenGLView):
            # OpenGLViewはon_image_loadedスロットを持つ
            view.on_image_loaded(image, page_index)
        elif isinstance(view, DefaultGraphicsView):
            # 非同期で画像が読み込まれた場合、表示中のページに関連するものであれば
            # UI全体を再描画するのが最も確実。
            # これにより、見開き表示の片方だけが読み込まれた場合でも正しく表示が更新される。
            indices_to_display = self._get_page_indices_to_display()
            if page_index in indices_to_display:
                self.update_view()

    def update_status_bar(self) -> None:
        """静的なステータスバー情報（ページ、モードなど）を更新します。"""
        if not self.app_state.is_content_loaded:
            self.main_window.page_info_label.setText("ファイルを開くか、ウィンドウにドロップしてください")
            self.main_window.view_mode_label.setText("")
            self.main_window.rendering_backend_label.setText("Backend: -")
            self.main_window.resampling_label.setText("Resampling: -")
            return

        # --- ページ情報と表示モード ---
        total_pages = self.app_state.total_pages
        current_page_num = self.app_state.current_page_index + 1
        
        if self.app_state.is_spread_view:
            is_first_page_single = self.app_state.spread_view_first_page_single and self.app_state.current_page_index == 0
            page_str = f"{current_page_num}"
            if self.app_state.current_page_index + 1 < total_pages and not is_first_page_single:
                page_str += f"-{current_page_num + 1}"
            self.main_window.page_info_label.setText(f"Page: {page_str} / {total_pages}")
            
            binding_str = "右綴じ" if self.app_state.binding_direction == 'right' else "左綴じ"
            self.main_window.view_mode_label.setText(f"View: 見開き ({binding_str})")
        else:
            self.main_window.page_info_label.setText(f"Page: {current_page_num} / {total_pages}")
            self.main_window.view_mode_label.setText("View: 単ページ")

        # --- レンダリングバックエンド ---
        backend = self.main_window.settings_manager.get('rendering_backend')
        backend_map = {'pyside6': 'PySide6', 'pyside6_mt': 'PySide6 (MT)', 'opengl': 'OpenGL'}
        display_name = backend_map.get(backend, backend)
        self.main_window.rendering_backend_label.setText(f"Backend: {display_name}")

        # --- リサンプリング品質 ---
        if backend == 'opengl':
            mode_key = self.main_window.settings_manager.get('resampling_mode_gl')
            mode_name = RESAMPLING_MODES_GL.get(mode_key, "Unknown")
        else:
            mode_key = self.main_window.settings_manager.get('resampling_mode_cpu')
            mode_name = RESAMPLING_MODES_CPU.get(mode_key, "Unknown")
        self.main_window.resampling_label.setText(f"Resampling: {mode_name}")

    def update_dynamic_status_info(self) -> None:
        """動的なステータスバー情報（キャッシュ）をタイマーで更新します。"""
        if not self.app_state.is_content_loaded:
            self.main_window.cpu_cache_label.setText("CPU: -")
            self.main_window.gpu_cache_label.setText("GPU: -")
            return

        # --- CPUキャッシュ情報 ---
        cpu_cache = self.main_window.controller.image_cache
        cpu_pages = cpu_cache.page_count
        cpu_keys = sorted(list(cpu_cache.cache.keys()))
        self.main_window.cpu_cache_label.setText(f"CPU: {cpu_pages} pages {cpu_keys}")

        # --- GPUキャッシュ情報 ---
        if isinstance(self.main_window.view, OpenGLView):
            gpu_cache = self.main_window.view.texture_cache
            gpu_pages = gpu_cache.page_count
            
            gpu_keys = []
            if hasattr(gpu_cache, 'lock'):
                with gpu_cache.lock:
                    # キーをページインデックスに変換する
                    raw_keys = list(gpu_cache.cache.keys())
                    logging.debug(f"[UIManager] Raw GPU keys: {raw_keys}")
                    for key in raw_keys:
                        try:
                            # '::'で分割してページインデックスを取得
                            page_index_str = key.rsplit('::', 1)[1]
                            page_index = int(page_index_str)
                            gpu_keys.append(page_index)
                        except (ValueError, IndexError) as e:
                            logging.warning(f"[UIManager] Could not parse GPU cache key: {key}. Error: {e}")
            
            gpu_keys.sort()
            self.main_window.gpu_cache_label.setText(f"GPU: {gpu_pages} pages {gpu_keys}")
        else:
            self.main_window.gpu_cache_label.setText("GPU: N/A")

    def toggle_status_bar_info_visibility(self):
        """設定に応じてキャッシュ情報ラベルの表示/非表示を切り替えます。"""
        show = self.main_window.settings_manager.get('show_status_bar_info', True)
        self.main_window.cpu_cache_label.setVisible(show)
        
        is_opengl = self.main_window.settings_manager.get('rendering_backend') == 'opengl'
        self.main_window.gpu_cache_label.setVisible(show and is_opengl)

        if show:
            # 表示状態になったら一度更新する
            self.update_dynamic_status_info()

    def _create_resampler(self) -> MultiThreadedImageResampler:
        """設定に基づいてCPUリサンプラーを生成する。"""
        mode = self.main_window.settings_manager.get('resampling_mode_cpu', 'PIL_BILINEAR')
        workers = self.main_window.settings_manager.get('parallel_decoding_workers', 1)
        logging.info(f"Creating CPU resampler with mode: {mode}, workers: {workers}")
        return MultiThreadedImageResampler(mode=mode, max_threads=workers)

    @Slot()
    def on_resampling_mode_changed(self):
        """リサンプリングモード変更のイベントハンドラ。"""
        backend = self.main_window.settings_manager.get('rendering_backend')
        if backend == 'opengl':
            return
        
        logging.info("CPU resampling mode changed. Re-creating resampler and updating view.")
        self.resampler = self._create_resampler()
        self.update_view()

    def update_window_title(self) -> None:
        """ウィンドウのタイトルを更新します。"""
        # TODO: Implement window title update logic
        pass

    def zoom_in(self):
        if self.main_window.view:
            self.main_window.view.zoom_in()

    def zoom_out(self):
        if self.main_window.view:
            self.main_window.view.zoom_out()

    def zoom_reset(self):
        if self.main_window.view:
            self.main_window.view.zoom_reset()

    def show_error_dialog(self, message: str, title: str = "エラー") -> None:
        """
        エラーメッセージダイアログを表示します。

        Args:
            message (str): 表示するエラーメッセージ。
            title (str): ダイアログのタイトル。
        """
        QMessageBox.critical(self.main_window, title, message)

    def show_about_dialog(self):
        """バージョン情報ダイアログを表示します。"""
        version = "0.2.0"
        author = "Tatsumaki.ishino"
        team = "KID Project Team"
        QMessageBox.information(
            self.main_window,
            "About HayateViewer",
            f"HayateViewer\nVersion: {version}\n\nDeveloped by: {author}\nA {team} Production"
        )
