import logging
from PySide6.QtCore import QObject, QThread, Slot, Signal, QMetaObject, Qt

from app.io.loader import ImageLoaderWorker, FileLoader
from app.io.archive import ExtractionThread, ExtractionStatus
from app.core.prefetcher import PrefetcherWorker
from app.core.state import AppState
from app.config.settings import Settings
from app.core.cache import ImageCache

log = logging.getLogger(__name__)

class ThreadManager(QObject):
    """
    アプリケーションのすべてのバックグラウンドスレッドとワーカーを管理します。
    """
    file_loader_updated = Signal(FileLoader)
    cache_settings_changed = Signal(dict)

    # ExtractionThreadからのシグナルを中継
    extraction_started = Signal()
    extraction_progress = Signal(int, int)
    extraction_completed = Signal()
    extraction_cancelled = Signal()
    first_file_extracted = Signal(str)

    # ImageLoaderWorkerからのシグナルを中継
    loader_ready = Signal()

    def __init__(self, app_state: AppState, settings: Settings, image_cache: ImageCache, event_bus, parent: QObject | None = None):
        super().__init__(parent)
        self.app_state = app_state
        self.settings = settings
        self.image_cache = image_cache
        self.event_bus = event_bus
        self.texture_cache = None # setup_threadsで設定される
        self.current_file_loader: FileLoader | None = None
        self.extraction_thread: ExtractionThread | None = None

        # --- ワーカーとスレッドの初期化 ---
        self.image_loader_thread = QThread()
        self.image_loader_thread.setObjectName("ImageLoaderThread")
        self.image_loader_worker = ImageLoaderWorker(self.image_cache, self.settings)
        self.image_loader_worker.moveToThread(self.image_loader_thread)

        self.prefetcher_thread = QThread()
        self.prefetcher_thread.setObjectName("PrefetcherThread")
        self.prefetcher_worker = PrefetcherWorker(self.app_state, self.settings, self.image_cache, self.texture_cache)
        self.prefetcher_worker.moveToThread(self.prefetcher_thread)

        self._connect_signals()

    def _connect_signals(self):
        """ワーカーとスレッド間のシグナルを接続します。"""
        # ライフサイクル
        self.image_loader_worker.finished.connect(self.image_loader_thread.quit)
        self.prefetcher_worker.finished.connect(self.prefetcher_thread.quit)

        # スレッド開始時の動作
        self.image_loader_thread.started.connect(self.image_loader_worker.process_next_task)
        # self.prefetcher_thread.started.connect(self.prefetcher_worker.run) # イベントループをブロックするため削除

        # ワーカー間連携
        self.prefetcher_worker.prefetch_request_generated.connect(self.image_loader_worker.add_task)
        self.prefetcher_worker.texture_preparation_requested.connect(self.image_loader_worker.texture_preparation_requested)

        # 状態変化の伝達
        self.file_loader_updated.connect(self.on_file_loader_updated)
        self.file_loader_updated.connect(self.prefetcher_worker.on_context_changed)
        self.cache_settings_changed.connect(self.prefetcher_worker.update_prefetch_settings)


        # ImageLoaderWorkerの状態を中継する
        self.image_loader_worker.ready_to_load.connect(self.loader_ready)

        # EventBusからのイベントを処理
        self.event_bus.RESAMPLING_MODE_CHANGED.connect(self.on_resampling_mode_changed)

    def setup_threads(self, texture_cache):
        """
        すべてのワーカースレッドを開始します。
        このメソッドはアプリケーション起動時に一度だけ呼び出されるべきです。
        """
        log.info("Setting up and starting threads...")
        if self.image_loader_thread.isRunning() or self.prefetcher_thread.isRunning():
            log.warning("Threads are already running.")
            return

        self.texture_cache = texture_cache
        if self.prefetcher_worker:
            self.prefetcher_worker.texture_cache = texture_cache
        
        self.image_loader_thread.start()
        self.prefetcher_thread.start()
        log.info("All threads have been started.")

    def cleanup_threads(self):
        """
        管理下のすべてのスレッドを安全に停止し、クリーンアップします。
        """
        log.info("Cleaning up threads...")
        
        # 依存関係を考慮し、Prefetcherから停止する
        if self.prefetcher_thread and self.prefetcher_thread.isRunning():
            log.debug("Stopping prefetcher worker...")
            # stop()はワーカスレッドのイベントキュー経由で実行される
            # stop()がfinishedシグナルを発行し、それがthread.quit()に接続されている
            self.prefetcher_worker.stop()
            if not self.prefetcher_thread.wait(5000):
                log.warning("Prefetcher thread did not finish in time.")

        # 次にImageLoaderを停止する
        if self.image_loader_thread and self.image_loader_thread.isRunning():
            log.debug("Stopping image loader worker...")
            self.image_loader_worker.stop()
            # finishedシグナルでquitが呼ばれるのを待つ
            if not self.image_loader_thread.wait(5000):
                log.warning("Image loader thread did not finish in time.")

        # 最後にExtractionThreadを停止する
        self._stop_extraction_thread()

        log.info("Threads cleanup process finished.")
        self.image_loader_worker = None
        self.image_loader_thread = None
        self.prefetcher_worker = None
        self.prefetcher_thread = None
        self.extraction_thread = None

    def get_image_loader(self) -> ImageLoaderWorker | None:
        """ImageLoaderWorkerのインスタンスを返します。"""
        return self.image_loader_worker

    def get_prefetcher(self) -> PrefetcherWorker | None:
        """PrefetcherWorkerのインスタンスを返します。"""
        return self.prefetcher_worker

    @Slot()
    def on_resampling_mode_changed(self):
        """リサンプリングモード変更イベントを処理します。"""
        log.debug("ThreadManager handling RESAMPLING_MODE_CHANGED event.")
        if self.image_loader_worker:
            # ImageLoaderWorkerは別スレッドにいるため、QueuedConnectionでメソッドを呼び出す
            QMetaObject.invokeMethod(self.image_loader_worker, "update_resampling_mode", Qt.ConnectionType.QueuedConnection)

    @Slot(FileLoader)
    def on_file_loader_updated(self, new_loader: FileLoader):
        """新しいFileLoaderが設定されたときに呼び出されます。"""
        log.info(f"FileLoader updated. New loader id: {id(new_loader)}, Old loader id: {id(self.current_file_loader) if self.current_file_loader else 'None'}")
        self.current_file_loader = new_loader
        self.image_loader_worker.set_file_loader(new_loader)

        self._stop_extraction_thread()

        if new_loader.load_type == 'archive':
            self._start_extraction_thread(new_loader)
        else:
            # フォルダ読み込みの場合、すぐにImageLoaderが利用可能であることを通知
            log.info("Direct folder load detected. Emitting loader_ready signal immediately.")
            self.loader_ready.emit()

    def _start_extraction_thread(self, loader: FileLoader):
        """ExtractionThreadを開始します。"""
        log.info(f"Starting extraction thread for FileLoader (id: {id(loader)})")
        if not loader.reader:
            log.error("Archive reader is not available. Cannot start extraction.")
            return

        self.extraction_thread = ExtractionThread(
            reader=loader.reader,
            file_list=loader.get_image_list(),
            cache=loader.cache,
            cache_lock=loader.cache_lock,
            cache_wait_condition=loader.cache_wait_condition
        )
        # シグナルを中継
        self.extraction_thread.progress.connect(self.extraction_progress)
        self.extraction_thread.finished_with_status.connect(self._on_extraction_finished)
        self.extraction_thread.first_file_extracted.connect(self.first_file_extracted)
        
        # AppStateからのページ変更通知を接続
        self.app_state.page_index_changed.connect(self.extraction_thread.update_current_page)

        self.extraction_thread.start()
        self.extraction_started.emit()
        log.info(f"Extraction thread (id: {id(self.extraction_thread)}) started for: {loader.path}")

    def _stop_extraction_thread(self):
        """既存のExtractionThreadを停止します。"""
        if self.extraction_thread and self.extraction_thread.isRunning():
            thread_id = id(self.extraction_thread)
            log.info(f"Stopping existing extraction thread (id: {thread_id})...")
            # ページ変更シグナルの切断
            try:
                self.app_state.page_index_changed.disconnect(self.extraction_thread.update_current_page)
                log.info(f"Disconnected page_index_changed from thread (id: {thread_id}).")
            except (RuntimeError, TypeError):
                log.warning(f"Could not disconnect page_index_changed from old extraction thread (id: {thread_id}). It might have been already deleted.")

            self.extraction_thread.stop()
            if self.extraction_thread.wait(5000):
                log.info(f"Extraction thread (id: {thread_id}) finished gracefully.")
            else:
                log.warning(f"Extraction thread (id: {thread_id}) did not finish in time.")
            self.extraction_thread = None
            log.info(f"Reference to extraction thread (id: {thread_id}) released.")

    @Slot(ExtractionStatus)
    def _on_extraction_finished(self, status: ExtractionStatus):
        """ExtractionThreadの終了を処理します。"""
        if self.current_file_loader:
            self.current_file_loader.set_extraction_status(status)
        
        if status == ExtractionStatus.COMPLETED:
            log.info("==> [ThreadManager] Emitting extraction_completed signal.")
            self.extraction_completed.emit()
        elif status == ExtractionStatus.CANCELLED:
            self.extraction_cancelled.emit()
        
        log.info(f"Extraction finished. Status: {status.name}")