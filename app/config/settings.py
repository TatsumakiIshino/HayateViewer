import json
import os
from PySide6.QtCore import QRect, QPoint, QObject, Signal
from PySide6.QtWidgets import QApplication

from app.constants import RESAMPLING_MODES_CPU, RESAMPLING_MODES_GL

class Settings(QObject):
    setting_changed = Signal(str, object)

    def __init__(self, config_file='config.json'):
        super().__init__()
        self.config_file = config_file
        self.settings = self.load_settings()

    def get(self, key, default=None):
        return self.settings.get(key, default)

    def set(self, key, value):
        if self.settings.get(key) != value:
            self.settings[key] = value
            self.save()

    def save(self):
        try:
            with open(self.config_file, 'w', encoding='utf-8') as f:
                json.dump(self.settings, f, indent=4, ensure_ascii=False)
        except IOError:
            pass

    def load_settings(self):
        try:
            with open(self.config_file, 'r', encoding='utf-8') as f:
                settings = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            settings = self._get_default_settings()
            self.settings = settings
            self.save()
        
        # ウィンドウ位置を画面内に調整
        x, y, width, height = self._get_window_geometry(settings)
        adjusted_rect = self._adjust_window_to_screen(x, y, width, height)
        settings['window_geometry'] = (adjusted_rect.x(), adjusted_rect.y(), adjusted_rect.width(), adjusted_rect.height())

        # レンダリングバックエンドの検証
        valid_backends = ['pyside6', 'pyside6_mt', 'opengl']
        if settings.get('rendering_backend') not in valid_backends:
            settings['rendering_backend'] = 'pyside6'

        # 古い設定キーからの移行
        if 'resampling_mode' in settings:
            old_mode = settings.pop('resampling_mode')
            if old_mode.startswith('GL_'):
                settings.setdefault('resampling_mode_gl', old_mode)
            else:
                # PIL, CV2, SKIMAGEなど
                settings.setdefault('resampling_mode_cpu', old_mode)

        # CPUリサンプリングモードの検証
        saved_cpu_mode = settings.get('resampling_mode_cpu', 'PIL_LANCZOS')
        if saved_cpu_mode not in RESAMPLING_MODES_CPU:
            settings['resampling_mode_cpu'] = 'PIL_LANCZOS'

        # GLリサンプリングモードの検証
        saved_gl_mode = settings.get('resampling_mode_gl', 'GL_LANCZOS3')
        if saved_gl_mode not in RESAMPLING_MODES_GL:
            settings['resampling_mode_gl'] = 'GL_LANCZOS3'
        
        # 並列デコードワーカ数の検証
        if not isinstance(settings.get('parallel_decoding_workers'), int) or settings.get('parallel_decoding_workers') < 0:
            workers = max(1, os.cpu_count() // 2) if os.cpu_count() else 1
            settings['parallel_decoding_workers'] = workers

        # --- 新しい設定への移行と検証 ---
        # max_prefetch_pages -> cpu_max_prefetch_pages
        if 'max_prefetch_pages' in settings:
            settings['cpu_max_prefetch_pages'] = settings.pop('max_prefetch_pages')
        if not isinstance(settings.get('cpu_max_prefetch_pages'), int) or settings.get('cpu_max_prefetch_pages') < 0:
            settings['cpu_max_prefetch_pages'] = 10

        # gpu_texture_cache_size -> gpu_max_prefetch_pages (移行)
        if 'gpu_texture_cache_size' in settings:
            settings['gpu_max_prefetch_pages'] = settings.pop('gpu_texture_cache_size')
        # gpu_cache_page_count -> gpu_max_prefetch_pages (移行)
        if 'gpu_cache_page_count' in settings:
            settings['gpu_max_prefetch_pages'] = settings.pop('gpu_cache_page_count')
        if not isinstance(settings.get('gpu_max_prefetch_pages'), int) or settings.get('gpu_max_prefetch_pages') < 0:
            settings['gpu_max_prefetch_pages'] = 9
            
        # 廃止された設定を削除
        settings.pop('gpu_prefetch_forward', None)
        settings.pop('gpu_prefetch_backward', None)
        settings.pop('resampling_multithreading', None)
        settings.pop('resampling_multithreading_workers', None)

        return settings

    def _get_default_settings(self):
        return {
            "rendering_backend": "pyside6_mt",
            "is_spread_view": True,
            "binding_direction": "left",
            "spread_view_first_page_single": True,
            "window_size": [
                1280,
                768
            ],
            "window_position": [
                417,
                349
            ],
            "window_geometry": [
                417,
                349,
                1280,
                768
            ],
            "parallel_decoding_workers": 8,
            "resampling_mode_cpu": "SKIMAGE_ORDER_5",
            "resampling_mode_gl": "GL_QUINTIC",
            "resampling_mode_dx": "DX_NEAREST",
            "show_advanced_cache_options": True,
            "max_cache_size_mb": 4096,
            "cpu_max_prefetch_pages": 10,
            "gpu_max_prefetch_pages": 9,
            "show_status_bar_info": True
        }

    def _get_window_geometry(self, settings):
        width, height = settings.get('window_size', (1200, 800))
        x, y = settings.get('window_position', (100, 100))
        return x, y, width, height

    def _adjust_window_to_screen(self, x, y, width, height):
        """ウィンドウが画面内に完全に収まるように位置を調整する"""
        screen = QApplication.screenAt(QPoint(x + width // 2, y + height // 2))
        if not screen:
            screen = QApplication.primaryScreen()
            if not screen:
                return QRect(100, 100, 1200, 800)

        screen_geo = screen.availableGeometry()

        if y < screen_geo.top():
            y = screen_geo.top()
        if x < screen_geo.left():
            x = screen_geo.left()
        if x + width > screen_geo.right():
            x = screen_geo.right() - width
        if y + height > screen_geo.bottom():
            y = screen_geo.bottom() - height
        if x < screen_geo.left():
            x = screen_geo.left()
        if y < screen_geo.top():
            y = screen_geo.top()

        return QRect(x, y, width, height)