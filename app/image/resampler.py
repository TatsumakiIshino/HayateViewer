from abc import ABC, abstractmethod
from PIL import Image
import numpy as np
import cv2
from skimage.transform import resize as sk_resize

# --- リサンプリング処理 ---

class ResamplingStrategy(ABC):
    """リサンプリング戦略のインターフェース。"""
    @abstractmethod
    def resize(self, image: Image.Image, size: tuple[int, int]) -> Image.Image:
        """指定されたサイズに画像をリサイズする。"""
        pass

class PillowResampler(ResamplingStrategy):
    """Pillowを使用したリサンプリング戦略。"""
    def __init__(self, resample_filter: int):
        self.filter = resample_filter

    def resize(self, image: Image.Image, size: tuple[int, int]) -> Image.Image:
        return image.resize(size, self.filter)

class OpenCVResampler(ResamplingStrategy):
    """OpenCVを使用したリサンプリング戦略。"""
    def __init__(self, interpolation: int):
        self.interpolation = interpolation

    def resize(self, image: Image.Image, size: tuple[int, int]) -> Image.Image:
        np_image = np.array(image.convert('RGB'))
        # OpenCVは(width, height)の順でサイズを指定する
        resized_np_image = cv2.resize(np_image, size, interpolation=self.interpolation)
        return Image.fromarray(resized_np_image)

class SkimageResampler(ResamplingStrategy):
    """Scikit-imageを使用したリサンプリング戦略。"""
    def __init__(self, order: int):
        self.order = order

    def resize(self, image: Image.Image, size: tuple[int, int]) -> Image.Image:
        # Scikit-imageは(height, width)の順でサイズを指定する
        h, w = size[1], size[0]
        np_image = np.array(image)
        # RGBAのまま処理し、アンチエイリアシングを有効にする
        resized_np_image = sk_resize(
            np_image,
            (h, w),
            order=self.order,
            preserve_range=True,
            anti_aliasing=True
        )
        # 浮動小数点数からuint8に変換
        return Image.fromarray(resized_np_image.astype(np.uint8))

class ImageResampler:
    """
    リサンプリングモードに応じて適切な戦略を選択し、画像のリサイズを実行するクラス。
    """
    def __init__(self, mode: str):
        self._strategy = self._create_strategy(mode)

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
        return self._strategy.resize(image, size)