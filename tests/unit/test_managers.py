import pytest
import os
from PySide6.QtWidgets import QLabel
from app.ui.managers.status_bar_manager import StatusBarManager
from app.ui.managers.title_manager import TitleManager

class TestStatusBarManager:
    def test_update_status_bar_single_page(self, mock_main_window, mock_app_state, mocker):
        mock_main_window.controller = mocker.Mock()
        
        manager = StatusBarManager(mock_main_window, mock_app_state)
        mock_app_state.is_content_loaded = True
        mock_app_state.total_pages = 10
        mock_app_state.is_spread_view = False
        mock_app_state.current_page_index = 0
        
        manager.update_status_bar([0])
        # Updated assertion to match "Page: 1 / 10"
        assert mock_main_window.page_info_label.text() == "Page: 1 / 10"

    def test_update_status_bar_spread_page(self, mock_main_window, mock_app_state, mocker):
        mock_main_window.controller = mocker.Mock()
        
        manager = StatusBarManager(mock_main_window, mock_app_state)
        mock_app_state.is_content_loaded = True
        mock_app_state.total_pages = 10
        mock_app_state.is_spread_view = True
        
        manager.update_status_bar([0, 1])
        # Updated assertion to match "Page: 1-2 / 10"
        assert mock_main_window.page_info_label.text() == "Page: 1-2 / 10"

    def test_update_dynamic_status_info(self, mock_main_window, mock_app_state, mocker):
        manager = StatusBarManager(mock_main_window, mock_app_state)
        mock_app_state.is_content_loaded = True
        
        mock_image_cache = mocker.Mock()
        mock_image_cache.current_size = 100 * 1024 * 1024 
        mock_image_cache.max_size = 1024 * 1024 * 1024 
        mock_image_cache.page_count = 5
        mock_image_cache.cache = {0: None, 1: None, 2: None, 3: None, 4: None} # mock cache keys
        
        mock_controller = mocker.Mock()
        mock_controller.image_cache = mock_image_cache
        mock_main_window.controller = mock_controller
        
        manager.update_dynamic_status_info()
        # Updated assertion to match implementation: "CPU: 5 pages [0, 1, 2, 3, 4]"
        assert "CPU: 5 pages [0, 1, 2, 3, 4]" in mock_main_window.cpu_cache_label.text()

class TestTitleManager:
    def test_update_window_title_no_content(self, mock_main_window, mock_app_state, mocker):
        mock_controller = mocker.Mock()
        mock_controller.file_loader = None
        mock_main_window.controller = mock_controller

        manager = TitleManager(mock_main_window, mock_app_state)
        mock_app_state.is_content_loaded = False
        
        mock_main_window.setWindowTitle = mocker.Mock()

        manager.update_window_title([])
        mock_main_window.setWindowTitle.assert_called_with("Project Hayate - 高速漫画ビューア")

    def test_update_window_title_with_content(self, mock_main_window, mock_app_state, mocker):
        mock_main_window.setWindowTitle = mocker.Mock()
        
        mock_controller = mocker.Mock()
        mock_loader = mocker.Mock()
        mock_controller.file_loader = mock_loader
        mock_main_window.controller = mock_controller

        manager = TitleManager(mock_main_window, mock_app_state)
        mock_app_state.is_content_loaded = True
        mock_app_state.current_file_path = "C:/Images"
        mock_app_state.image_files = ["img1.jpg", "img2.jpg"]
        
        # Removed os.path.basename patch
        
        manager.update_window_title([0])
        mock_main_window.setWindowTitle.assert_called_with("Images - img1.jpg")
