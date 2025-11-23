import pytest
import os
from app.core.services.loader_service import LoaderService

class TestLoaderService:
    def test_load_path_success(self, mock_main_window, mock_app_state, mocker):
        # Mock controller
        mock_controller = mocker.Mock()
        mock_controller.app_state = mock_app_state
        mock_controller.file_loader = None
        mock_controller._is_loading = False
        mock_controller.ui_manager = mocker.Mock()
        mock_controller.image_cache = mocker.Mock()
        mock_controller.thread_manager = mocker.Mock()
        
        service = LoaderService(mock_controller)
        
        # Mock FileLoader
        mock_loader_cls = mocker.patch('app.core.services.loader_service.FileLoader')
        mock_loader_instance = mock_loader_cls.return_value
        mock_loader_instance.get_image_list.return_value = ["img1.jpg", "img2.jpg"]
        mock_loader_instance.load_type = 'folder'
        
        # Execute
        service.load_path("dummy/path")
        
        # Verify
        assert mock_controller.app_state.current_file_path == "dummy/path"
        assert len(mock_controller.app_state.image_files) == 2
        mock_controller.thread_manager.file_loader_updated.emit.assert_called()

    def test_load_path_not_found(self, mock_main_window, mock_app_state, mocker):
        mock_controller = mocker.Mock()
        mock_controller.app_state = mock_app_state
        mock_controller._is_loading = False
        mock_controller.ui_manager = mocker.Mock()
        mock_controller.image_cache = mocker.Mock()
        
        service = LoaderService(mock_controller)
        
        # Mock FileLoader to raise FileNotFoundError
        mocker.patch('app.core.services.loader_service.FileLoader', side_effect=FileNotFoundError)
        
        service.load_path("invalid/path")
        
        mock_controller.ui_manager.show_error_dialog.assert_called()
