from __future__ import annotations
from typing import TYPE_CHECKING
from PySide6.QtCore import QObject
from PySide6.QtWidgets import QMessageBox

from app.ui.dialogs import SettingsDialog

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow
    from app.core.app_controller import ApplicationController

class DialogManager(QObject):
    """ダイアログの表示を担当するクラス。"""

    def __init__(self, main_window: MainWindow, controller: ApplicationController):
        super().__init__()
        self.main_window = main_window
        self.controller = controller

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

    def open_settings_dialog(self):
        """設定ダイアログを開く。"""
        dialog = SettingsDialog(self.main_window.settings_manager, self.controller.event_bus, self.main_window)
        # シグナルを直接コントローラーのメソッドに接続
        dialog.settings_changed.connect(self.controller.handle_settings_change)
        dialog.clear_cache_requested.connect(self.controller.handle_clear_cache)
        dialog.cache_settings_changed.connect(self.controller.handle_cache_settings_change)
        
        dialog.exec()

    def show_restart_required_message(self):
        """再起動が必要なことをユーザーに通知するダイアログを表示する。"""
        QMessageBox.information(
            self.main_window,
            "再起動が必要です",
            "レンダリングバックエンドの変更を適用するには、アプリケーションを再起動してください。",
            QMessageBox.StandardButton.Ok
        )
