# app/core/prefetcher.py

import logging
from PySide6.QtCore import QThread, Signal, QObject, Slot

from app.io.loader import ImageLoaderWorker
from app import constants

class PrefetcherWorker(QObject):
    """
    ワーカスレッドでプリフェッチ処理を実行するクラス。
    CPUキャッシュとGPUキャッシュの歯抜けを検知し、補充する統一的なコントローラーとして機能する。
    moveToThreadで使用されることを想定。
    """
    finished = Signal()
    # CPUキャッシュへのロード要求
    prefetch_request_generated = Signal(int, int) # page_index, priority
    # GPUテクスチャ準備要求
    texture_preparation_requested = Signal(int) # page_index

    def __init__(self, app_state, settings_manager, image_cache, texture_cache):
        super().__init__()
        self.app_state = app_state
        self.settings_manager = settings_manager
        self.image_cache = image_cache
        self.texture_cache = texture_cache
        
        self.cpu_max_prefetch_pages = self.settings_manager.get('cpu_max_prefetch_pages', 9)
        self.gpu_max_prefetch_pages = self.settings_manager.get('gpu_max_prefetch_pages', 4)
        self._is_running = True

    @Slot()
    def stop(self):
        """ワーカの停止をマークする。"""
        logging.info("Stopping PrefetcherWorker...")
        self._is_running = False
        self.finished.emit()

    @Slot(int, bool)
    def on_page_index_changed(self, new_index, is_spread_view):
        """ページインデックスの変更を処理するスロット。"""
        if not self._is_running:
            return
            
        logging.debug(f"[Prefetcher] on_page_index_changed received index: {new_index}, is_spread: {is_spread_view}. Thread: {QThread.currentThread()}")
        self._do_prefetch(new_index, is_spread_view)

    @Slot(dict)
    def update_prefetch_settings(self, settings: dict):
        """設定ダイアログからの変更を一括で適用する。"""
        logging.info(f"Updating prefetcher settings: {settings}")
        if 'cpu_max_prefetch_pages' in settings:
            self.cpu_max_prefetch_pages = settings['cpu_max_prefetch_pages']
        if 'gpu_max_prefetch_pages' in settings:
            self.gpu_max_prefetch_pages = settings['gpu_max_prefetch_pages']
        
        logging.info(f"New prefetcher settings: CPU={self.cpu_max_prefetch_pages}, GPU={self.gpu_max_prefetch_pages}")
        # 設定変更を即座に反映させるためにプリフェッチを再実行
        self.on_page_index_changed(self.app_state.current_page_index, self.app_state.is_spread_view)

    @Slot()
    def on_context_changed(self):
        """
        FileLoaderが変更されたときなど、読み込みコンテキストが変わったときに呼び出される。
        現在のページインデックスに基づいてプリフェッチを再実行する。
        """
        logging.info("[Prefetcher] Context changed. Rerunning prefetch logic.")
        self.on_page_index_changed(self.app_state.current_page_index, self.app_state.is_spread_view)

    def _do_prefetch(self, current_index, is_spread):
        """プリフェッチ処理を実行する。"""
        if not self._is_running or not self.app_state.is_content_loaded:
            return

        logging.debug(f"--- Running prefetch logic ---")
        logging.debug(f"Current page: {current_index}, is_spread: {is_spread}")

        max_index = len(self.app_state.image_files) - 1
        if max_index < 0:
            return

        # 1. CPUとGPUのプリフェッチ範囲をそれぞれ計算
        cpu_pages_to_prefetch = self._calculate_pages_for_prefetch(
            current_index, self.cpu_max_prefetch_pages, max_index, is_spread
        )
        gpu_pages_to_prefetch = self._calculate_pages_for_prefetch(
            current_index, self.gpu_max_prefetch_pages, max_index, is_spread
        )
        self.app_state.gpu_prefetch_range = set(gpu_pages_to_prefetch)
        logging.info(f"CPU prefetch pages: {cpu_pages_to_prefetch}")
        logging.info(f"GPU prefetch pages: {gpu_pages_to_prefetch} (updated in app_state)")

        # 2. CPUキャッシュの歯抜けを埋める
        self._fill_cpu_cache_gaps(cpu_pages_to_prefetch)

        # 3. GPUキャッシュの処理
        if self.texture_cache:
            # 3a. GPUプリフェッチ範囲外のテクスチャをクリーンアップ
            if gpu_pages_to_prefetch:
                start_page = min(gpu_pages_to_prefetch)
                end_page = max(gpu_pages_to_prefetch) + 1
                self.texture_cache.evict_outside_range(start_page, end_page)

            # 3b. GPUキャッシュの歯抜けを埋める
            self._fill_gpu_cache_gaps(gpu_pages_to_prefetch)
            
            gpu_cached_keys = list(self.texture_cache.cache.keys())
            logging.debug(f"[Prefetcher] GPU cache keys after prefetch: {gpu_cached_keys}")
        
        logging.info("[Prefetcher] _do_prefetch finished.")

    @Slot()
    def update_settings(self):
        """設定マネージャーから最新の設定を読み込む。"""
        self.cpu_max_prefetch_pages = self.settings_manager.get('cpu_max_prefetch_pages', 9)
        self.gpu_max_prefetch_pages = self.settings_manager.get('gpu_max_prefetch_pages', 4)
        logging.info(f"[Prefetcher] Settings updated. CPU prefetch pages: {self.cpu_max_prefetch_pages}, GPU prefetch pages: {self.gpu_max_prefetch_pages}")

    def _calculate_pages_for_prefetch(self, current_index: int, distance: int, max_index: int, is_spread: bool) -> list[int]:
        """
        Calculates the pages to prefetch based on the current index and distance.
        """
        # 1. 基準となるページを決定
        base_pages = [current_index]
        if is_spread and current_index + 1 <= max_index:
            base_pages.append(current_index + 1)

        # 2. 基準ページから範囲を拡大して全対象ページをセットとして収集（重複排除）
        all_pages_to_prefetch = set()
        for page in base_pages:
            # 範囲が 0 と max_index を超えないようにクランプする
            start = max(0, page - distance)
            end = min(max_index, page + distance)
            for i in range(start, end + 1):
                all_pages_to_prefetch.add(i)

        # 3. ソート済みリストとして返す
        return sorted(list(all_pages_to_prefetch))

    def _fill_cpu_cache_gaps(self, prefetch_pages):
        """CPUキャッシュの歯抜けを検知し、ロード要求を出す。"""
        cpu_gaps = [p for p in prefetch_pages if self.image_cache.get(p) is None]
        logging.debug(f"Detected CPU cache gaps: {cpu_gaps}")

        if cpu_gaps:
            logging.info(f"[Prefetcher] Prefetching CPU pages: {cpu_gaps}")
        for page_index in cpu_gaps:
            if not self._is_running: break
            self.prefetch_request_generated.emit(page_index, constants.PRIORITY_PREFETCH)

    def _fill_gpu_cache_gaps(self, prefetch_pages):
        """GPUキャッシュの歯抜けを検知し、テクスチャ準備要求を出す。"""
        gpu_gaps = []
        for p in prefetch_pages:
            key = f"{self.app_state.current_file_path}::{p}"
            is_in_texture_cache = self.texture_cache.get(key) is not None
            is_in_image_cache = self.image_cache.get(p) is not None
            if not is_in_texture_cache and is_in_image_cache:
                gpu_gaps.append(p)
            logging.debug(f"[PREFETCHER_GPU_DEBUG] Page {p}: key='{key}', in_texture_cache={is_in_texture_cache}, in_image_cache={is_in_image_cache}")

        logging.debug(f"Detected GPU cache gaps: {gpu_gaps}")

        if gpu_gaps:
            logging.info(f"[Prefetcher] Prefetching GPU pages: {gpu_gaps}")
        for page_index in gpu_gaps:
            if not self._is_running: break
            self.texture_preparation_requested.emit(page_index)