from __future__ import annotations
import logging
import os
from typing import TYPE_CHECKING
from PySide6.QtCore import QObject

from app.io.loader import FileLoader

if TYPE_CHECKING:
    from app.core.app_controller import ApplicationController

class LoaderService(QObject):
    """
    ファイルの読み込みプロセスを管理するサービス。
    ApplicationControllerから分離された読み込みロジックを担当する。
    """

    def __init__(self, controller: ApplicationController):
        super().__init__()
        self.controller = controller

    def load_path(self, path: str, page: int = 0) -> None:
        """
        新しいファイルまたはディレクトリパスを読み込む。
        """
        logging.info(f"--- STARTING NEW LOAD OPERATION FOR: {path} ---")
        if self.controller._is_loading:
            logging.warning(f"Load operation already in progress. Ignoring request for: {path}")
            return

        self.controller._is_loading = True
        self.controller._is_first_image_ready = False # 新しい読み込みが開始されたらフラグをリセット

        # 1. 既存のFileLoaderがあれば、安全な削除をスケジュールし、参照をクリア
        if self.controller.file_loader:
            logging.info(f"Scheduling previous FileLoader for deletion (id: {id(self.controller.file_loader)})")
            self.controller.file_loader.deleteLater() # Qtのオブジェクト削除のベストプラクティスに従う
            self.controller.file_loader = None

        # 2. AppStateの各プロパティをリセット
        self.controller.app_state.image_files = []
        self.controller.app_state.total_pages = 0
        self.controller.app_state.is_content_loaded = False
        self.controller.app_state.current_page_index = -1 # 無効なインデックスに設定
        self.controller.app_state.file_loader = None
        
        # 3. UIとキャッシュをクリアする
        logging.debug("[DEBUG_HAYATE] load_path called. Clearing UI, existing image and texture caches.")
        if self.controller.ui_manager:
            self.controller.ui_manager.reset_view()
        self.controller.image_cache.clear()
        
        # OpenGLViewのテクスチャキャッシュクリアはUIManager経由か、View直接か
        # UIManager.reset_view() で clear_view() が呼ばれるが、TextureCacheのクリアは？
        # OpenGLView.clear_view() は display_keys = [] にするだけ。
        # TextureCache.clear() も呼ぶべき。
        if self.controller.main_window and self.controller.main_window.view.objectName() == 'OpenGL':
            # OpenGLViewのリファクタリングにより、texture_cacheプロパティ経由でアクセス可能
            if hasattr(self.controller.main_window.view, 'texture_cache'):
                self.controller.main_window.view.texture_cache.clear()
        
        logging.info(f"Attempting to load path: {path}")
        self.controller.app_state.current_file_path = path

        try:
            # 4. 新しいFileLoaderをインスタンス化
            logging.info(f"Creating new FileLoader for path: {path}")
            loader = FileLoader(path=path, parent=self.controller)
            image_files = loader.get_image_list()

            if not image_files:
                logging.warning(f"No images found in: {path}")
                loader.deleteLater() # ここでも不要になったloaderは削除
                return

            # 5. AppStateとControllerのfile_loaderを更新
            self.controller.file_loader = loader
            self.controller.app_state.file_loader = loader
            folder_indices = self._get_folder_start_indices(image_files)
            self.controller.app_state.set_file_list(image_files, folder_indices)
            self.controller.app_state.current_page_index = page

            # 6. ThreadManagerに新しいFileLoaderを通知
            self.controller.thread_manager.file_loader_updated.emit(loader)

            logging.info(f"Successfully loaded {len(image_files)} images from {path}")
            logging.info(f"--- FINISHED LOAD OPERATION SETUP FOR: {path} ---")

        except FileNotFoundError:
            logging.error(f"Error: File or directory not found at '{path}'")
            if self.controller.ui_manager:
                self.controller.ui_manager.show_error_dialog(f"ファイルまたはディレクトリが見つかりません。\nパス: {path}")
        except Exception as e:
            logging.error(f"An unexpected error occurred while loading '{path}': {e}", exc_info=True)
            if self.controller.ui_manager:
                self.controller.ui_manager.show_error_dialog(f"ファイルの読み込み中に予期せぬエラーが発生しました。\n詳細: {e}")
        finally:
            self.controller._is_loading = False
            if self.controller.ui_manager:
                self.controller.ui_manager.update_view()

    def _get_folder_start_indices(self, file_list: list[str]) -> list[int]:
        """
        ファイルリストから各フォルダの最初のファイルのインデックスを抽出する。
        """
        folder_indices = []
        last_dir = None
        for i, file_path in enumerate(file_list):
            current_dir = os.path.dirname(file_path)
            if current_dir != last_dir:
                folder_indices.append(i)
                last_dir = current_dir
        return folder_indices
