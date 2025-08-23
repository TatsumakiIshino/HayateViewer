import logging
from PySide6.QtCore import Signal, Qt
from PySide6.QtGui import QIntValidator
from PySide6.QtWidgets import (QDialog, QCheckBox, QPushButton, QComboBox, QVBoxLayout, QHBoxLayout, QLabel, QMessageBox, QWidget, QSlider, QGroupBox, QSpinBox, QLineEdit)
from app.config.settings import Settings
from app.constants import RESAMPLING_MODES_CPU, RESAMPLING_MODES_GL

class SettingsDialog(QDialog):
    settings_changed = Signal(dict)
    clear_cache_requested = Signal()
    cache_settings_changed = Signal(dict)

    def __init__(self, settings_manager, event_bus, parent=None):
        super().__init__(parent)
        self.settings_manager = settings_manager
        self.event_bus = event_bus
        self.setWindowTitle("設定")
        self.setModal(True)

        # 初期設定値を保存
        self._save_initial_settings()
        
        self.layout = QVBoxLayout(self)

        # --- 一般設定 ---
        general_group = QGroupBox("一般設定")
        general_layout = QVBoxLayout()

        # レンダリングバックエンド設定
        backend_layout = QHBoxLayout()
        backend_label = QLabel("レンダリングバックエンド:")
        self.backend_combo = QComboBox()
        self.backend_combo.addItem("PySide6", "pyside6")
        self.backend_combo.addItem("PySide6 (MT)", "pyside6_mt")
        self.backend_combo.addItem("OpenGL", "opengl")
        current_backend = self.settings_manager.get('rendering_backend', 'pyside6')
        index = self.backend_combo.findData(current_backend)
        if index != -1:
            self.backend_combo.setCurrentIndex(index)
        backend_layout.addWidget(backend_label)
        backend_layout.addWidget(self.backend_combo)
        general_layout.addLayout(backend_layout)

        # リサンプリング方式設定
        self.resampling_cpu_widget = QWidget()
        resampling_cpu_layout = QHBoxLayout(self.resampling_cpu_widget)
        resampling_cpu_layout.setContentsMargins(0, 0, 0, 0)
        self.resampling_cpu_label = QLabel("リサンプリング方式 (PySide6):")
        self.resampling_cpu_combo = QComboBox()
        for mode_key, mode_name in RESAMPLING_MODES_CPU.items():
            self.resampling_cpu_combo.addItem(mode_name, mode_key)
        current_cpu_mode_key = self.settings_manager.get('resampling_mode_cpu', 'PIL_LANCZOS')
        self.resampling_cpu_combo.setCurrentText(RESAMPLING_MODES_CPU.get(current_cpu_mode_key, "Pillow: Lanczos"))
        resampling_cpu_layout.addWidget(self.resampling_cpu_label)
        resampling_cpu_layout.addWidget(self.resampling_cpu_combo)
        general_layout.addWidget(self.resampling_cpu_widget)

        self.resampling_gl_widget = QWidget()
        resampling_gl_layout = QHBoxLayout(self.resampling_gl_widget)
        resampling_gl_layout.setContentsMargins(0, 0, 0, 0)
        self.resampling_gl_label = QLabel("リサンプリング方式 (OpenGL):")
        self.resampling_gl_combo = QComboBox()
        for mode_key, mode_name in RESAMPLING_MODES_GL.items():
            self.resampling_gl_combo.addItem(mode_name, mode_key)
        current_gl_mode_key = self.settings_manager.get('resampling_mode_gl', 'GL_LANCZOS3')
        self.resampling_gl_combo.setCurrentText(RESAMPLING_MODES_GL.get(current_gl_mode_key, "Lanczos3 (Shader)"))
        resampling_gl_layout.addWidget(self.resampling_gl_label)
        resampling_gl_layout.addWidget(self.resampling_gl_combo)
        general_layout.addWidget(self.resampling_gl_widget)

        self.backend_combo.currentIndexChanged.connect(lambda: self.update_resampling_options_visibility(self.backend_combo.currentData()))
        self.update_resampling_options_visibility(self.backend_combo.currentData())

        # 見開き表示設定
        self.checkbox_spread_view = QCheckBox("見開き表示を有効にする")
        self.checkbox_spread_view.setChecked(self.settings_manager.get('is_spread_view', False))
        general_layout.addWidget(self.checkbox_spread_view)

        self.checkbox_single_page = QCheckBox("見開き表示時、最初の1ページ目を単独で表示する")
        self.checkbox_single_page.setChecked(self.settings_manager.get('spread_view_first_page_single', False))
        general_layout.addWidget(self.checkbox_single_page)

        self.checkbox_spread_view.toggled.connect(self.checkbox_single_page.setEnabled)
        self.checkbox_single_page.setEnabled(self.checkbox_spread_view.isChecked())
        
        self.checkbox_show_status_bar_info = QCheckBox("ステータスバーにキャッシュ情報を表示する")
        self.checkbox_show_status_bar_info.setChecked(self.settings_manager.get('show_status_bar_info', True))
        general_layout.addWidget(self.checkbox_show_status_bar_info)
        
        general_group.setLayout(general_layout)
        self.layout.addWidget(general_group)

        # --- キャッシュ設定 ---
        cache_group = QGroupBox("キャッシュ設定")
        cache_layout = QVBoxLayout()

        self.show_advanced_checkbox = QCheckBox("詳細オプションを表示")
        self.show_advanced_checkbox.setChecked(self.settings_manager.get('show_advanced_cache_options', False))
        cache_layout.addWidget(self.show_advanced_checkbox)

        self.advanced_options_widget = QWidget()
        advanced_layout = QVBoxLayout(self.advanced_options_widget)
        advanced_layout.setContentsMargins(10, 10, 10, 10)

        # キャッシュ最大サイズ
        cache_size_layout = QHBoxLayout()
        self.cache_size_label = QLabel(f"キャッシュ最大サイズ (MB):")
        self.cache_size_slider = QSlider(Qt.Orientation.Horizontal)
        self.cache_size_slider.setRange(64, 4096)
        self.cache_size_slider.setSingleStep(64)
        self.cache_size_slider.setValue(self.settings_manager.get('max_cache_size_mb', 1024))
        self.cache_size_value_label = QLabel(f"{self.cache_size_slider.value()} MB")
        cache_size_layout.addWidget(self.cache_size_label)
        cache_size_layout.addWidget(self.cache_size_slider)
        cache_size_layout.addWidget(self.cache_size_value_label)
        advanced_layout.addLayout(cache_size_layout)

        # 先読みページ数
        prefetch_layout = QHBoxLayout()
        self.prefetch_label = QLabel("CPUの前後の最大先読みページ数:")
        self.prefetch_spinbox = QSpinBox()
        self.prefetch_spinbox.setKeyboardTracking(False)
        self.prefetch_spinbox.setRange(1, 50)
        self.prefetch_spinbox.setValue(self.settings_manager.get('cpu_max_prefetch_pages', 10))
        prefetch_layout.addWidget(self.prefetch_label)
        prefetch_layout.addStretch(1)
        prefetch_layout.addWidget(self.prefetch_spinbox)
        advanced_layout.addLayout(prefetch_layout)

        # GPU先読みページ数
        gpu_prefetch_layout = QHBoxLayout()
        self.gpu_prefetch_label = QLabel("GPUの前後の最大先読みページ数:")
        self.gpu_prefetch_spinbox = QSpinBox()
        self.gpu_prefetch_spinbox.setKeyboardTracking(False)
        self.gpu_prefetch_spinbox.setRange(1, 50)
        self.gpu_prefetch_spinbox.setValue(self.settings_manager.get('gpu_max_prefetch_pages', 9))
        gpu_prefetch_layout.addWidget(self.gpu_prefetch_label)
        gpu_prefetch_layout.addStretch(1)
        gpu_prefetch_layout.addWidget(self.gpu_prefetch_spinbox)
        advanced_layout.addLayout(gpu_prefetch_layout)

        # ボタン
        cache_buttons_layout = QHBoxLayout()
        self.clear_cache_button = QPushButton("キャッシュをクリア")
        self.reset_cache_button = QPushButton("設定をデフォルトに戻す")
        cache_buttons_layout.addStretch(1)
        cache_buttons_layout.addWidget(self.clear_cache_button)
        cache_buttons_layout.addWidget(self.reset_cache_button)
        advanced_layout.addLayout(cache_buttons_layout)

        cache_layout.addWidget(self.advanced_options_widget)
        cache_group.setLayout(cache_layout)
        self.layout.addWidget(cache_group)

        self.layout.addStretch(1)

        # OK/キャンセルボタン
        self.button_box = QHBoxLayout()
        self.ok_button = QPushButton("OK")
        self.cancel_button = QPushButton("キャンセル")
        self.button_box.addStretch(1)
        self.button_box.addWidget(self.ok_button)
        self.button_box.addWidget(self.cancel_button)
        self.layout.addLayout(self.button_box)

        # --- シグナル接続 ---
        self.ok_button.clicked.connect(self.accept_settings)
        self.cancel_button.clicked.connect(self.reject)
        self.show_advanced_checkbox.toggled.connect(self.toggle_advanced_options)
        self.cache_size_slider.valueChanged.connect(self.update_cache_size_label)
        self.clear_cache_button.clicked.connect(self.on_clear_cache_clicked)
        self.reset_cache_button.clicked.connect(self.on_reset_cache_settings)
        self.prefetch_spinbox.valueChanged.connect(self._validate_prefetch_settings)
        self.gpu_prefetch_spinbox.valueChanged.connect(self._validate_prefetch_settings)

        self.toggle_advanced_options(self.show_advanced_checkbox.isChecked())
        self._validate_prefetch_settings() # 初期状態のバリデーション
        self.adjustSize() # ウィンドウサイズを内容に合わせる

    def _validate_prefetch_settings(self):
        """GPUの先読みページ数がCPUのそれを超えないようにバリデーションする。"""
        cpu_pages = self.prefetch_spinbox.value()
        gpu_pages = self.gpu_prefetch_spinbox.value()
        
        # GPUスピンボックスの最大値をCPUの値に設定
        self.gpu_prefetch_spinbox.setMaximum(cpu_pages)
        
        # 現在のGPUの値が新しい最大値を超えている場合は、最大値に調整
        if gpu_pages > cpu_pages:
            self.gpu_prefetch_spinbox.setValue(cpu_pages)

    def _save_initial_settings(self):
        """ダイアログ表示時の設定値をインスタンス変数に保存する。"""
        self.initial_settings = {
            'rendering_backend': self.settings_manager.get('rendering_backend'),
            'resampling_mode_cpu': self.settings_manager.get('resampling_mode_cpu'),
            'resampling_mode_gl': self.settings_manager.get('resampling_mode_gl'),
            'is_spread_view': self.settings_manager.get('is_spread_view'),
            'spread_view_first_page_single': self.settings_manager.get('spread_view_first_page_single'),
            'show_advanced_cache_options': self.settings_manager.get('show_advanced_cache_options'),
            'max_cache_size_mb': self.settings_manager.get('max_cache_size_mb'),
            'cpu_max_prefetch_pages': self.settings_manager.get('cpu_max_prefetch_pages'),
            'gpu_max_prefetch_pages': self.settings_manager.get('gpu_max_prefetch_pages'),
            'show_status_bar_info': self.settings_manager.get('show_status_bar_info', True),
        }

    def toggle_advanced_options(self, checked):
        self.advanced_options_widget.setVisible(checked)
        self.adjustSize()

    def update_cache_size_label(self, value):
        self.cache_size_value_label.setText(f"{value} MB")

    def on_clear_cache_clicked(self):
        reply = QMessageBox.question(self, "確認", "すべてのキャッシュをクリアしますか？",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            self.clear_cache_requested.emit()
            QMessageBox.information(self, "完了", "キャッシュをクリアしました。")

    def on_reset_cache_settings(self):
        default_settings = self.settings_manager._get_default_settings()
        self.cache_size_slider.setValue(default_settings.get('max_cache_size_mb', 1024))
        self.prefetch_spinbox.setValue(default_settings.get('cpu_max_prefetch_pages', 10))
        self.gpu_prefetch_spinbox.setValue(default_settings.get('gpu_max_prefetch_pages', 9))

    def update_resampling_options_visibility(self, backend):
        is_pyside = backend in ['pyside6', 'pyside6_mt']
        self.resampling_cpu_widget.setVisible(is_pyside)
        self.resampling_gl_widget.setVisible(backend == 'opengl')

    def accept_settings(self):
        # 現在のUIから設定値を取得
        current_settings = {
            'rendering_backend': self.backend_combo.currentData(),
            'resampling_mode_cpu': self.resampling_cpu_combo.currentData(),
            'resampling_mode_gl': self.resampling_gl_combo.currentData(),
            'is_spread_view': self.checkbox_spread_view.isChecked(),
            'spread_view_first_page_single': self.checkbox_single_page.isChecked(),
            'show_advanced_cache_options': self.show_advanced_checkbox.isChecked(),
            'max_cache_size_mb': self.cache_size_slider.value(),
            'cpu_max_prefetch_pages': self.prefetch_spinbox.value(),
            'gpu_max_prefetch_pages': self.gpu_prefetch_spinbox.value(),
            'show_status_bar_info': self.checkbox_show_status_bar_info.isChecked(),
        }

        # 変更があった設定項目を特定
        changed_settings = {}
        for key, new_value in current_settings.items():
            if self.initial_settings.get(key) != new_value:
                changed_settings[key] = new_value

        # 変更がなければ何もせずにダイアログを閉じる
        if not changed_settings:
            self.accept()
            return

        # 変更された設定のみをマネージャーに保存
        for key, value in changed_settings.items():
            self.settings_manager.set(key, value)
        self.settings_manager.save()

        # 変更内容を分類して通知
        general_changes = {
            k: v for k, v in changed_settings.items()
            if k in ['rendering_backend', 'resampling_mode_cpu', 'resampling_mode_gl', 'is_spread_view', 'spread_view_first_page_single', 'show_status_bar_info']
        }
        cache_changes = {
            k: v for k, v in changed_settings.items()
            if k in ['max_cache_size_mb', 'cpu_max_prefetch_pages', 'gpu_max_prefetch_pages']
        }

        if general_changes:
            self.settings_changed.emit(general_changes)
        
        if cache_changes:
            self.cache_settings_changed.emit(cache_changes)

        self.accept()


class JumpToPageDialog(QDialog):
    """ページ番号を入力してジャンプするためのシンプルなダイアログ。"""
    def __init__(self, max_page: int, current_page: int, parent=None):
        super().__init__(parent)
        self.setWindowTitle("ページへ移動")
        self.setModal(True)

        layout = QVBoxLayout(self)

        info_label = QLabel(f"ページ番号 (1 - {max_page}) を入力してください:")
        layout.addWidget(info_label)

        self.page_input = QLineEdit()
        self.page_input.setValidator(QIntValidator(1, max_page, self))
        self.page_input.setText(str(current_page))
        layout.addWidget(self.page_input)

        button_layout = QHBoxLayout()
        self.ok_button = QPushButton("OK")
        self.cancel_button = QPushButton("キャンセル")
        button_layout.addStretch()
        button_layout.addWidget(self.ok_button)
        button_layout.addWidget(self.cancel_button)
        layout.addLayout(button_layout)

        self.ok_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        self.page_input.returnPressed.connect(self.accept)

    def get_page_index(self) -> int | None:
        """入力されたページ番号を0ベースのインデックスとして返す。"""
        if self.exec() == QDialog.DialogCode.Accepted:
            try:
                page_number = int(self.page_input.text())
                return page_number - 1
            except (ValueError, TypeError):
                return None
        return None