from __future__ import annotations
import typing
import logging
from PySide6.QtCore import Qt, QObject, Slot
from app.constants import PRIORITY_DISPLAY
from app.io.loader import FileLoader
from app.core.cache import ImageCache
from app.core.events import EventBus, EventHandler
from app.core.thread_manager import ThreadManager

# MainWindowとUIManagerを実行時にインポートする
from app.ui.main_window import MainWindow
from app.ui.ui_manager import UIManager

if typing.TYPE_CHECKING:
    from app.core.state import AppState
    from app.config.settings import Settings


class ApplicationController(QObject):
    """
    Main controller for the application.

    This class is responsible for orchestrating the main components of the application,
    including UI, state management, and background threads.
    """

    def __init__(self, app_state: AppState, settings_manager: Settings):
        """
        Initialize the ApplicationController.

        Args:
            app_state: The application state manager.
            settings_manager: The settings manager.
        """
        super().__init__()
        self.app_state = app_state
        self.settings_manager = settings_manager
        self.event_bus = EventBus()
        self.image_cache = ImageCache(settings_manager)
        self.thread_manager = ThreadManager(self.app_state, self.settings_manager, self.image_cache, self.event_bus)
        self.file_loader: typing.Optional[FileLoader] = None
        self.ui_manager: typing.Optional[UIManager] = None
        self._is_loading = False
        self._is_first_image_ready = False
        self.main_window: typing.Optional[MainWindow] = None
        self.event_handler: typing.Optional[EventHandler] = None

    def start(self) -> None:
        """
        Start the application.

        Initializes and shows the main window and sets up other components.
        """
        # 1. MainWindowをインスタンス化 (TextureCacheの取得に必要)
        self.main_window = MainWindow(self)
        
        # 2. バックエンドスレッドを起動
        # UIより先にワーカを準備完了状態にする
        texture_cache = self.main_window.view.texture_cache if hasattr(self.main_window.view, 'texture_cache') else None
        self.thread_manager.setup_threads(texture_cache)

        # 3. UIManagerをインスタンス化
        self.ui_manager = UIManager(self.main_window, self.app_state)
        
        # 4. EventHandlerをインスタンス化
        self.event_handler = EventHandler(self.main_window, self.app_state, self.settings_manager, self.event_bus)

        # 5. MainWindowに各マネージャーを接続
        self.main_window.set_ui_manager(self.ui_manager)
        self.main_window.set_event_handler(self.event_handler)

        # 6. シグナル接続
        self.connect_signals()

        # 7. ウィンドウを表示
        self.main_window.show()


    def load_path(self, path: str, page: int = 0) -> None:
        """
        Load a new file or directory path.
        This method is now responsible for creating a FileLoader and passing it to the ThreadManager.
        """
        logging.info(f"--- STARTING NEW LOAD OPERATION FOR: {path} ---")
        if self._is_loading:
            logging.warning(f"Load operation already in progress. Ignoring request for: {path}")
            return

        self._is_loading = True
        self._is_first_image_ready = False # 新しい読み込みが開始されたらフラグをリセット

        # 1. 既存のFileLoaderがあれば、安全な削除をスケジュールし、参照をクリア
        if self.file_loader:
            logging.info(f"Scheduling previous FileLoader for deletion (id: {id(self.file_loader)})")
            self.file_loader.deleteLater() # Qtのオブジェクト削除のベストプラクティスに従う
            self.file_loader = None

        # 2. AppStateの各プロパティをリセット
        self.app_state.image_files = []
        self.app_state.total_pages = 0
        self.app_state.is_content_loaded = False
        self.app_state.current_page_index = -1 # 無効なインデックスに設定
        self.app_state.file_loader = None
        
        # 3. UIとキャッシュをクリアする
        logging.debug("[DEBUG_HAYATE] load_path called. Clearing UI, existing image and texture caches.")
        if self.ui_manager:
            self.ui_manager.reset_view()
        self.image_cache.clear()
        if self.main_window and self.main_window.view.objectName() == 'OpenGL':
            self.main_window.view.texture_cache.clear()
        
        logging.info(f"Attempting to load path: {path}")
        self.app_state.current_file_path = path

        try:
            # 4. 新しいFileLoaderをインスタンス化
            logging.info(f"Creating new FileLoader for path: {path}")
            loader = FileLoader(path=path, parent=self)
            image_files = loader.get_image_list()

            if not image_files:
                logging.warning(f"No images found in: {path}")
                loader.deleteLater() # ここでも不要になったloaderは削除
                return

            # 5. AppStateと自身のfile_loaderを更新
            self.file_loader = loader
            self.app_state.file_loader = loader
            self.app_state.set_file_list(image_files)
            self.app_state.current_page_index = page

            # 6. ThreadManagerに新しいFileLoaderを通知
            self.thread_manager.file_loader_updated.emit(loader)

            logging.info(f"Successfully loaded {len(image_files)} images from {path}")
            logging.info(f"--- FINISHED LOAD OPERATION SETUP FOR: {path} ---")

        except FileNotFoundError:
            logging.error(f"Error: File or directory not found at '{path}'")
            if self.ui_manager:
                self.ui_manager.show_error_dialog(f"ファイルまたはディレクトリが見つかりません。\nパス: {path}")
        except Exception as e:
            logging.error(f"An unexpected error occurred while loading '{path}': {e}", exc_info=True)
            if self.ui_manager:
                self.ui_manager.show_error_dialog(f"ファイルの読み込み中に予期せぬエラーが発生しました。\n詳細: {e}")
        finally:
            self._is_loading = False
            if self.ui_manager:
                self.ui_manager.update_view()

    def cleanup(self) -> None:
        """
        Clean up resources before application exit.
        """
        logging.info(f"Application controller cleanup started. (id: {id(self)})")
        if self.thread_manager:
            self.thread_manager.cleanup_threads()
        logging.info("Application controller cleanup finished.")

    def handle_settings_change(self, changed_settings):
        "設定変更のシグナルを処理する。"
        should_update_view = False

        if 'is_spread_view' in changed_settings:
            new_value = changed_settings['is_spread_view']
            if self.app_state.is_spread_view != new_value:
                self.app_state.is_spread_view = new_value
                should_update_view = True
        
        if 'spread_view_first_page_single' in changed_settings:
            new_value = changed_settings['spread_view_first_page_single']
            if self.app_state.spread_view_first_page_single != new_value:
                self.app_state.spread_view_first_page_single = new_value
                should_update_view = True

        if 'binding_direction' in changed_settings:
            new_value = changed_settings['binding_direction']
            if self.app_state.binding_direction != new_value:
                self.app_state.binding_direction = new_value
                should_update_view = True

        if 'show_status_bar_info' in changed_settings:
            if self.ui_manager:
                self.ui_manager.toggle_status_bar_info_visibility()

        if 'rendering_backend' in changed_settings:
            if self.ui_manager and self.ui_manager.main_window:
                self.ui_manager.main_window.show_restart_required_message()

        if 'resampling_mode_cpu' in changed_settings or 'resampling_mode_gl' in changed_settings:
            self.event_bus.RESAMPLING_MODE_CHANGED.emit()

        if should_update_view and self.ui_manager:
            self.ui_manager.update_view()

    def handle_clear_cache(self):
        "キャッシュクリアのシグナルを処理する。"
        self.image_cache.clear()
        if self.main_window and self.main_window.view.objectName() == 'OpenGL':
            self.main_window.view.texture_cache.clear()
        
        if self.ui_manager:
            self.ui_manager.update_view()


    def handle_cache_settings_change(self, cache_changes):
        "キャッシュ設定変更のシグナルを処理する。"
        if 'max_cache_size_mb' in cache_changes:
            self.image_cache.set_max_size(cache_changes['max_cache_size_mb'])
        
        # Prefetcherに関連する設定変更をThreadManager経由で通知
        prefetcher_keys = ['cpu_max_prefetch_pages', 'gpu_max_prefetch_pages']
        prefetcher_changes = {k: v for k, v in cache_changes.items() if k in prefetcher_keys}
        if prefetcher_changes:
            self.thread_manager.cache_settings_changed.emit(prefetcher_changes)

        if self.ui_manager:
            # 必要に応じてUI（ステータスバーなど）を更新
            pass

    def connect_signals(self):
        "Connect signals between components."
        image_loader = self.thread_manager.get_image_loader()
        prefetcher = self.thread_manager.get_prefetcher()

        if image_loader and self.main_window and self.ui_manager:
            # Worker -> UIManager (who then delegates to the correct view)
            image_loader.image_loaded.connect(self.ui_manager.handle_image_loaded)
            
            # ThreadManager -> Controller (when loader is ready)
            self.thread_manager.loader_ready.connect(self.on_loader_ready)

            # UIManager -> Controller (when first image texture is ready for display)
            self.ui_manager.first_image_ready.connect(self.on_first_image_ready)

            # View -> Controller -> Worker (to request image loading)
            if hasattr(self.main_window.view, 'request_load_image'):
                self.main_window.view.request_load_image.connect(self.on_request_load_image)

            # ThreadManager -> UIManager (when the first file from an archive is ready)
            self.thread_manager.first_file_extracted.connect(self.ui_manager.handle_first_file_extracted)
        
        if prefetcher:
            # AppState -> Prefetcher
            self.app_state.page_index_changed.connect(prefetcher.on_page_index_changed)

        # EventBus -> UIManager
        if self.ui_manager:
            self.event_bus.RESAMPLING_MODE_CHANGED.connect(self.ui_manager.update_view)

    def on_loader_ready(self):
        "Slot to handle the ready signal from ImageLoaderWorker."
        self._is_loading = False
        logging.info("ImageLoaderWorker is ready.")
        # 最初のページのロードはload_pathのfinallyブロックにあるupdate_viewに任せる
        # ここで個別に行うとUI更新のタイミングが複雑になるため

    @Slot()
    def on_first_image_ready(self):
        "最初の画像のテクスチャが準備できたときに呼び出されるスロット。"
        if self._is_first_image_ready:
            return
        self._is_first_image_ready = True
        
        logging.info("First image texture is ready. Starting prefetch.")
        # UIの更新はload_pathのfinallyブロックに任せるため、ここでは行わない
        # ここではプリフェッチの開始のみを担当する
        prefetcher = self.thread_manager.get_prefetcher()
        if prefetcher:
            prefetcher.on_page_index_changed(self.app_state.current_page_index, self.app_state.is_spread_view)

    @Slot(int, int)
    def on_request_load_image(self, page_index: int, priority: int):
        "Slot to handle image load requests from the view."
        image_loader = self.thread_manager.get_image_loader()
        if image_loader:
            image_loader.add_task(page_index, priority)

    def navigate_pages_by_wheel(self, delta: int):
        "マウスホイールの回転に応じてページを移動する。"
        if self.event_handler:
            direction = -1 if delta > 0 else 1
            self.event_handler.navigate_pages_by_wheel(direction)

    def toggle_view_mode(self):
        "Cycles through viewing modes: single, spread (right-to-left), spread (left-to-right)."
        is_spread = self.app_state.is_spread_view
        binding = self.app_state.binding_direction

        if not is_spread:
            # --- From single to spread (RTL) ---
            self.app_state.is_spread_view = True
            self.app_state.binding_direction = 'right'
        elif is_spread and binding == 'right':
            # --- From spread (RTL) to spread (LTR) ---
            self.app_state.binding_direction = 'left'
        elif is_spread and binding == 'left':
            # --- From spread (LTR) to single ---
            self.app_state.is_spread_view = False
        
        if self.ui_manager:
            self.ui_manager.update_view()


    def jump_to_page(self, page_index: int):
        """
        Jump to the specified page index.

        Args:
            page_index: The index of the page to jump to.
        """
        if not (0 <= page_index < len(self.app_state.image_files)):
            logging.warning(f"Invalid page index: {page_index}. Must be between 0 and {len(self.app_state.image_files) - 1}")
            return

        if self.app_state.current_page_index != page_index:
            self.app_state.current_page_index = page_index
