import sys
import os
import pytest
from PySide6.QtWidgets import QApplication, QLabel
from PySide6.QtCore import QSettings

# Add project root to path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from app.core.state import AppState
from app.config.settings import Settings

@pytest.fixture(scope="session")
def qapp():
    """
    Session-scoped fixture to create the QApplication instance.
    pytest-qt requires a QApplication to be running.
    """
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    yield app

@pytest.fixture
def mock_settings(tmp_path):
    """
    Fixture to create a Settings instance with a temporary file.
    """
    settings_file = tmp_path / "test_config.json"
    return Settings(str(settings_file))

@pytest.fixture
def mock_app_state(mock_settings):
    """
    Fixture to create a fresh AppState instance.
    """
    return AppState(mock_settings)

@pytest.fixture
def mock_main_window(qapp, qtbot, mock_settings):
    """
    Fixture to create a mock MainWindow.
    Using a simple QObject or a minimal mock class to avoid full GUI initialization overhead if possible,
    but for managers that interact with UI elements, we might need real widgets.
    Here we mock the necessary attributes.
    """
    class MockMainWindow:
        def __init__(self):
            self.status_bar = MockStatusBar()
            self.page_info_label = QLabel()
            self.view_mode_label = QLabel()
            self.rendering_backend_label = QLabel()
            self.resampling_label = QLabel()
            self.cpu_cache_label = QLabel()
            self.gpu_cache_label = QLabel()
            self.controller = None
            self.view = None
            self.settings_manager = mock_settings
        
        def setWindowTitle(self, title):
            pass
        
        def update_seek_widget_state(self):
            pass
            
        def _on_app_state_page_changed(self, index, is_spread):
            pass

    class MockStatusBar:
        def showMessage(self, message, timeout=0):
            pass

    window = MockMainWindow()
    return window
