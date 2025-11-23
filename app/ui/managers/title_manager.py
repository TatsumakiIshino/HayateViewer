from __future__ import annotations
from typing import TYPE_CHECKING
import os
from PySide6.QtCore import QObject

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow
    from app.core.state import AppState

class TitleManager(QObject):
    """ウィンドウタイトルの更新を担当するクラス。"""

    def __init__(self, main_window: MainWindow, app_state: AppState):
        super().__init__()
        self.main_window = main_window
        self.app_state = app_state

    def update_window_title(self, indices: list[int]) -> None:
        """ウィンドウのタイトルを更新します。"""
        if not self.app_state.is_content_loaded or not self.main_window.controller.file_loader:
            self.main_window.setWindowTitle("Project Hayate - 高速漫画ビューア")
            return

        # ファイル名（フォルダ名）の取得
        base_name = os.path.basename(self.app_state.current_file_path)
        
        # 表示中の画像ファイル名を取得
        names = []
        for idx in indices:
            if 0 <= idx < len(self.app_state.image_files):
                file_path = self.app_state.image_files[idx]
                names.append(os.path.basename(file_path))
        
        if not names:
            self.main_window.setWindowTitle(f"{base_name} - Project Hayate")
        else:
            names_str = " / ".join(names)
            self.main_window.setWindowTitle(f"{base_name} - {names_str}")
