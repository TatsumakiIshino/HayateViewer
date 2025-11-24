from unittest.mock import MagicMock
from PySide6.QtWidgets import QApplication
import sys
import os

# Add current directory to path
sys.path.append(os.getcwd())

try:
    from app.ui.views.default_view import DefaultGraphicsView
    
    app = QApplication(sys.argv)
    controller = MagicMock()
    controller.app_state = MagicMock()
    # Mock settings_manager.get to return a default value if needed
    controller.settings_manager.get.return_value = 9 
    
    # parent = MagicMock()
    # parent.size.return_value = QSize(800, 600)
    parent = None

    print("Attempting to instantiate DefaultGraphicsView...")
    view = DefaultGraphicsView(controller, parent)
    print("DefaultGraphicsView instantiated successfully")
    
except Exception as e:
    print(f"Failed to instantiate DefaultGraphicsView: {e}")
    import traceback
    traceback.print_exc()
    sys.exit(1)
