import sys
import multiprocessing
import logging
import os
import platform

# --- Set unrar.dll path before any other imports ---
# This needs to be done before the `unrar` library is imported by any module.
if platform.system() == 'Windows':
    if getattr(sys, 'frozen', False):
        base_path = getattr(sys, '_MEIPASS', os.path.abspath(os.path.dirname(__file__)))
    else:
        base_path = os.path.abspath(os.path.dirname(__file__))
    
    lib_path = os.path.join(base_path, 'unrar.dll')
    
    if os.path.exists(lib_path):
        os.environ['UNRAR_LIB_PATH'] = lib_path

from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QTimer

from app.config.settings import Settings
from app.core.state import AppState
from app.core.app_controller import ApplicationController
from app.constants import RESTART_CODE


def main():
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    app = QApplication(sys.argv)

    settings_manager = Settings()
    app_state = AppState(settings_manager)

    # ApplicationControllerがアプリケーションのライフサイクルを管理する
    app_controller = ApplicationController(app_state, settings_manager)
    app.controller = app_controller  # QApplicationに所有権を移譲
    app_controller.start()

    path_to_load = None
    page_to_load = 0

    if len(sys.argv) > 1:
        path_to_load = sys.argv[1]
    if len(sys.argv) > 2:
        try:
            page_to_load = int(sys.argv[2])
        except ValueError:
            page_to_load = 0

    if path_to_load:
        # Controller経由でパスをロードする
        QTimer.singleShot(0, lambda p=path_to_load, page=page_to_load: app_controller.load_path(p, page))

    exit_code = RESTART_CODE  # デフォルト値を設定
    try:
        exit_code = app.exec()
    finally:
        # アプリケーション終了前にクリーンアップ処理を呼び出す
        app_controller.cleanup()

    if exit_code == RESTART_CODE:
        # 新しいプロセスでアプリケーションを再起動
        # sys.argv[0] はスクリプト名 (main.py)
        args = [sys.executable, sys.argv[0]]
        if hasattr(app, 'restart_args'):
            # restart_args にはファイルパスとページ番号が設定されている想定
            args.extend(app.restart_args)

        os.execl(sys.executable, *args)
    else:
        sys.exit(exit_code)

if __name__ == '__main__':
    # Windowsでmultiprocessingを使用する場合、'spawn'がデフォルトのため、
    # 子プロセスで不要なモジュールがインポートされないように、
    # アプリケーションのエントリポイントをこのブロック内に記述する必要がある。
    multiprocessing.freeze_support()
    main()