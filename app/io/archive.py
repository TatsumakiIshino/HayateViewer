import os
import zipfile
import io
import logging
import re
import threading
from abc import ABC, abstractmethod
from enum import Enum, auto

import py7zr
from unrar.cffi import rarfile
from PySide6.QtCore import QThread, Signal, Slot, QMutex, QWaitCondition

from app.constants import SUPPORTED_FORMATS

log = logging.getLogger(__name__)

def natural_sort_key(s):
    """自然順ソート用のキーを生成する。"""
    return [int(text) if text.isdigit() else text.lower() for text in re.split('([0-9]+)', s)]

# --- 書庫読み込みインターフェース ---
class IArchiveReader(ABC):
    """書庫読み込みのインターフェースを定義する抽象基底クラス。"""

    def __init__(self, file_like_object):
        self.file_like_object = file_like_object

    @abstractmethod
    def get_filelist(self) -> list[str]:
        """書庫内のファイル名リストを取得します。"""
        pass

    @abstractmethod
    def read_file(self, name: str) -> bytes:
        """指定されたファイルをバイナリデータとして読み込みます。"""
        pass

    @abstractmethod
    def close(self) -> None:
        """書庫ファイルを閉じ、リソースを解放します。"""
        pass


class ZipReader(IArchiveReader):
    """ZIP書庫を読み込むためのIArchiveReader実装。"""

    def __init__(self, file_like_object):
        """
        ZipReaderを初期化します。

        Args:
            file_like_object: ZIP書庫のファイルライクオブジェクト。
        """
        super().__init__(file_like_object)
        self.zip_file = zipfile.ZipFile(self.file_like_object)

    def get_filelist(self) -> list[str]:
        """
        書庫内のサポートされている画像ファイル名のリストを取得します。

        Returns:
            サポートされている形式のファイル名リスト。
        """
        all_files = self.zip_file.namelist()
        supported_files = [
            f for f in all_files
            if os.path.splitext(f)[1].lower() in SUPPORTED_FORMATS
        ]
        return sorted(supported_files, key=natural_sort_key)

    def read_file(self, name: str) -> bytes:
        """
        指定されたファイルをバイナリデータとして読み込みます。

        Args:
            name: 読み込むファイルの名前。

        Returns:
            ファイルのバイナリデータ。
        """
        self.file_like_object.seek(0)
        return self.zip_file.read(name)

    def close(self) -> None:
        """書庫ファイルを閉じ、リソースを解放します。"""
        self.zip_file.close()


class MemoryWriterFactory(py7zr.WriterFactory):
    def __init__(self):
        self.files = {}

    def create(self, filename, mode='wb'):
        self.files[filename] = io.BytesIO()
        return self.files[filename]


class SevenZipReader(IArchiveReader):
    """
    7Z書庫を読み込むためのIArchiveReader実装。
    オンデマンドでファイルを読み込みます。
    """

    def __init__(self, file_like_object):
        """
        SevenZipReaderを初期化します。

        Args:
            file_like_object: 7Z書庫のファイルライクオブジェクト。
        """
        super().__init__(file_like_object)
        self._all_files = None  # ファイルリストは初回取得時にキャッシュする

    def get_filelist(self) -> list[str]:
        """
        書庫内のサポートされている画像ファイル名のリストを取得します。
        """
        if self._all_files is None:
            with py7zr.SevenZipFile(self.file_like_object, 'r') as archive:
                self._all_files = archive.getnames()

        supported_files = [
            f for f in self._all_files
            if os.path.splitext(f)[1].lower() in SUPPORTED_FORMATS and not f.endswith('/')
        ]
        return sorted(supported_files, key=natural_sort_key)

    def read_file(self, name: str) -> bytes:
        """
        指定されたファイルをバイナリデータとして読み込みます。
        py7zrのextractメソッドとカスタムファクトリを使用して、メモリに展開します。
        """
        try:
            self.file_like_object.seek(0)
            with py7zr.SevenZipFile(self.file_like_object, 'r') as archive:
                factory = MemoryWriterFactory()
                archive.extract(targets=[name], factory=factory)
                if name in factory.files:
                    return factory.files[name].getvalue()
                else:
                    raise FileNotFoundError(f"File '{name}' not found in the 7z archive.")
        except Exception as e:
            log.error(f"Failed to read file '{name}' from 7z archive: {e}")
            raise FileNotFoundError(f"File '{name}' could not be read from the 7z archive.")

    def close(self) -> None:
        """
        リソースを解放します。この実装では何もしません。
        """
        pass


class RarReader(IArchiveReader):
    """RAR書庫を読み込むためのIArchiveReader実装。"""

    def __init__(self, file_path):
        """
        RarReaderを初期化します。

        Args:
            file_path: RAR書庫へのファイルパス。
        """
        super().__init__(file_path)
        self.rar_file = rarfile.RarFile(file_path)

    def get_filelist(self) -> list[str]:
        """
        書庫内のサポートされている画像ファイル名のリストを取得します。

        Returns:
            サポートされている形式のファイル名リスト。
        """
        all_files = self.rar_file.namelist()
        supported_files = [
            f for f in all_files
            if os.path.splitext(f)[1].lower() in SUPPORTED_FORMATS
        ]
        return sorted(supported_files, key=natural_sort_key)

    def read_file(self, name: str) -> bytes:
        """
        指定されたファイルをバイナリデータとして読み込みます。

        Args:
            name: 読み込むファイルの名前。

        Returns:
            ファイルのバイナリデータ。
        """
        return self.rar_file.read(name)

    def close(self) -> None:
        """書庫ファイルを閉じ、リソースを解放します。"""
        self.rar_file.close()


class ExtractionStatus(Enum):
    PENDING = auto()
    RUNNING = auto()
    COMPLETED = auto()
    FAILED = auto()
    CANCELLED = auto()


class ExtractionThread(QThread):
    """
    書庫ファイルをバックグラウンドでメモリに展開するスレッド。
    現在の表示ページが含まれるフォルダ単位で展開を行う。
    """
    progress = Signal(int, int)  # current, total
    first_file_extracted = Signal(str)
    finished_with_status = Signal(ExtractionStatus)

    def __init__(self, reader: IArchiveReader, file_list: list[str], folder_indices: list[int], cache: dict, cache_lock: QMutex, cache_wait_condition):
        super().__init__()
        self.reader = reader
        self.file_list = file_list
        self.folder_indices = folder_indices
        self.cache = cache
        self.cache_lock = cache_lock
        self.cache_wait_condition = cache_wait_condition

        self.total_files = len(self.file_list)
        self.unextracted_folders = list(range(len(self.folder_indices)))
        
        self.current_page_index = 0
        self.page_index_lock = threading.Lock()

        self._running = True
        self._status = ExtractionStatus.PENDING

    @Slot(int)
    def update_current_page(self, new_index: int):
        """UIスレッドから呼び出され、現在のページインデックスを更新する。"""
        with self.page_index_lock:
            self.current_page_index = new_index
        log.debug(f"Extraction priority updated. Current page index: {self.current_page_index}")

    def _get_current_page(self) -> int:
        """現在のページインデックスをスレッドセーフに取得する。"""
        with self.page_index_lock:
            return self.current_page_index

    def _find_closest_folder_index(self) -> int | None:
        """未展開のフォルダの中から、現在のページに最も近いフォルダを見つける。"""
        if not self.unextracted_folders:
            return None

        current_page = self._get_current_page()
        
        closest_folder_idx = -1
        min_distance = float('inf')

        for folder_idx in self.unextracted_folders:
            start_page = self.folder_indices[folder_idx]
            end_page = self.folder_indices[folder_idx + 1] - 1 if folder_idx + 1 < len(self.folder_indices) else self.total_files - 1
            
            if start_page <= current_page <= end_page:
                return folder_idx # 現在ページが含まれるフォルダを最優先

            distance = abs(start_page - current_page)
            if distance < min_distance:
                min_distance = distance
                closest_folder_idx = folder_idx
        
        return closest_folder_idx

    def run(self):
        """メインの展開ループ。フォルダ単位で処理する。"""
        self._status = ExtractionStatus.RUNNING
        first_file_emitted = False
        extracted_count = 0

        while self._running and self.unextracted_folders:
            target_folder_idx = self._find_closest_folder_index()
            if target_folder_idx is None:
                break

            start_page = self.folder_indices[target_folder_idx]
            end_page = self.folder_indices[target_folder_idx + 1] if target_folder_idx + 1 < len(self.folder_indices) else self.total_files
            
            files_in_folder = self.file_list[start_page:end_page]
            
            for target_file in files_in_folder:
                if not self._running:
                    break

                self.cache_lock.lock()
                is_cached = target_file in self.cache
                self.cache_lock.unlock()

                if is_cached:
                    continue

                try:
                    data = self.reader.read_file(target_file)
                    if data:
                        self.cache_lock.lock()
                        try:
                            self.cache[target_file] = data
                            if not first_file_emitted:
                                self.first_file_extracted.emit(target_file)
                                first_file_emitted = True
                            
                            data_size_mb = len(data) / (1024 * 1024)
                            log.info(f"[Extraction] Cached: {target_file} ({data_size_mb:.2f} MB)")
                        finally:
                            self.cache_lock.unlock()
                        self.cache_wait_condition.wakeAll()
                except Exception as e:
                    log.error(f"Failed to extract {target_file}: {e}", exc_info=True)

            if not self._running:
                break

            if target_folder_idx in self.unextracted_folders:
                self.unextracted_folders.remove(target_folder_idx)

            extracted_count += len(files_in_folder)
            self.progress.emit(extracted_count, self.total_files)

        if not self._running:
            self._status = ExtractionStatus.CANCELLED
        else:
            self._status = ExtractionStatus.COMPLETED
        
        log.info(f"Extraction finished. Status: {self._status.name}")
        self.finished_with_status.emit(self._status)

    def stop(self):
        """スレッドの停止を要求する。"""
        log.info("Stopping ExtractionThread...")
        self._running = False