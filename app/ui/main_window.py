import logging
from typing import TYPE_CHECKING
import os

from PySide6.QtWidgets import (QMainWindow, QLabel, QStatusBar, QMessageBox, QWidget, QHBoxLayout, QLineEdit, QSlider)
from PySide6.QtGui import QDropEvent, QDragEnterEvent, QCloseEvent, QResizeEvent, QIntValidator
from PySide6.QtCore import Qt, Signal, QTimer

from app.ui.views import ImageViewer
from app.ui.dialogs import SettingsDialog

if TYPE_CHECKING:
    from app.core.app_controller import ApplicationController
    from app.ui.views.opengl_view import OpenGLView
    from PySide6.QtGui import QImage


class MainWindow(QMainWindow):
    """
    アプリケーションのメインウィンドウ。
    UIイベントの受付と、各マネージャークラスへの処理の委譲に専念する。
    """
    # ワーカスレッドへの処理要求シグナル (ThreadManagerがリッスン)
    request_load_signal = Signal(int, int)  # page_index, priority
    # 設定変更通知シグナル (ThreadManager/Prefetcherがリッスン)
    update_prefetcher_settings = Signal()

    def __init__(self, controller: "ApplicationController"):
        super().__init__()
        self.controller = controller
        self.app_state = controller.app_state
        self.settings_manager = controller.settings_manager
        self.ui_manager = None  # UIManagerは後から設定される
        self.event_handler = None

        self.init_ui()
        self.setAcceptDrops(True)

        # シーク機能用タイマー
        self.seek_delay_timer = QTimer(self)
        self.seek_delay_timer.setSingleShot(True)
        self.seek_delay_timer.timeout.connect(self._on_seek_timer_timeout)

        self.create_seek_widget()

    def set_ui_manager(self, ui_manager: "UIManager"):
        """
        ApplicationControllerからUIManagerのインスタンスを受け取り、UIの最終設定を行う。
        """
        self.ui_manager = ui_manager
        # UIManagerに依存するUIの初期化処理を実行する
        self.ui_manager.update_status_bar()
        self.ui_manager.toggle_status_bar_info_visibility()

    def init_ui(self):
        """UIウィジェットの作成とレイアウトを行う。"""
        self.setWindowTitle("Project Hayate - 高速漫画ビューア")
        self.setGeometry(100, 100, 1280, 768)

        # ImageViewerのセットアップ
        self.recreate_view()


        # ステータスバーのセットアップ
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        
        self.page_info_label = QLabel("Page: - / -")
        self.view_mode_label = QLabel("View: -")
        self.rendering_backend_label = QLabel("Backend: -")
        self.resampling_label = QLabel()
        self.cpu_cache_label = QLabel()
        self.gpu_cache_label = QLabel()

        self.status_bar.addWidget(self.page_info_label)
        self.status_bar.addWidget(self.view_mode_label)
        self.status_bar.addPermanentWidget(self.cpu_cache_label)
        self.status_bar.addPermanentWidget(self.gpu_cache_label)
        self.status_bar.addPermanentWidget(self.rendering_backend_label)
        self.status_bar.addPermanentWidget(self.resampling_label)

    def set_event_handler(self, event_handler):
        """イベントハンドラを接続する。"""
        self.event_handler = event_handler
        # ApplicationControllerが接続の責務を持つ
        if self.image_viewer:
            self.image_viewer.keyPressed.connect(self.event_handler.handle_key_press)

    # --- イベントハンドラ ---

    def dropEvent(self, event: QDropEvent):
        """ファイル/フォルダがドロップされたときの処理。"""
        urls = event.mimeData().urls()
        if urls:
            path = urls[0].toLocalFile()
            if os.path.isdir(path) or os.path.isfile(path):
                self.controller.load_path(path)

    def dragEnterEvent(self, event: QDragEnterEvent):
        """ドラッグエンターイベント。"""
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def resizeEvent(self, event: QResizeEvent):
        """ウィンドウリサイズイベント。"""
        super().resizeEvent(event)
        if self.app_state.is_content_loaded and self.ui_manager:
            self.ui_manager.update_view()
        
        # シークウィジェットの位置を更新
        if hasattr(self, 'seek_widget'):
            self._update_seek_widget_position()

    def handle_wheel_scroll(self, delta: int):
        """マウスホイールイベント。"""
        if not self.app_state.is_content_loaded: return
        # Controllerにナビゲーションを委譲
        self.controller.navigate_pages_by_wheel(delta)

    def closeEvent(self, event: QCloseEvent):
        """ウィンドウクローズイベント。"""
        self.controller.cleanup()
        super().closeEvent(event)

    # --- シーク機能関連のメソッド ---

    def create_seek_widget(self):
        """シーク用のウィジェット（スライダーのみ）を作成し、ウィンドウ上に配置する。"""
        self.seek_widget = QWidget(self)
        self.seek_widget.setFixedWidth(self.width() // 2)
        seek_layout = QHBoxLayout(self.seek_widget)
        seek_layout.setContentsMargins(10, 5, 10, 5)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.slider.valueChanged.connect(self._on_slider_value_changed)

        seek_layout.addWidget(self.slider)
        
        self.seek_widget.setLayout(seek_layout)
        self.seek_widget.hide()

    def toggle_seek_widget_visibility(self):
        """シークウィジェットの表示/非表示を切り替える。"""
        if self.app_state.is_content_loaded:
            is_visible = not self.seek_widget.isVisible()
            self.seek_widget.setVisible(is_visible)
            if is_visible:
                self._update_seek_widget_position()
                self.seek_widget.raise_()

    def update_seek_widget_state(self):
        """コンテンツの読み込み状態に基づいてシークウィジェットの状態を更新する。"""
        if self.app_state.is_content_loaded and self.app_state.total_pages > 0:
            total_pages = self.app_state.total_pages
            current_index = self.app_state.current_page_index

            # シグナルをブロックして値の更新による再帰呼び出しを防ぐ
            self.slider.blockSignals(True)
            self.slider.setRange(0, total_pages - 1)
            self.slider.setValue(current_index)
            self.slider.blockSignals(False)
        else:
            self.seek_widget.hide()

    # --- シーク機能関連のスロット ---

    def _on_slider_value_changed(self, index: int):
        """スライダーの値が変更されたときに呼び出されるスロット。"""
        self.seek_delay_timer.start(100)  # 100msのデバウンス

    def _on_seek_timer_timeout(self):
        """デバウンスタイマーがタイムアウトしたときに呼び出されるスロット。"""
        index = self.slider.value()
        self.controller.jump_to_page(index)

    def _on_app_state_page_changed(self, index: int, is_spread: bool):
        """UI以外の要因でページが変更された場合にUIを更新するスロット。"""
        # is_spreadは現在の実装では未使用だが、将来のために引数は維持
        if self.seek_widget.isVisible() or self.app_state.is_content_loaded:
            self.slider.blockSignals(True)
            self.slider.setValue(index)
            self.slider.blockSignals(False)

    def _update_seek_widget_position(self):
        """シークウィジェットの位置をウィンドウの中央下部に更新する。"""
        # ウィジェットの幅をウィンドウサイズの半分に設定
        widget_width = self.width() // 2
        self.seek_widget.setFixedWidth(widget_width)
        
        # X座標: ウィンドウ中央
        x = (self.width() - self.seek_widget.width()) // 2
        # Y座標: ステータスバーの少し上
        y = self.height() - self.seek_widget.height() - self.status_bar.height() - 10
        self.seek_widget.move(x, y)

    # --- スロット (Worker/Managerからのコールバック) ---

    def on_texture_prepared(self, key: str):
        """テクスチャ準備完了時のスロット。"""
        if self.ui_manager:
            self.ui_manager.handle_texture_prepared(key)

    def on_loading_failed(self, path: str, message: str):
        """画像読み込み失敗時のスロット。"""
        error_message = f"読み込みエラー: {os.path.basename(path)}"
        logging.error(f"{error_message} - {message}")
        if self.ui_manager:
            self.ui_manager.show_status_message(error_message, 5000)

    # --- 公開メソッド (Controller/EventHandlerから呼び出される) ---

    def open_settings_dialog(self):
        """設定ダイアログを開く。"""
        dialog = SettingsDialog(self.settings_manager, self.controller.event_bus, self)
        # シグナルを直接コントローラーのメソッドに接続
        dialog.settings_changed.connect(self.controller.handle_settings_change)
        dialog.clear_cache_requested.connect(self.controller.handle_clear_cache)
        dialog.cache_settings_changed.connect(self.controller.handle_cache_settings_change)
        
        dialog.exec()

    def zoom_in(self):
        if self.ui_manager:
            self.ui_manager.zoom_in()

    def zoom_out(self):
        if self.ui_manager:
            self.ui_manager.zoom_out()

    def zoom_reset(self):
        if self.ui_manager:
            self.ui_manager.zoom_reset()

    def toggle_fullscreen(self):
        if self.isFullScreen():
            self.showNormal()
        else:
            self.showFullScreen()

    def show_restart_required_message(self):
        """再起動が必要なことをユーザーに通知するダイアログを表示する。"""
        QMessageBox.information(
            self,
            "再起動が必要です",
            "レンダリングバックエンドの変更を適用するには、アプリケーションを再起動してください。",
            QMessageBox.StandardButton.Ok
        )

    def recreate_view(self):
        """レンダリングバックエンドに基づいてビューを再生成する。"""
        # 現在の中央ウィジェット（ImageViewer）を安全に破棄
        current_widget = self.centralWidget()
        if current_widget:
            current_widget.deleteLater()

        # 新しいビューのインスタンス化
        backend = self.settings_manager.get('rendering_backend')
        if backend == 'opengl':
            from app.ui.views.opengl_view import OpenGLView
            view = OpenGLView(self.app_state, self.settings_manager, self.controller.image_cache, self.controller.event_bus, self)
            view.texture_prepared.connect(self.on_texture_prepared)
            view.request_load_image.connect(self.controller.on_request_load_image)
        else:
            from app.ui.views.default_view import DefaultGraphicsView
            view = DefaultGraphicsView(self.controller, self)

        # 新しいビューの準備完了シグナルをUI更新に接続
        if self.ui_manager:
            view.view_initialized.connect(self.ui_manager.update_view)

        self.view = view
        self.image_viewer = ImageViewer(view, self)
        self.setCentralWidget(self.image_viewer)

        # イベント接続の再設定
        self.image_viewer.wheelScrolled.connect(self.handle_wheel_scroll)
        if self.event_handler:
            self.image_viewer.keyPressed.connect(self.event_handler.handle_key_press)
