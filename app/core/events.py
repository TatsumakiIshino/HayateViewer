import logging
import bisect
from PySide6.QtCore import Qt, QObject, Signal
from PySide6.QtWidgets import QFileDialog
from app.ui.dialogs import JumpToPageDialog

class EventBus(QObject):
    RENDERING_MODE_CHANGED = Signal()
    RESAMPLING_MODE_CHANGED = Signal()
    gpu_prefetch_request = Signal(int) # index

class EventHandler:
    def __init__(self, main_window, app_state, settings_manager, event_bus: EventBus):
        self.main_window = main_window
        self.app_state = app_state
        self.settings_manager = settings_manager
        self.event_bus = event_bus

    def handle_key_press(self, event):
        key = event.key()
        modifiers = event.modifiers()
        logging.debug(f"Key pressed: {key} ({Qt.Key(key).name}), Modifiers: {modifiers}")

        # --- アプリケーション操作 ---
        if key == Qt.Key.Key_O:
            self.open_options_dialog()
            return
        if key == Qt.Key.Key_F1:
            self.main_window.ui_manager.show_about_dialog()
            return
        if key == Qt.Key.Key_Q:
            self.main_window.close()
            return

        # --- ファイル操作 ---
        if key == Qt.Key.Key_F:
            if modifiers == Qt.KeyboardModifier.ShiftModifier:
                self.open_file_dialog()
            else:
                self.open_folder_dialog()
            return

        # --- 表示切替 ---
        if modifiers == Qt.KeyboardModifier.AltModifier and key == Qt.Key.Key_Return:
            self.main_window.toggle_fullscreen()
            return
        if key == Qt.Key.Key_Escape:
            if self.main_window.isFullScreen():
                self.main_window.showNormal()
                return
        
        # 以下、画像読み込み後のみ有効なショートカット
        if not self.app_state.is_content_loaded:
            return

        # --- ページ移動 ---
        if key in [Qt.Key.Key_Left, Qt.Key.Key_Right]:
            direction = 1 if key == Qt.Key.Key_Right else -1
            if modifiers == Qt.KeyboardModifier.ShiftModifier:
                self._navigate_folder(direction)
            elif modifiers & Qt.KeyboardModifier.ControlModifier or modifiers & Qt.KeyboardModifier.MetaModifier:
                self.navigate_single_page(direction)
            else:
                self.navigate_pages(key)
            return
        if key == Qt.Key.Key_Home:
            self.main_window.controller.jump_to_page(0)
            return
        if key == Qt.Key.Key_End:
            last_page_index = len(self.app_state.image_files) - 1
            self.main_window.controller.jump_to_page(last_page_index)
            return

        # --- 表示モード ---
        if key == Qt.Key.Key_B:
            self.main_window.controller.toggle_view_mode()
            return
        if key == Qt.Key.Key_S:
            if modifiers == Qt.KeyboardModifier.ShiftModifier:
                self.open_jump_to_page_dialog()
            else:
                self.main_window.toggle_seek_widget_visibility()
            return
        
        # --- ズーム操作 ---
        if key == Qt.Key.Key_Plus:
            self.main_window.zoom_in()
            return
        if key == Qt.Key.Key_Minus:
            self.main_window.zoom_out()
            return
        if key == Qt.Key.Key_Asterisk:
            self.main_window.zoom_reset()
            return

    def navigate_pages_by_wheel(self, direction):
        self._navigate(direction)

    def navigate_pages(self, key):
        if key == Qt.Key.Key_Right:
            direction = 1
        elif key == Qt.Key.Key_Left:
            direction = -1
        else:
            return

        self._navigate(direction)

    def _navigate(self, direction):
        page_step = 2 if self.app_state.is_spread_view else 1
        
        if self.app_state.is_spread_view and self.app_state.spread_view_first_page_single:
            single_page_indices = set([0] + self.app_state.folder_start_indices)
            current_index = self.app_state.current_page_index
            
            # フォルダの最終ページかどうかを判定
            is_last_page_of_folder = (current_index + 1) in single_page_indices

            if direction == 1:  # 進む場合
                # 現在地が単独表示ページか、フォルダの最終ページならステップは1
                if current_index in single_page_indices or is_last_page_of_folder:
                    page_step = 1
            elif direction == -1:  # 戻る場合
                # 移動先が単独表示ページならステップは1
                if (current_index - 1) in single_page_indices:
                    page_step = 1

        new_index = self.app_state.current_page_index + (direction * page_step)

        if new_index < 0:
            new_index = 0
        elif new_index >= len(self.app_state.image_files):
            last_page_index = len(self.app_state.image_files) - 1
            if self.app_state.current_page_index == last_page_index:
                return
            new_index = last_page_index
        
        if self.app_state.current_page_index != new_index:
            self.app_state.current_page_index = new_index

    def navigate_single_page(self, direction):
        """単一ページの移動を処理する。
        
        Args:
            direction (int): 移動方向。1で進む、-1で戻る。
        """
        new_index = self.app_state.current_page_index + direction

        # ページ範囲の境界チェック
        if new_index < 0:
            new_index = 0
        elif new_index >= len(self.app_state.image_files):
            # 最終ページ以降には進めないため、処理を中断
            return

        if self.app_state.current_page_index != new_index:
            self.main_window.controller.jump_to_page(new_index)

    def _navigate_folder(self, direction: int):
        """フォルダ単位で移動する。
        
        Args:
            direction (int): 移動方向。1で次、-1で前。
        """
        if not self.app_state.folder_start_indices:
            return

        current_index = self.app_state.current_page_index
        folder_indices = self.app_state.folder_start_indices
        
        # 現在のページがどのフォルダに属しているかを探す
        # bisect_rightは、current_indexがフォルダの先頭だった場合に次のフォルダのインデックスを返す
        current_folder_pos = bisect.bisect_right(folder_indices, current_index) - 1

        new_folder_pos = current_folder_pos + direction

        # 境界チェック
        if new_folder_pos < 0:
            # 最初のフォルダより前には行かない
            return
        if new_folder_pos >= len(folder_indices):
            # 最後のフォルダより後ろには行かない
            return
            
        new_page_index = folder_indices[new_folder_pos]
        
        if self.app_state.current_page_index != new_page_index:
            self.main_window.controller.jump_to_page(new_page_index)

    def open_jump_to_page_dialog(self):
        """ページジャンプダイアログを開き、指定されたページに移動する。"""
        if not self.app_state.is_content_loaded:
            return
        
        total_pages = self.app_state.total_pages
        current_page = self.app_state.current_page_index + 1
        
        dialog = JumpToPageDialog(total_pages, current_page, self.main_window)
        page_index = dialog.get_page_index()
        
        if page_index is not None:
            self.main_window.controller.jump_to_page(page_index)

    def open_options_dialog(self):
        from app.ui.dialogs import SettingsDialog
        dialog = SettingsDialog(self.settings_manager, self.main_window)
        # 設定変更シグナルをコントローラーのハンドラに接続
        dialog.settings_changed.connect(self.main_window.controller.handle_settings_change)
        dialog.cache_settings_changed.connect(self.main_window.controller.handle_cache_settings_change)
        dialog.clear_cache_requested.connect(self.main_window.controller.handle_clear_cache)
        dialog.exec()
        # 切断はダイアログが破棄される際に自動的に行われる

    def open_folder_dialog(self):
        dir_path = QFileDialog.getExistingDirectory(self.main_window, "フォルダを開く")
        if dir_path:
            self.main_window.controller.load_path(dir_path)

    def open_file_dialog(self):
        supported_formats = set(self.settings_manager.get('supported_formats', []))
        supported_archive_formats = set(self.settings_manager.get('supported_archive_formats', []))
        all_supported_formats = supported_formats.union(supported_archive_formats)
        filter_str = f"対応ファイル ({' '.join(['*' + f for f in sorted(list(all_supported_formats))])});;すべてのファイル (*)"
        file_path, _ = QFileDialog.getOpenFileName(self.main_window, "ファイルを開く", filter=filter_str)
        if file_path:
            self.main_window.controller.load_path(file_path)