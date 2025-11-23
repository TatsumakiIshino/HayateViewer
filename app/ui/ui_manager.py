from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from PySide6.QtCore import QObject, QTimer, Slot, Signal

from app.ui.views.opengl_view import OpenGLView
from app.ui.managers.status_bar_manager import StatusBarManager
from app.ui.managers.title_manager import TitleManager
from app.ui.managers.view_manager import ViewManager
from app.ui.managers.dialog_manager import DialogManager

if TYPE_CHECKING:
    from app.core.state import AppState
    from app.ui.main_window import MainWindow
    from app.io.loader import FileLoader


class UIManager(QObject):
    """
    UIの更新と管理を専門に扱うクラス。
    各マネージャー（StatusBar, Title, View, Dialog）へのファサードとして機能する。
    """
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
        
        # サブマネージャーの初期化
        self.status_bar_manager = StatusBarManager(main_window, app_state)
        self.title_manager = TitleManager(main_window, app_state)
        self.view_manager = ViewManager(main_window, app_state)
        self.dialog_manager = DialogManager(main_window, main_window.controller)

        self.app_state.file_list_changed.connect(self._on_content_loaded)
        self.app_state.page_index_changed.connect(self._on_page_index_changed)
        self.app_state.view_mode_changed.connect(self.update_view)
        self.file_loader: FileLoader | None = None
        self.is_first_image_handled = False

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
        self.title_manager.update_window_title(self._get_page_indices_to_display())

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
            pass

    def handle_first_file_extracted(self, path: str):
        """最初のファイルが展開されたときに呼び出されるスロット。"""
        self.view_manager.switch_to_opengl_view()
        if self.main_window.view and hasattr(self.main_window.view, 'update_image'):
            self.main_window.view.update_image(path)

    def _schedule_status_update(self):
        """UIの更新を現在のイベントサイクルの直後にスケジュールする。"""
        QTimer.singleShot(0, self.status_bar_manager.update_dynamic_status_info)

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

    def update_view(self, *args):
        """AppStateからのシグナルに基づいてビューを更新する。"""
        indices = self._get_page_indices_to_display()
        logging.debug(f"[DEBUG_HAYATE] UIManager.update_view called. Displaying indices: {indices}")
        
        self.status_bar_manager.update_status_bar(indices)
        self.title_manager.update_window_title(indices)
        self.view_manager.update_view(indices)

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

        # 2. 単ページ表示モードの処理
        if not self.app_state.is_spread_view:
            return [current_index]

        # --- ここから下は見開き表示モードのロジック ---

        is_first_page_single = self.app_state.spread_view_first_page_single
        folder_start_indices = self.app_state.folder_start_indices
        
        # 単独表示されるページのインデックスリストを作成
        single_page_indices = set()
        if is_first_page_single:
            single_page_indices.add(0)
            single_page_indices.update(folder_start_indices)
        
        # 3. 現在のページが単独表示ページの場合
        if current_index in single_page_indices:
            return [current_index]

        # 4. 見開きペアの計算（新ロジック）
        # current_indexを基準にペアを形成する
        page1 = current_index
        page2 = current_index + 1

        # 5. 最後のページとフォルダ境界のチェック
        if page2 >= total_pages or page2 in single_page_indices:
            return [current_index]

        # 6. 綴じ方向の適用
        if self.app_state.binding_direction == 'right':
            return [page2, page1]  # 右綴じ: [2, 1], [4, 3] ...
        else:
            return [page1, page2]  # 左綴じ: [1, 2], [3, 4] ...

    def handle_image_loaded(self, image, page_index):
        """
        ワーカから画像が読み込まれた際の統一的なハンドラ。
        現在のビューに応じて適切なメソッドを呼び出す。
        """
        view = self.main_window.view
        if isinstance(view, OpenGLView):
            # OpenGLViewはon_image_loadedスロットを持つ
            view.on_image_loaded(image, page_index)
        elif hasattr(view, 'displayImage'): # DefaultGraphicsView
            # 非同期で画像が読み込まれた場合、表示中のページに関連するものであれば
            # UI全体を再描画するのが最も確実。
            indices_to_display = self._get_page_indices_to_display()
            if page_index in indices_to_display:
                self.update_view()

    def update_status_bar(self) -> None:
        """静的なステータスバー情報（ページ、モードなど）を更新します。"""
        self.status_bar_manager.update_status_bar(self._get_page_indices_to_display())

    def update_dynamic_status_info(self) -> None:
        """動的なステータスバー情報（キャッシュ）をタイマーで更新します。"""
        self.status_bar_manager.update_dynamic_status_info()

    def toggle_status_bar_info_visibility(self):
        """設定に応じてキャッシュ情報ラベルの表示/非表示を切り替えます。"""
        self.status_bar_manager.toggle_status_bar_info_visibility()

    def update_window_title(self) -> None:
        """ウィンドウのタイトルを更新します。"""
        self.title_manager.update_window_title(self._get_page_indices_to_display())

    def zoom_in(self):
        self.view_manager.zoom_in()

    def zoom_out(self):
        self.view_manager.zoom_out()

    def zoom_reset(self):
        self.view_manager.zoom_reset()

    def show_error_dialog(self, message: str, title: str = "エラー") -> None:
        self.dialog_manager.show_error_dialog(message, title)

    def show_about_dialog(self):
        self.dialog_manager.show_about_dialog()

    def show_status_message(self, message: str, timeout: int = 0):
        """ステータスバーにメッセージを表示する。"""
        self.main_window.status_bar.showMessage(message, timeout)
