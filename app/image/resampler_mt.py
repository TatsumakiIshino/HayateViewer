from PySide6.QtCore import QRunnable, QThreadPool
from PIL import Image
import numpy as np
import cv2
from skimage.transform import resize as sk_resize

from app.image.resampler import ResamplingStrategy, PillowResampler, OpenCVResampler, SkimageResampler

class ResampleTileWorker(QRunnable):
    """
    画像タイルをリサンプリングするQRunnableワーカ。
    """
    def __init__(self, strategy: ResamplingStrategy, tile: Image.Image, target_size: tuple[int, int]):
        super().__init__()
        self.strategy = strategy
        self.tile = tile
        self.target_size = target_size
        self.result = None

    def run(self):
        # 既存の戦略オブジェクトを使ってタイルをリサイズ
        self.result = self.strategy.resize(self.tile, self.target_size)


class MultiThreadedImageResampler:
    """
    タイルベースの並列処理で画像のリサイズを実行するクラス。
    """
    def __init__(self, mode: str, max_threads: int):
        self.mode = mode
        self._strategy = self._create_strategy(mode)
        self.thread_pool = QThreadPool()
        self.thread_pool.setMaxThreadCount(max_threads)

    def _create_strategy(self, mode: str) -> ResamplingStrategy:
        if mode == "PIL_NEAREST":
            return PillowResampler(Image.Resampling.NEAREST)
        elif mode == "PIL_BILINEAR":
            return PillowResampler(Image.Resampling.BILINEAR)
        elif mode == "PIL_BICUBIC":
            return PillowResampler(Image.Resampling.BICUBIC)
        elif mode == "PIL_LANCZOS":
            return PillowResampler(Image.Resampling.LANCZOS)
        elif mode == "CV2_INTER_AREA":
            return OpenCVResampler(cv2.INTER_AREA)
        elif mode == "CV2_INTER_CUBIC":
            return OpenCVResampler(cv2.INTER_CUBIC)
        elif mode == "CV2_INTER_LANCZOS4":
            return OpenCVResampler(cv2.INTER_LANCZOS4)
        elif mode == "SKIMAGE_ORDER_4":
            return SkimageResampler(order=4)
        elif mode == "SKIMAGE_ORDER_5":
            return SkimageResampler(order=5)
        else:
            # デフォルトはPillowのBilinear
            return PillowResampler(Image.Resampling.BILINEAR)

    def resize(self, image: Image.Image, size: tuple[int, int]) -> Image.Image:
        # 1. 画像をタイルに分割
        num_tiles = self.thread_pool.maxThreadCount()
        w, h = image.size
        tile_h = h // num_tiles
        tiles = []
        for i in range(num_tiles):
            box = (0, i * tile_h, w, (i + 1) * tile_h if i < num_tiles - 1 else h)
            tiles.append(image.crop(box))

        # 2. 各タイルのリサイズ後のサイズを計算
        target_w, target_h = size
        target_tile_h = target_h // num_tiles
        
        # 3. ワーカを作成し、スレッドプールに投入
        workers = []
        for i, tile in enumerate(tiles):
            # 最後のタイルは残りの高さをすべて使う
            if i == num_tiles - 1:
                target_tile_size = (target_w, target_h - (i * target_tile_h))
            else:
                target_tile_size = (target_w, target_tile_h)

            worker = ResampleTileWorker(self._strategy, tile, target_tile_size)
            workers.append(worker)
            self.thread_pool.start(worker)

        # 4. 全てのワーカの完了を待つ
        self.thread_pool.waitForDone()

        # 5. 処理済みのタイルを結合
        # モードに応じて適切なカラーモードで新しい画像を作成
        result_image = Image.new(image.mode, size)
        current_h = 0
        for worker in workers:
            if worker.result:
                result_image.paste(worker.result, (0, current_h))
                current_h += worker.result.height
            
        return result_image