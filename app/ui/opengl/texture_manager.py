from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from PySide6.QtGui import QImage
from OpenGL.GL import *

from app.core.cache import TextureCache

if TYPE_CHECKING:
    from app.core.state import AppState

class TextureManager:
    """OpenGLテクスチャの管理（生成、削除、キャッシュ）を担当するクラス。"""

    def __init__(self, app_state: AppState, max_cache_size: int = 20):
        self.app_state = app_state
        self.texture_cache = TextureCache(max_size=max_cache_size, app_state=app_state)
        self.prepare_queue = {}

    def set_max_cache_size(self, size: int):
        self.texture_cache.set_max_size(size)

    def get_texture(self, key: str):
        return self.texture_cache.get(key)

    def pin_keys(self, keys: list[str]):
        """指定されたキーをピン留めし、それ以外をピン留め解除する。"""
        # 現在のピン留め状況を確認し、不要なものを解除
        # TextureCacheの実装に依存するが、ここでは単純に新しいキーをピン留めし、
        # 古いキー（display_keysに含まれていないもの）はunpinされるべきだが、
        # TextureCache.pin/unpinの仕様に合わせて呼び出す必要がある。
        # 元のコードでは:
        # for old_key in self.display_keys: self.texture_cache.unpin(old_key)
        # self.display_keys = keys
        # for key in self.display_keys: self.texture_cache.pin(key)
        # となっていた。
        pass # 上位層（OpenGLView）で管理するか、ここで管理するか。
             # ここで管理するなら、前回のkeysを覚えておく必要がある。

    def prepare_texture(self, key: str, qimage: QImage):
        """テクスチャ生成をキューに入れるか、即座に生成する（メインスレッド想定）。"""
        if key in self.texture_cache or key in self.prepare_queue:
            return

        if not qimage or qimage.isNull():
            logging.warning(f"Cannot prepare texture for key '{key}', QImage is null.")
            return

        # paintGLで処理するためにキューに入れる
        self.prepare_queue[key] = qimage

    def process_prepare_queue(self, settings_manager):
        """キューにあるテクスチャ生成リクエストを処理する。"""
        if not self.prepare_queue:
            return

        queue_to_process = self.prepare_queue.copy()
        self.prepare_queue.clear()
        
        for key, qimage in queue_to_process.items():
            self._create_texture(key, qimage, settings_manager)

    def _create_texture(self, key: str, image: QImage, settings_manager):
        """実際にOpenGLテクスチャを生成する。"""
        if key in self.texture_cache:
            return

        try:
            texture_id = glGenTextures(1)
            glBindTexture(GL_TEXTURE_2D, texture_id)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE)
            
            resampling_mode = settings_manager.get('resampling_mode_gl', 'GL_LANCZOS3')
            texture_filter = GL_NEAREST if resampling_mode == 'GL_NEAREST' else GL_LINEAR
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, texture_filter)
            glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, texture_filter)
            
            image = image.convertToFormat(QImage.Format.Format_RGBA8888)
            mv = memoryview(image.bits())
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGBA, image.width(), image.height(), 0, GL_RGBA, GL_UNSIGNED_BYTE, mv)
            
            self.texture_cache.set(key, {
                'texture_id': texture_id,
                'width': image.width(),
                'height': image.height()
            })
            glBindTexture(GL_TEXTURE_2D, 0)
        except Exception as e:
            logging.error(f"Error preparing texture for key {key}: {e}", exc_info=True)

    def delete_pending_textures(self):
        """削除待ちのテクスチャを削除する。"""
        deleted_ids = self.texture_cache.get_deleted_textures()
        if deleted_ids:
            glDeleteTextures(deleted_ids)

    def clear(self):
        self.texture_cache.clear()
        self.delete_pending_textures()
