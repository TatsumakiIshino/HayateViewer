import os
import zipfile
import io
import logging
import re
import threading
from abc import ABC, abstractmethod
from enum import Enum, auto

import py7zr
import unrar
from PySide6.QtCore import QThread, Signal, Slot, QMutex, QWaitCondition

from app.constants import SUPPORTED_FORMATS

log = logging.getLogger(__name__)

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
        # TODO: natsortを使った自然順ソートを後で実装する
        return sorted(supported_files)

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
        # TODO: natsortを使った自然順ソートを後で実装する
        return sorted(supported_files)

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

    def __init__(self, file_like_object):
        """
        RarReaderを初期化します。

        Args:
            file_like_object: RAR書庫のファイルライクオブジェクト。
        """
        super().__init__(file_like_object)
        self.rar_file = unrar.rarfile.RarFile(self.file_like_object)

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
        # TODO: natsortを使った自然順ソートを後で実装する
        return sorted(supported_files)

    def read_file(self, name: str) -> bytes:
        """
        指定されたファイルをバイナリデータとして読み込みます。

        Args:
            name: 読み込むファイルの名前。

        Returns:
            ファイルのバイナリデータ。
        """
        self.file_like_object.seek(0)
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
    現在の表示ページに応じて、展開の優先順位を動的に変更する。
    """
    progress = Signal(int, int)  # current, total
    first_file_extracted = Signal(str)
    finished_with_status = Signal(ExtractionStatus)

    def __init__(self, reader: IArchiveReader, file_list: list[str], cache: dict, cache_lock: QMutex, cache_wait_condition):
        super().__init__()
        self.reader = reader
        self.cache = cache
        self.cache_lock = cache_lock
        self.cache_wait_condition = cache_wait_condition

        self.unextracted_files = file_list[:]
        self.total_files = len(self.unextracted_files)
        
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

    def _find_closest_file(self) -> str | None:
        """未展開のファイルの中から、現在のページに最も近いファイルを見つける。"""
        if not self.unextracted_files:
            return None

        current_page = self._get_current_page()
        
        def get_numeric_part(filename):
            numbers = re.findall(r'\d+', os.path.basename(filename))
            return int(numbers[-1]) if numbers else -1

        closest_file = min(
            self.unextracted_files,
            key=lambda f: abs(get_numeric_part(f) - current_page)
        )
        return closest_file

    def run(self):
        """メインの展開ループ。"""
        self._status = ExtractionStatus.RUNNING
        first_file_emitted = False
        
        while self._running and self.unextracted_files:
            target_file = self._find_closest_file()
            if target_file is None:
                break

            # すでにキャッシュに存在するか最終確認
            self.cache_lock.lock()
            is_cached = target_file in self.cache
            self.cache_lock.unlock()

            if is_cached:
                self.unextracted_files.remove(target_file)
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

                if target_file in self.unextracted_files:
                    self.unextracted_files.remove(target_file)
                
                extracted_count = self.total_files - len(self.unextracted_files)
                self.progress.emit(extracted_count, self.total_files)

            except Exception as e:
                log.error(f"Failed to extract {target_file}: {e}", exc_info=True)
                self.unextracted_files.remove(target_file) # エラーが発生したファイルは再試行しない

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
