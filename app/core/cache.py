import logging
import threading
import time
from collections import OrderedDict
import psutil
from PySide6.QtCore import QObject, Signal

class ImageCache(QObject):
    cache_changed = Signal()
    page_cached = Signal(int)

    def __init__(self, settings_manager):
        super().__init__()
        self.lock = threading.Lock()
        self.dynamic_resizing = settings_manager.get('dynamic_cache_resizing', True)
        self.min_cache_size = settings_manager.get('min_cache_size_mb', 64) * 1024 * 1024
        self.max_cache_size = settings_manager.get('max_cache_size_mb', 1024) * 1024 * 1024
        
        self.cache = OrderedDict()
        self.current_size = 0
        self.max_size = self.max_cache_size

        if self.dynamic_resizing:
            self.adjust_cache_size()

    def get(self, key):
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                logging.info(f"[CACHE_L2_GET] HIT: Key '{key}' found in ImageCache.")
                return self.cache[key]
            logging.info(f"[CACHE_L2_GET] MISS: Key '{key}' not found in ImageCache.")
            return None

    def set(self, key, value):
        with self.lock:
            image_size = len(value.tobytes())
            if image_size > self.max_size:
                logging.warning(f"Image size ({image_size / (1024*1024):.2f}MB) exceeds max cache size ({self.max_size / (1024*1024):.2f}MB). Cannot cache.")
                return

            if key in self.cache:
                old_size = len(self.cache[key].tobytes())
                self.current_size -= old_size
                del self.cache[key]
            
            while self.current_size + image_size > self.max_size:
                oldest_key, oldest_value = self.cache.popitem(last=False)
                oldest_size = len(oldest_value.tobytes())
                self.current_size -= oldest_size

            self.cache[key] = value
            self.current_size += image_size
            self.cache.move_to_end(key)
            logging.info(f"LOG_DEBUG: [Cache] Set page {key} to cache. Current cache size: {len(self.cache)} pages.")
            # page_number は int 型である必要があるため、キーを変換
            try:
                page_number = int(key)
                self.page_cached.emit(page_number)
            except (ValueError, TypeError):
                logging.warning(f"Could not convert cache key '{key}' to int for page_cached signal.")
            self.cache_changed.emit()

    def evict_outside_range(self, start, end):
        """指定された範囲外のキャッシュアイテムを削除する。"""
        with self.lock:
            # キーが整数に変換できるもののみを対象とする
            keys_to_evict = [k for k in self.cache if isinstance(k, int) and not (start <= k < end)]
            if not keys_to_evict:
                return

            logging.info(f"[ImageCache] Evicting {len(keys_to_evict)} pages outside of range {start}-{end-1}.")
            for key in keys_to_evict:
                if key in self.cache:
                    size = len(self.cache[key].tobytes())
                    self.current_size -= size
                    del self.cache[key]
            self.cache_changed.emit()

    def __contains__(self, key):
        with self.lock:
            return key in self.cache

    @property
    def page_count(self):
        with self.lock:
            return len(self.cache)

    def clear(self):
        with self.lock:
            if len(self.cache) > 0:
                self.cache.clear()
                self.current_size = 0
                self.cache_changed.emit()

    def __delitem__(self, key):
        with self.lock:
            if key in self.cache:
                old_size = len(self.cache[key].tobytes())
                self.current_size -= old_size
                del self.cache[key]
                self.cache_changed.emit()

    def set_max_size(self, new_size_mb):
        """キャッシュの最大サイズを動的に設定する。"""
        with self.lock:
            self.max_cache_size = new_size_mb * 1024 * 1024
            # 動的リサイズが無効な場合でも、手動設定されたサイズを max_size に反映する
            if not self.dynamic_resizing:
                self.max_size = self.max_cache_size
            
            # 新しいサイズに合わせてキャッシュを削減
            while self.current_size > self.max_size:
                oldest_key, oldest_value = self.cache.popitem(last=False)
                self.current_size -= len(oldest_value.tobytes())

    def adjust_cache_size(self):
        if not self.dynamic_resizing:
            return

        with self.lock:
            available_memory = psutil.virtual_memory().available
            
            # 空きメモリの50%をターゲットとする
            target_size = int(available_memory * 0.5)
            
            # 最小・最大サイズの範囲内に収める
            new_max_size = max(self.min_cache_size, min(target_size, self.max_cache_size))

            self.max_size = new_max_size
            
            # 新しいサイズに合わせてキャッシュを削減
            while self.current_size > self.max_size:
                oldest_key, oldest_value = self.cache.popitem(last=False)
                self.current_size -= len(oldest_value.tobytes())

class TextureCache(QObject):
   """OpenGLテクスチャを管理するためのLRUキャッシュ。"""
   cache_changed = Signal()

   def __init__(self, max_size, app_state):
       super().__init__()
       self.lock = threading.Lock()
       self.app_state = app_state
       self.cache = OrderedDict()
       self.max_size = max_size
       self.deleted_textures = []
       self.pinned_keys = set()

   def pin(self, key):
       """指定されたキーをピン留めして、キャッシュから削除されないようにする。"""
       with self.lock:
           if key in self.cache:
               self.pinned_keys.add(key)
               logging.debug(f"[TextureCache] Pinned page {key}")

   def unpin(self, key):
       """指定されたキーのピン留めを解除する。"""
       with self.lock:
           self.pinned_keys.discard(key)
           logging.debug(f"[TextureCache] Unpinned page {key}")

   def unpin_all(self):
       """すべてのキーのピン留めを解除する。"""
       with self.lock:
           if self.pinned_keys:
               logging.debug(f"[TextureCache] Unpinning all {len(self.pinned_keys)} keys.")
               self.pinned_keys.clear()

   def get(self, key):
       with self.lock:
           if key in self.cache:
               self.cache.move_to_end(key)
               return self.cache[key]
           return None

   def set(self, key, value):
       with self.lock:
           logging.debug(f"[TextureCache.set] Attempting to set key: {key}")
           if key in self.cache:
               # 既存のキーの場合は更新するだけ
               self.cache[key] = value
               self.cache.move_to_end(key)
               logging.debug(f"[TextureCache.set] Updated existing key: {key}")
               return

           self.cache[key] = value
           self._evict_if_needed()
           self.cache_changed.emit()
           logging.debug(f"[TextureCache.set] Successfully set new key: {key}. Cache size: {len(self.cache)}")

   def evict_outside_range(self, start, end):
       """指定された範囲外のキャッシュアイテムを削除する。"""
       with self.lock:
           # ピン留めされていない、かつ範囲外のキーを削除対象とする
           # キーは 'path::index' 形式なので、index部分を抽出して比較する
           keys_to_evict = []
           for k in self.cache:
               if k in self.pinned_keys:
                   continue
               try:
                   # キーからページインデックスを抽出
                   page_index = int(str(k).split('::')[-1])
                   if not (start <= page_index < end):
                       keys_to_evict.append(k)
               except (ValueError, IndexError):
                   # 'path::index' 形式でないキーは、特定のページ範囲に属さないため、
                   # 常に範囲外とみなし、破棄対象とする。
                   keys_to_evict.append(k)

           if not keys_to_evict:
               return

           logging.info(f"[TextureCache] Evicting {len(keys_to_evict)} textures outside of range {start}-{end-1}.")
           for key in keys_to_evict:
               if key in self.cache:
                   value_to_evict = self.cache.pop(key)
                   if value_to_evict and 'texture_id' in value_to_evict:
                       self.deleted_textures.append(value_to_evict['texture_id'])
           self.cache_changed.emit()

   def _evict_if_needed(self):
       """キャッシュサイズが上限を超えている場合、現在ページから最も遠いアイテムを削除する。"""
       logging.debug(f"[_evict_if_needed] Start. Cache size: {len(self.cache)}, Max size: {self.max_size}")
       while len(self.cache) > self.max_size:
           logging.debug(f"[_evict_if_needed] Loop. Cache size: {len(self.cache)}, Pinned keys: {self.pinned_keys}")
           unpinned_keys = [k for k in self.cache if k not in self.pinned_keys]

           if not unpinned_keys:
               logging.warning("[TextureCache] Cannot evict item, all cached items are pinned.")
               break

           current_page = self.app_state.current_page_index
           logging.debug(f"[_evict_if_needed] Current page: {current_page}, Unpinned keys: {unpinned_keys}")

           farthest_key = None
           try:
               # キーからインデックスを抽出し、距離を計算
               def get_distance(k):
                   try:
                       # キーは 'path::index' 形式
                       page_index = int(str(k).split('::')[-1])
                       return abs(page_index - current_page)
                   except (ValueError, IndexError):
                       # 形式が違う場合は大きな値を返し、優先度を下げる
                       return float('inf')
               
               # 距離が無限大でないキーのみを対象とする
               valid_keys = [k for k in unpinned_keys if get_distance(k) != float('inf')]
               if valid_keys:
                    farthest_key = max(valid_keys, key=get_distance)
               else:
                    # 有効なキーがない場合、単純なLRU
                    farthest_key = unpinned_keys[0]

           except Exception as e:
               logging.error(f"[_evict_if_needed] Error finding farthest key: {e}", exc_info=True)
               # エラー時は単純なLRUで代替
               farthest_key = unpinned_keys[0] if unpinned_keys else None

           logging.debug(f"[_evict_if_needed] Farthest key to evict: {farthest_key}")
           if farthest_key:
               value_to_evict = self.cache.pop(farthest_key)
               logging.info(f"[TextureCache] Evicting page {farthest_key} (distance from {current_page}).")
               if value_to_evict and 'texture_id' in value_to_evict:
                   self.deleted_textures.append(value_to_evict['texture_id'])
           else:
               logging.warning("[_evict_if_needed] No key to evict was found. Breaking loop.")
               break
       logging.debug(f"[_evict_if_needed] End. Cache size: {len(self.cache)}")

   def set_max_size(self, new_size):
       """キャッシュの最大サイズ（アイテム数）を動的に設定する。"""
       with self.lock:
           self.max_size = new_size
           self._evict_if_needed()

   def get_max_size(self):
       """キャッシュの最大サイズ（アイテム数）を取得する。"""
       with self.lock:
           return self.max_size

   def get_deleted_textures(self):
       """削除対象のテクスチャIDリストを取得し、リストをクリアする。"""
       with self.lock:
           deleted = self.deleted_textures
           self.deleted_textures = []
           return deleted

   def clear(self):
       with self.lock:
           if len(self.cache) > 0:
               # すべてのテクスチャを削除対象とする
               for value in self.cache.values():
                   if value and 'texture_id' in value:
                       self.deleted_textures.append(value['texture_id'])
               logging.info(f"[TextureCache] Cleared. {len(self.deleted_textures)} textures queued for deletion.")
               self.cache.clear()
               self.pinned_keys.clear()
               self.cache_changed.emit()

   def __contains__(self, key):
       with self.lock:
           return key in self.cache

   @property
   def page_count(self):
       with self.lock:
           return len(self.cache)
