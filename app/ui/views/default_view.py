from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene, QGraphicsPixmapItem, QFrame
from PySide6.QtGui import QKeyEvent, QPainter, QTransform, QImage, QPixmap
from PySide6.QtCore import Signal, Qt, QPoint, QRectF

if TYPE_CHECKING:
    from app.ui.main_window import MainWindow
    from app.core.app_controller import ApplicationController

class DefaultGraphicsView(QGraphicsView):
    """PySide6の標準レンダリングを使用するビュー。"""
    keyPressed = Signal(QKeyEvent)
    wheelScrolled = Signal(int)
    view_initialized = Signal()

    def __init__(self, controller: ApplicationController, parent: 'MainWindow' | None = None):
        super().__init__(parent)
        self.controller = controller
        self.app_state = controller.app_state
        self.settings_manager = controller.settings_manager
        
        self.scene = QGraphicsScene(self)
        self.setScene(self.scene)
        self.pixmap_items = []

        # ルーペ機能用の状態変数
        self.loupe_active = False
        self.panning_active = False
        self.original_transform = QTransform()
        self.last_pan_pos = QPoint()

        self.setAcceptDrops(True) # ドラッグ&ドロップを有効にする
        self.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.view_initialized.emit()

    def sizeHint(self):
        """ウィンドウの自動リサイズを防ぐため、推奨サイズとして親のサイズを返す"""
        from PySide6.QtCore import QSize
        # 親ウィジェットのサイズを返すことで、レイアウトマネージャーに
        # ウィンドウサイズを変更しないよう指示する
        if self.parentWidget():
            return self.parentWidget().size()
        return QSize(800, 600)  # デフォルトサイズ

    def displayImage(self, images: list[QImage]):
        logging.debug(f"[DEBUG_HAYATE] DefaultGraphicsView.displayImage called with {len(images)} images.")
        self.scene.clear()
        self.pixmap_items.clear()

        if not images:
            self.setSceneRect(QRectF()) # シーンをクリア
            return

        pixmaps = [QPixmap.fromImage(img) for img in images]

        if len(pixmaps) == 1:
            # 単一ページ表示
            pixmap = pixmaps[0]
            item = QGraphicsPixmapItem(pixmap)
            # アイテムの中心がシーンの原点(0,0)に来るようにオフセットを設定
            item.setOffset(-pixmap.width() / 2, -pixmap.height() / 2)
            self.pixmap_items.append(item)
            self.scene.addItem(item)

        elif len(pixmaps) == 2:
            # 見開き表示
            # UIManagerからは [左ページ, 右ページ] の順で渡される
            pixmap_left = pixmaps[0]
            pixmap_right = pixmaps[1]

            item_left = QGraphicsPixmapItem(pixmap_left)
            item_right = QGraphicsPixmapItem(pixmap_right)

            # 垂直方向の中央揃えのために高さを合わせる
            max_height = max(pixmap_left.height(), pixmap_right.height())

            # 見開きの中心(綴じ目)がシーンの原点(0,0)に来るように配置
            # 左ページは原点から左に広がる
            item_left.setOffset(-pixmap_left.width(), -(max_height / 2) + (max_height - pixmap_left.height()) / 2)
            # 右ページは原点から右に広がる
            item_right.setOffset(0, -(max_height / 2) + (max_height - pixmap_right.height()) / 2)

            # シーンに追加する順序は描画順に影響しないが、可読性のために合わせる
            self.pixmap_items.extend([item_left, item_right])
            self.scene.addItem(item_left)
            self.scene.addItem(item_right)

        self.fit_in_view_properly()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_in_view_properly()

    def fit_in_view_properly(self):
        if not self.pixmap_items:
            return
        
        rect = self.scene.itemsBoundingRect()
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)

    def keyPressEvent(self, event: QKeyEvent):
        # すべてのキーイベントを上位のハンドラに伝達する
        self.keyPressed.emit(event)
        # QGraphicsView のデフォルトのキー処理（矢印キーでのスクロールなど）が
        # 意図しない動作を引き起こす可能性があるため、superの呼び出しは慎重に行う。
        # しかし、他の基本的なキーイベント（修飾キーの状態変化など）のために必要。
        # EventHandler側で適切に処理されることを期待する。
        super().keyPressEvent(event)

    def wheelEvent(self, event):
        event.accept()
        self.wheelScrolled.emit(event.angleDelta().y())

    def dragEnterEvent(self, event):
        # MainWindowのdragEnterEventに処理を委譲するため、イベントを無視する
        event.ignore()

    def dropEvent(self, event):
        # MainWindowのdropEventに処理を委譲するため、イベントを無視する
        event.ignore()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.loupe_active = True
            self.original_transform = self.transform()
            self.last_pan_pos = event.pos()
            
            self.setDragMode(QGraphicsView.DragMode.NoDrag)
            
            # カーソル位置を中心に2倍に拡大
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
            self.scale(2.0, 2.0)
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor)

        elif event.button() == Qt.MouseButton.RightButton:
            is_zoomed = not self.transform().isIdentity()
            if is_zoomed:
                self.panning_active = True
                self.last_pan_pos = event.pos()
                self.setDragMode(QGraphicsView.DragMode.NoDrag)

        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.loupe_active or self.panning_active:
            delta = event.pos() - self.last_pan_pos
            transform = self.transform()
            zoom_x = transform.m11()
            zoom_y = transform.m22()
            
            if zoom_x != 0 and zoom_y != 0:
                self.translate(delta.x() / zoom_x, delta.y() / zoom_y)
                
            self.last_pan_pos = event.pos()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.loupe_active:
            self.loupe_active = False
            self.setTransform(self.original_transform)
            self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
            self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        elif event.button() == Qt.MouseButton.RightButton and self.panning_active:
            self.panning_active = False
            if not self.loupe_active:
                self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)
        super().mouseReleaseEvent(event)
        
    def zoom_in(self):
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.scale(1.15, 1.15)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor) # デフォルトに戻す

    def zoom_out(self):
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.scale(1 / 1.15, 1 / 1.15)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.NoAnchor) # デフォルトに戻す

    def zoom_reset(self):
        self.setTransform(QTransform())
        self.fit_in_view_properly()