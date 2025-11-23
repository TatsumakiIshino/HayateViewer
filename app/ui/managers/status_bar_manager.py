from __future__ import annotations
from typing import TYPE_CHECKING
import logging
from PySide6.QtCore import QObject

from app.constants import RESAMPLING_MODES_CPU, RESAMPLING_MODES_GL
from app.ui.views.opengl_view import OpenGLView

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow
    from app.core.state import AppState

class StatusBarManager(QObject):
    """ステータスバーの更新と管理を担当するクラス。"""

    def __init__(self, main_window: MainWindow, app_state: AppState):
        super().__init__()
        self.main_window = main_window
        self.app_state = app_state

    def update_status_bar(self, indices_to_display: list[int]) -> None:
        """静的なステータスバー情報（ページ、モードなど）を更新します。"""
        if not self.app_state.is_content_loaded:
            self.main_window.page_info_label.setText("ファイルを開くか、ウィンドウにドロップしてください")
            self.main_window.view_mode_label.setText("")
            self.main_window.rendering_backend_label.setText("Backend: -")
            self.main_window.resampling_label.setText("Resampling: -")
            return

        # --- ページ情報と表示モード ---
        total_pages = self.app_state.total_pages
        current_index = self.app_state.current_page_index
        current_page_num = current_index + 1
        folder_indices = self.app_state.folder_start_indices

        folder_info_str = ""
        if folder_indices and len(folder_indices) > 1:
            current_folder_idx = -1
            for i, start_idx in enumerate(folder_indices):
                if current_index >= start_idx:
                    current_folder_idx = i
                else:
                    break
            
            if current_folder_idx != -1:
                folder_start_page = folder_indices[current_folder_idx]
                folder_end_page = folder_indices[current_folder_idx + 1] if current_folder_idx + 1 < len(folder_indices) else total_pages
                pages_in_folder = folder_end_page - folder_start_page
                current_page_in_folder = current_index - folder_start_page + 1
                folder_info_str = f" (Folder {current_folder_idx + 1}: {current_page_in_folder}/{pages_in_folder})"

        if self.app_state.is_spread_view:
            page_str = "-".join(map(lambda x: str(x + 1), indices_to_display))
            self.main_window.page_info_label.setText(f"Page: {page_str} / {total_pages}{folder_info_str}")
            
            binding_str = "右綴じ" if self.app_state.binding_direction == 'right' else "左綴じ"
            self.main_window.view_mode_label.setText(f"View: 見開き ({binding_str})")
        else:
            self.main_window.page_info_label.setText(f"Page: {current_page_num} / {total_pages}{folder_info_str}")
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
        """動的なステータスバー情報（キャッシュ）を更新します。"""
        if not self.app_state.is_content_loaded:
            self.main_window.cpu_cache_label.setText("CPU: -")
            self.main_window.gpu_cache_label.setText("GPU: -")
            return

        # --- CPUキャッシュ情報 ---
        cpu_cache = self.main_window.controller.image_cache
        cpu_pages = cpu_cache.page_count
        cpu_keys = sorted(list(cpu_cache.cache.keys()))
        
        # ページリストが長い場合は省略表示（最初の3つと最後の1つのみ表示）
        if len(cpu_keys) > 5:
            cpu_keys_str = f"[{cpu_keys[0]}, {cpu_keys[1]}, {cpu_keys[2]}, ..., {cpu_keys[-1]}]"
        else:
            cpu_keys_str = str(cpu_keys)
        
        self.main_window.cpu_cache_label.setText(f"CPU: {cpu_pages}p {cpu_keys_str}")
        # ツールチップで完全なリストを表示
        self.main_window.cpu_cache_label.setToolTip(f"CPU Cache: {cpu_pages} pages\n{cpu_keys}")

        # --- GPUキャッシュ情報 ---
        if isinstance(self.main_window.view, OpenGLView):
            gpu_cache = self.main_window.view.texture_cache
            gpu_pages = gpu_cache.page_count
            
            gpu_keys = []
            if hasattr(gpu_cache, 'lock'):
                with gpu_cache.lock:
                    # キーをページインデックスに変換する
                    raw_keys = list(gpu_cache.cache.keys())
                    for key in raw_keys:
                        try:
                            # '::'で分割してページインデックスを取得
                            page_index_str = key.rsplit('::', 1)[1]
                            page_index = int(page_index_str)
                            gpu_keys.append(page_index)
                        except (ValueError, IndexError) as e:
                            logging.warning(f"[StatusBarManager] Could not parse GPU cache key: {key}. Error: {e}")
            
            gpu_keys.sort()
            
            # ページリストが長い場合は省略表示（最初の3つと最後の1つのみ表示）
            if len(gpu_keys) > 5:
                gpu_keys_str = f"[{gpu_keys[0]}, {gpu_keys[1]}, {gpu_keys[2]}, ..., {gpu_keys[-1]}]"
            else:
                gpu_keys_str = str(gpu_keys)
            
            self.main_window.gpu_cache_label.setText(f"GPU: {gpu_pages}p {gpu_keys_str}")
            # ツールチップで完全なリストを表示
            self.main_window.gpu_cache_label.setToolTip(f"GPU Cache: {gpu_pages} pages\n{gpu_keys}")
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
