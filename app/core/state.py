import json
import os
import logging
from PySide6.QtCore import QObject, Signal

class AppState(QObject):
    page_index_changed = Signal(int, bool)
    file_list_changed = Signal()
    view_mode_changed = Signal(bool)

    def __init__(self, settings_manager):
        super().__init__()
        self.settings_manager = settings_manager
        self.current_file_path = None
        self.file_loader = None
        self.image_loader = None
        self.image_files = []
        self.total_pages = 0
        self.is_content_loaded = False
        self._current_page_index = 0
        self._is_spread_view = False
        self.is_spread_view = self.settings_manager.get('is_spread_view', False)
        self.binding_direction = self.settings_manager.get('binding_direction', 'right')
        self.spread_view_first_page_single = self.settings_manager.get('spread_view_first_page_single', False)
        self.resampling_mode = self.settings_manager.get('resampling_mode', 'PIL_BILINEAR')
        self.is_zooming = False
        self.gpu_prefetch_range = set()

    @property
    def current_page_index(self):
        return self._current_page_index

    @current_page_index.setter
    def current_page_index(self, value):
        if self._current_page_index != value:
            logging.info(f"[State] Page index changed from {self._current_page_index} to {value}. is_spread_view: {self.is_spread_view}")
            self._current_page_index = value
            self.page_index_changed.emit(self.current_page_index, self.is_spread_view)

    @property
    def is_spread_view(self):
        return self._is_spread_view

    @is_spread_view.setter
    def is_spread_view(self, value):
        if self._is_spread_view != value:
            self._is_spread_view = value
            # self.view_mode_changed.emit(value) # AppControllerがUI更新を制御する
            logging.info(f"[State] Spread view mode changed to {value}")

    def set_image_loader(self, loader):
        self.image_loader = loader

    def set_file_list(self, file_list):
        self.image_files = file_list
        self.total_pages = len(file_list)
        self.is_content_loaded = bool(file_list)
        self.file_list_changed.emit()
        logging.info(f"[State] File list set with {self.total_pages} files.")

    def reset(self):
        self.current_page_index = 0
        self.is_content_loaded = False
        self.image_files = []
        if self.file_loader:
            self.file_loader.stop()
            self.file_loader = None
        self.image_loader = None

# This functionality is now handled by passing command line arguments.
