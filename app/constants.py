# --- 定数定義 ---

# 対応する画像形式
SUPPORTED_FORMATS = {'.jpg', '.jpeg', '.png', '.bmp', '.webp', '.avif', '.jp2', '.j2k'}

# 対応する書庫形式
SUPPORTED_ARCHIVE_FORMATS = {'.zip', '.cbz', '.7z', '.cb7', '.rar', '.cbr'}

# リサンプリング方式 (CPU / PyQtバックエンド用)
RESAMPLING_MODES_CPU = {
    # Pillow
    "PIL_NEAREST": "Pillow: Nearest Neighbor",
    "PIL_BILINEAR": "Pillow: Bilinear",
    "PIL_BICUBIC": "Pillow: Bicubic",
    "PIL_LANCZOS": "Pillow: Lanczos",
    # OpenCV
    "CV2_INTER_AREA": "OpenCV: Area (縮小向け)",
    "CV2_INTER_CUBIC": "OpenCV: Bicubic",
    "CV2_INTER_LANCZOS4": "OpenCV: Lanczos4",
    # Scikit-image
    "SKIMAGE_ORDER_4": "Scikit-image: Quartic (高画質)",
    "SKIMAGE_ORDER_5": "Scikit-image: Quintic (最高画質)",
}

# リサンプリング方式 (GPU / OpenGLバックエンド用)
RESAMPLING_MODES_GL = {
    'GL_NEAREST': "Nearest (Shader)",
    'GL_BILINEAR': "Bilinear (Shader)",
    'GL_LANCZOS3': "Lanczos3 (Shader)",
    'GL_LANCZOS4': "Lanczos4 (Shader)",
    'GL_QUINTIC': "Scikit-image: Quintic (Shader)",
}

# アプリケーション再起動用の終了コード
RESTART_CODE = 1000


# 読み込み優先度
PRIORITY_DISPLAY = 1
PRIORITY_PREFETCH = 10

# アプリケーションバージョン
APP_VERSION = "0.7.0"
