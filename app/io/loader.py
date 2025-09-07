import os
import io
import re
from collections import deque
import threading
import logging
from enum import Enum, auto
from dataclasses import dataclass, field
from PySide6.QtCore import QThread, Signal, QObject, Slot, QMutex, QWaitCondition, QMetaObject, Qt, QRunnable, QThreadPool
from PySide6.QtGui import QImage
import cv2
import numpy as np

from app.constants import SUPPORTED_FORMATS, SUPPORTED_ARCHIVE_FORMATS, PRIORITY_DISPLAY, PRIORITY_PREFETCH
from app.io.archive import IArchiveReader, ExtractionStatus, ZipReader, SevenZipReader, RarReader
from app.config.settings import Settings

log = logging.getLogger(__name__)

# --- Globals for Unique IDs ---
_file_loader_id_counter = 0
_loader_id_lock = threading.Lock()

def get_next_file_loader_id():
    """Generate a thread-safe, unique ID for a FileLoader."""
    global _file_loader_id_counter
    with _loader_id_lock:
        _file_loader_id_counter += 1
        return _file_loader_id_counter

def natural_sort_key(s):
    """自然順ソート用のキーを生成する。"""
    return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]

# --- Load Task Definition ---
@dataclass(order=True)
class LoadTask:
    """画像読み込みタスクを表すデータクラス。"""
    priority: int
    page_index: int = field(compare=False)

    def __post_init__(self):
        if self.page_index is None:
            self.page_index = -1


# --- Runnable for Parallel IO and Decoding ---
class RunnableSignals(QObject):
    """QRunnableからシグナルを送信するためのQObject。"""
    finished = Signal(object, float, int, int)  # decoded_image, decode_time, index, loader_id
    error = Signal(str, str, int, int)          # filepath, message, index, loader_id

class LoadDecodeRunnable(QRunnable):
    """
    ファイル読み込み(I/O)と画像デコード(CPU)をまとめて行うワーカー。
    QThreadPoolのワーカで実行されることで、メインスレッドやワーカースレッドをブロックしない。
    """
    def __init__(self, file_loader: 'FileLoader', filepath: str, index: int, signals: RunnableSignals, priority: int):
        super().__init__()
        self.file_loader = file_loader
        self.filepath = filepath
        self.index = index
        self.signals = signals
        self.priority = priority
        self.loader_id = file_loader.id
        self.setAutoDelete(True)

    @Slot()
    def run(self):
        """QThreadPoolによって実行されるメインロジック。"""
        try:
            # 1. ファイル読み込み (I/O-bound)
            image_data = self.file_loader.get_image_data(self.filepath, self.priority)
            if not image_data:
                self.signals.finished.emit(None, 0.0, self.index, self.loader_id)
                return

            # 2. 画像デコード (CPU-bound)
            import time
            start_time = time.perf_counter()
            
            np_arr = np.frombuffer(image_data, np.uint8)
            # IMREAD_UNCHANGEDで元のチャンネル数のまま読み込む
            image = cv2.imdecode(np_arr, cv2.IMREAD_UNCHANGED)
            
            if image is None:
                self.signals.finished.emit(None, 0.0, self.index, self.loader_id)
                return

            # チャンネル数をチェックし、3チャンネルのBGR形式に正規化する
            if len(image.shape) == 2: # グレースケール
                image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
            elif image.shape[2] == 4: # BGRA
                image = cv2.cvtColor(image, cv2.COLOR_BGRA2BGR)
            
            end_time = time.perf_counter()
            decode_time = (end_time - start_time) * 1000
            self.signals.finished.emit(image, decode_time, self.index, self.loader_id)

        except Exception as e:
            log.error(f"Error in runnable for {self.filepath}: {e}", exc_info=True)
            self.signals.error.emit(self.filepath, str(e), self.index, self.loader_id)

# --- ファイル/フォルダ読み込み ---
class FileLoader(QObject):
    """
    ファイル、フォルダ、書庫ファイルを読み込み、中の画像ファイルリストを管理するクラス。
    Qtベースのシグナル/スロットで状態変化を通知する。
    """

    def __init__(self, path, parent=None):
        # IMPORTANT: Initialize path before super().__init__ to prevent AttributeError in __del__
        self.path = path
        super().__init__(parent)
        self.id = get_next_file_loader_id()
        log.info(f"FileLoader created for path: {path} (id: {self.id}, thread: {id(QThread.currentThread())})")
        if not os.path.exists(path):
            raise FileNotFoundError(f'"{path}"が見つかりません。')
        self.load_type = self._determine_load_type()
        self.reader: IArchiveReader | None = None
        self.archive_data: io.BytesIO | None = None

        if self.load_type == 'archive':
            # For RAR files, we need the file path, not in-memory data.
            ext = os.path.splitext(self.path)[1].lower()
            if ext not in ['.rar', '.cbr']:
                try:
                    with open(self.path, 'rb') as f:
                        self.archive_data = io.BytesIO(f.read())
                    log.info(f"Loaded archive '{self.path}' into memory ({self.archive_data.getbuffer().nbytes / (1024*1024):.2f} MB)")
                except Exception as e:
                    log.error(f"Failed to load archive file into memory: {e}")
                    raise IOError(f"書庫ファイル '{self.path}' をメモリに読み込めませんでした。") from e

        self.image_list = self._create_image_list()

        # L3 Cache
        self.cache = {}
        self.cache_lock = QMutex() # threading.LockからQMutexに変更
        self.cache_wait_condition = QWaitCondition()
        self._extraction_status = ExtractionStatus.PENDING

    def __del__(self):
        log.info(f"FileLoader garbage collected: {self.path} (id: {self.id})")

    def _determine_load_type(self):
        if os.path.isdir(self.path):
            return 'folder'
        elif os.path.isfile(self.path):
            ext = os.path.splitext(self.path)[1].lower()
            if ext in SUPPORTED_ARCHIVE_FORMATS:
                return 'archive'
            elif ext in SUPPORTED_FORMATS:
                return 'image'
        return 'unsupported'

    def _create_image_list(self):
        image_list = []
        if self.load_type == 'folder':
            try:
                filenames = os.listdir(self.path)
                sorted_filenames = sorted(filenames, key=natural_sort_key)
                for filename in sorted_filenames:
                    if os.path.splitext(filename)[1].lower() in SUPPORTED_FORMATS:
                        image_list.append(os.path.join(self.path, filename))
            except Exception as e:
                log.error(f"Error reading or sorting directory {self.path}: {e}")
        elif self.load_type == 'image':
            image_list.append(self.path)
        elif self.load_type == 'archive':
            ext = os.path.splitext(self.path)[1].lower()
            
            try:
                if ext in ['.zip', '.cbz']:
                    if not self.archive_data: return []
                    self.archive_data.seek(0)
                    self.reader = ZipReader(self.archive_data)
                elif ext in ['.7z', '.cb7']:
                    if not self.archive_data: return []
                    self.archive_data.seek(0)
                    self.reader = SevenZipReader(self.archive_data)
                elif ext in ['.rar', '.cbr']:
                    self.reader = RarReader(self.path)
                else:
                    raise NotImplementedError(f"Unsupported archive format: {ext}")
                
                image_list = self.reader.get_filelist()
            except Exception as e:
                log.error(f"Failed to create archive reader or get filelist: {e}", exc_info=True)
                return []
        return image_list

    def get_image_list(self):
        return self.image_list

    def get_file_path(self, index: int) -> str | None:
        """指定されたインデックスのファイルパス（または書庫内のファイル名）を取得します。"""
        if 0 <= index < len(self.image_list):
            return self.image_list[index]
        return None

    def get_image_data(self, filepath, priority=PRIORITY_DISPLAY):
        self.cache_lock.lock()
        try:
            # ループでキャッシュの存在を確認し、なければ待機条件を評価
            while filepath not in self.cache:
                extraction_status = self.get_extraction_status()
                log.debug(f"[GET_IMAGE_DATA_DEBUG] Cache miss for '{filepath}'. Priority: {priority}, Status: {extraction_status.name}")

                # 待機条件の判定
                should_wait = (
                    self.load_type == 'archive' and
                    extraction_status == ExtractionStatus.RUNNING and
                    priority == PRIORITY_DISPLAY
                )

                if should_wait:
                    log.debug(f"Waiting for '{filepath}' (Priority: DISPLAY)...")
                    # waitは自動的にmutexをアンロックし、再開時に再ロックする
                    self.cache_wait_condition.wait(self.cache_lock)
                    log.debug(f"[GET_IMAGE_DATA_DEBUG] Woke up for '{filepath}'. Checking cache again.")
                else:
                    # 待機しない場合 (プリフェッチ or 展開が完了/失敗/キャンセル or 書庫ではない)
                    if priority == PRIORITY_PREFETCH and self.load_type == 'archive':
                        log.debug(f"Not waiting for '{filepath}' (Priority: PREFETCH). Returning None.")
                        return None # プリフェッチの場合はNoneを返して終了
                    
                    log.warning(f"[GET_IMAGE_DATA_DEBUG] Not waiting for '{filepath}'. Breaking wait loop. (Status: {extraction_status.name}, Priority: {priority})")
                    break # 表示要求だが展開中でない場合は、フォールバック読み込みを試す

            # 待機後、最終的にキャッシュに存在するか確認
            if filepath in self.cache:
                data = self.cache[filepath]
                log.info(f"[FileLoader] Got '{filepath}' from L3 cache ({len(data) if data else 0} bytes).")
                return data
        finally:
            self.cache_lock.unlock()

        # ロックが解放された後、ファイルシステムまたは書庫から直接読み込む
        data = None
        if self.load_type == 'archive':
            if self.reader:
                # このread_fileはキャッシュにない場合の最終手段
                data = self.reader.read_file(filepath)
                if data:
                    log.info(f"[FileLoader] Read '{filepath}' from archive reader as a fallback ({len(data)} bytes).")
                    # フォールバックで読み込めた場合、キャッシュに書き込む
                    self.cache_lock.lock()
                    try:
                        self.cache[filepath] = data
                    finally:
                        self.cache_lock.unlock()
            else:
                # readerがない場合はエラーとし、ファイルシステムからの読み込みは試みない
                log.error(f"[FileLoader] Archive reader not found for an archive type. Cannot read {filepath}.")
                return None
        else:  # 'folder' or 'image'
            try:
                # フルパスのはずなので、直接ファイルを開く
                with open(filepath, 'rb') as f:
                    data = f.read()
                log.info(f"[FileLoader] Read '{filepath}' from filesystem ({len(data) if data else 0} bytes).")
            except FileNotFoundError:
                log.error(f"[FileLoader] File not found: {filepath}")
                return None

        return data

    def get_extraction_status(self):
        return self._extraction_status

    def set_extraction_status(self, status: ExtractionStatus):
        self._extraction_status = status

    @Slot()
    def stop(self):
        log.info(f"==> [TID: {id(QThread.currentThread())}] Stopping FileLoader for path: {self.path} (id: {self.id})")
        if self.reader:
            self.reader.close()
        log.info(f"==> [TID: {id(QThread.currentThread())}] FileLoader cleanup finished for path: {self.path} (id: {self.id})")


# --- 画像読み込みワーカ ---
class ImageLoaderWorker(QObject):
    """
    ワーカスレッドで画像読み込みとデコードを実行するクラス。
    Qtのシグナル/スロットと状態管理により、堅牢な非同期処理を実現する。
    """
    image_loaded = Signal(QImage, int) # qimage, index
    texture_preparation_requested = Signal(int)
    error_occurred = Signal(str, str) # path, message
    finished = Signal()
    ready_to_load = Signal()

    def __init__(self, image_cache, settings: Settings):
        super().__init__()
        log.info(f"[InstanceTracker] ImageLoader created: {id(self)} in thread {id(QThread.currentThread())}")
        self.file_loader: FileLoader | None = None
        self.image_files = []
        self.image_cache = image_cache
        self.settings = settings
        
        self.high_priority_queue = deque()
        self.low_priority_queue = deque()
        self.processing_pages = set()
        
        self._running = True

        # GPUテクスチャ準備要求のシグナルを自身のスロットに接続
        self.texture_preparation_requested.connect(self.on_texture_preparation_requested)

    @Slot(FileLoader)
    def set_file_loader(self, file_loader: FileLoader):
        """[スロット] 新しいFileLoaderでワーカーを更新する。"""
        log.info(f"Setting/Updating ImageLoaderWorker with new FileLoader for path: {file_loader.path}")
        self.file_loader = file_loader
        self.image_files = file_loader.get_image_list()
        
        # 既存のタスクをキャンセルしてキューをクリア
        self.high_priority_queue.clear()
        self.low_priority_queue.clear()
        self.processing_pages.clear()
        
        # 準備完了を通知
        self.ready_to_load.emit()

    @Slot(int)
    def on_texture_preparation_requested(self, page_index: int):
        """[スロット] CPUキャッシュからテクスチャを準備する要求を処理する。"""
        if not self._running or not (0 <= page_index < len(self.image_files)):
            return

        # CPUキャッシュから画像データを取得
        decoded_image = self.image_cache.get(page_index)
        if decoded_image is not None:
            log.info(f"Preparing texture from CPU cache for page {page_index}.")
            # デコード後の処理を呼び出す
            self._on_image_decoded(decoded_image, page_index)
        else:
            # CPUキャッシュにない場合は、通常のロードタスクとして追加する
            log.warning(f"Page {page_index} not in CPU cache for texture preparation. Adding as a low priority task.")
            self.add_task(page_index, PRIORITY_PREFETCH)

    @Slot(int, int)
    def add_task(self, page_index: int, priority: int):
        """[スロット] UIスレッドからタスクを追加する。スレッドセーフ。"""
        if not self._running or not self.image_files or not (0 <= page_index < len(self.image_files)):
            log.warning(f"[ImageLoader] Invalid page index or worker not running: {page_index}")
            return

        in_cache = page_index in self.image_cache
        is_processing = page_index in self.processing_pages
        in_queue = any(task.page_index == page_index for task in self.high_priority_queue) or \
                   any(task.page_index == page_index for task in self.low_priority_queue)

        if not in_cache and not is_processing and not in_queue:
            task = LoadTask(priority=priority, page_index=page_index)
            if priority == PRIORITY_DISPLAY:
                self.high_priority_queue.append(task)
            else:
                self.low_priority_queue.append(task)
            
            log.debug(f"[ADD_TASK_DEBUG] Added page {page_index} to queue with priority {priority}. processing_pages: {self.processing_pages}")
            # タスク処理チェーンを開始
            QMetaObject.invokeMethod(self, "process_next_task", Qt.ConnectionType.QueuedConnection)
        else:
            log.info(f"Page {page_index} is already in cache, being processed, or in queue. Skipping. (in_cache={in_cache}, is_processing={is_processing}, in_queue={in_queue})")

    @Slot()
    def process_next_task(self):
        """キューから次のタスクを取り出して処理する。"""
        if not self._running:
            return

        max_workers = self.settings.get('parallel_decoding_workers', 1)
        if max_workers <= 0:
            max_workers = 1

        while len(self.processing_pages) < max_workers:
            task: LoadTask | None = None
            if self.high_priority_queue:
                task = self.high_priority_queue.popleft()
            elif self.low_priority_queue:
                task = self.low_priority_queue.popleft()

            if task is None:
                break

            page_index = task.page_index
            priority = task.priority

            # 既に処理中の場合はスキップ (念のため)
            if page_index in self.processing_pages:
                log.warning(f"Skipping task for page {page_index} as it is already being processed.")
                continue

            self.processing_pages.add(page_index)
            self.load_page_data(page_index, priority)

    def load_page_data(self, index, priority):
        if not self._running or not self.image_files or not (0 <= index < len(self.image_files)):
            return

        filepath = self.image_files[index]
        try:
            signals = RunnableSignals()
            signals.finished.connect(self._on_runnable_finished, Qt.ConnectionType.QueuedConnection)
            signals.error.connect(self._on_runnable_error, Qt.ConnectionType.QueuedConnection)

            runnable = LoadDecodeRunnable(self.file_loader, filepath, index, signals, priority)
            QThreadPool.globalInstance().start(runnable)
            log.info(f"[ImageLoaderWorker] Submitted runnable to QThreadPool for page {index}.")

        except Exception as e:
            log.error(f"画像読み込みタスクの投入中にエラーが発生しました: {filepath}, error: {e}")
            self.error_occurred.emit(filepath, str(e))
            self._on_image_decoded(None, index)

    @Slot(object, float, int, int)
    def _on_runnable_finished(self, decoded_image, decode_time, index, loader_id):
        """LoadDecodeRunnableからの完了シグナルを処理する。"""
        if not self._running or not self.file_loader or self.file_loader.id != loader_id:
            log.warning(f"Ignoring result from obsolete FileLoader (id: {loader_id}) for page {index}.")
            if index in self.processing_pages:
                self.processing_pages.remove(index)
            QMetaObject.invokeMethod(self, "process_next_task", Qt.ConnectionType.QueuedConnection)
            return

        if decoded_image is not None:
            log.info(f"Load/Decode time for page {index}: {decode_time:.2f} ms")
        
        self._on_image_decoded(decoded_image, index)
        QMetaObject.invokeMethod(self, "process_next_task", Qt.ConnectionType.QueuedConnection)

    @Slot(str, str, int, int)
    def _on_runnable_error(self, filepath, message, index, loader_id):
        """LoadDecodeRunnableからのエラーシグナルを処理する。"""
        if not self._running or not self.file_loader or self.file_loader.id != loader_id:
            log.warning(f"Ignoring error from obsolete FileLoader (id: {loader_id}) for page {index}.")
            if index in self.processing_pages:
                self.processing_pages.remove(index)
            QMetaObject.invokeMethod(self, "process_next_task", Qt.ConnectionType.QueuedConnection)
            return

        log.error(f"ロード/デコード処理中にエラー: {message}", exc_info=False)
        self.error_occurred.emit(filepath, message)
        self._on_image_decoded(None, index)
        QMetaObject.invokeMethod(self, "process_next_task", Qt.ConnectionType.QueuedConnection)

    def numpy_to_qimage(self, np_image):
        if np_image is None:
            return None
        try:
            height, width, channel = np_image.shape
            bytes_per_line = channel * width
            rgb_image = np_image[..., ::-1].copy()
            qimage = QImage(rgb_image.data, width, height, bytes_per_line, QImage.Format.Format_RGB888).copy()
            return qimage
        except Exception as e:
            log.error(f"Failed to convert numpy array to QImage: {e}")
            return None

    def _on_image_decoded(self, decoded_image, index):
        # このメソッドは、loader_idのチェックが完了した後に呼び出されるため、
        # self.file_loaderが現在の正しいローダーであることを前提として良い。
        if not self.file_loader or not self.image_files or not (0 <= index < len(self.image_files)):
            log.warning(f"_on_image_decoded called with invalid index {index} or missing file_loader/image_files.")
            return

        filepath = self.image_files[index]
        try:
            if decoded_image is not None:
                # デコードされたNumPy配列をそのままキャッシュに保存
                self.image_cache.set(index, decoded_image)
                
                # PySide6モードの場合、UI側でリサンプリングとQImage変換を行うため、
                # ここではimage_loadedシグナルは発行しない。
                # 代わりに、キャッシュに保存されたことを通知する。
                # (この通知はPrefetcherでも利用される)
                # UIManagerがこのシグナルを捉えてUIを更新する。
                
                # ただし、OpenGLモードではテクスチャ準備のためにQImageが必要。
                # そのため、numpy_to_qimageは残し、シグナルを発行する。
                # TODO: このあたりの責務を再整理する必要があるかもしれない。
                # 現状では、両方のモードで動作するようにしておく。
                qimage = self.numpy_to_qimage(decoded_image)
                if qimage:
                    self.image_loaded.emit(qimage, index)
                else:
                    log.error(f"==> [ImageLoader] QImage conversion FAILED for page {index}.")
                    self.error_occurred.emit(filepath, f"QImageへの変換失敗: {os.path.basename(filepath)}")

        except Exception as e:
            log.error(f"画像デコード後の処理中にエラーが発生しました: {filepath}, error: {e}", exc_info=True)
            self.error_occurred.emit(filepath, str(e))
        finally:
            if index in self.processing_pages:
                self.processing_pages.remove(index)

    @Slot()
    def stop(self):
        """[スロット] 処理ループを安全に停止させる。"""
        log.info(f"==> [VERIFICATION][TID: {id(QThread.currentThread())}] Stopping ImageLoaderWorker {id(self)}...")
        if not self._running:
            log.warning(f"==> [TID: {id(QThread.currentThread())}] ImageLoaderWorker {id(self)} already stopping.")
            return
        self._running = False
        
        self.high_priority_queue.clear()
        self.low_priority_queue.clear()
        
        self.finished.emit()
        log.info(f"==> [VERIFICATION][TID: {id(QThread.currentThread())}] ImageLoaderWorker {id(self)} stop() finished.")