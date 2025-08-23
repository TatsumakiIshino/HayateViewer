import logging
from PySide6.QtWidgets import QWidget, QVBoxLayout
from PySide6.QtGui import QKeyEvent
from PySide6.QtCore import Signal

from .default_view import DefaultGraphicsView
from .opengl_view import OpenGLView

class ImageViewer(QWidget):
    """
    Viewをホストするためのコンテナウィジェット。
    """
    keyPressed = Signal(QKeyEvent)
    wheelScrolled = Signal(int)

    def __init__(self, view, parent=None):
        super().__init__(parent)
        self.current_view = view

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        self.setLayout(self.layout)
        
        self.current_view.keyPressed.connect(self.keyPressed)
        self.current_view.wheelScrolled.connect(self.wheelScrolled)
        self.layout.addWidget(self.current_view)

    def displayImage(self, image_keys: list, images: list, is_spread: bool):
        if self.current_view:
            if isinstance(self.current_view, OpenGLView):
                self.current_view.displayImage(image_keys, images)
            elif isinstance(self.current_view, DefaultGraphicsView):
                self.current_view.displayImage(images, is_spread)
            else:
                logging.warning(f"Unknown view type: {type(self.current_view)}")