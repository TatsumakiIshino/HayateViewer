# HayateViewer 技術仕様書

## 1. 概要

`HayateViewer`は、PythonとPySide6 (Qt) を基盤とする、高機能かつ高性能な画像ビューアアプリケーションです。特に、漫画やイラストの閲覧体験を向上させることに重点を置いており、見開き表示、主要な書庫フォーマット（ZIP, RAR, 7z）の直接読み込み、GPUアクセラレーションを活用した高品質な画像リサンプリングといった高度な機能を提供します。

アーキテクチャは、UIロジックとコアビジネスロジックを明確に分離したコンポーネントベース設計を採用しています。マルチスレッド処理と2層キャッシュシステム（CPU/GPU）を積極的に利用することで、UIの応答性を維持しつつ、巨大な画像ファイルや書庫ファイルでもスムーズなブラウジング体験を実現します。

## 2. 機能一覧

*   **画像表示:**
    *   単一ページ表示
    *   見開き表示（左右綴じ対応）
    *   フルスクリーン表示
    *   ズーム、パン操作
*   **対応フォーマット:**
    *   一般画像ファイル (JPEG, PNG, BMP, etc.)
    *   書庫ファイル (ZIP, RAR, 7z) の直接読み込み
*   **画像リサンプリング:**
    *   **CPUベース:** Pillow, OpenCV, scikit-imageを利用した複数のアルゴリズム（マルチスレッド対応）
    *   **GPUベース (OpenGL):** GLSLシェーダーによるリアルタイム・高品質リサンプリング
        *   Nearest Neighbor
        *   Bilinear
        *   Lanczos-3
        *   Quintic
*   **パフォーマンス:**
    *   CPU (L2) と GPU (L1) の2層キャッシュシステムによる高速な画像再表示
    *   先読み (Prefetching) 機構によるスムーズなページめくり
    *   マルチスレッドによるファイルI/O、画像デコード、書庫展開の並列処理
*   **UI/UX:**
    *   キーボードショートカットによる快適な操作
    *   ステータスバー、シークバーによる状態表示とナビゲーション
    *   設定ダイアログによるカスタマイズ
    *   ページジャンプ機能

## 3. システムアーキテクチャ

`HayateViewer`は、責務の分離を重視したコンポーネントベースのアーキテクチャを採用しています。UI、コアロジック、画像処理、I/Oがそれぞれ独立したコンポーネントとして設計されており、`ApplicationController`がこれらを統括します。

*   **UI層 (`app/ui/`):** ユーザーからの入力を受け付け、アプリケーションの状態を画面に表示します。`AppState`の変更を監視し、必要に応じてビューを更新します。
*   **コア層 (`app/core/`):** アプリケーション全体の状態 (`AppState`) を管理し、ビジネスロジックを実行します。UIとバックグラウンド処理の中継役を担います。
*   **バックグラウンド処理層:** `ThreadManager`によって管理される複数のスレッドで構成されます。
    *   **I/O処理 (`app/io/`):** ファイルの読み込みや書庫の展開など、時間のかかるI/O処理を非同期で実行します。
    *   **画像処理 (`app/image/`):** CPUベースの画像デコードやリサンプリングを並列処理します。
    *   **先読み処理 (`app/core/`):** ユーザーの操作を予測し、画像を先読みしてキャッシュに格納します。
*   **描画層:**
    *   **CPU描画 (`app/ui/views/DefaultGraphicsView`):** 標準的なCPUベースの描画を行います。
    *   **GPU描画 (`app/ui/views/OpenGLView`):** OpenGLとGLSLシェーダーを利用して、GPUアクセラレーションによる高速かつ高品質な描画を行います。

`ApplicationController`が中心に位置し、`UIManager` (UI)、`AppState` (状態)、`ThreadManager` (スレッド) を調整します。ユーザー操作は`MainWindow`から`ApplicationController`に伝わり、状態が更新されます。`UIManager`は`AppState`の変更を検知してUIを更新し、`ThreadManager`は`ImageLoaderWorker`や`PrefetcherWorker`を起動してバックグラウンドでデータを準備します。準備されたデータは`ImageCache`や`TextureCache`に格納され、`OpenGLView`または`DefaultGraphicsView`によって画面に描画されます。

## 4. コンポーネント詳細

### 4.1. Core (`app/core/`)

アプリケーションの中核機能と状態管理を担当します。

*   **`ApplicationController`:** アプリケーション全体のライフサイクルと主要コンポーネント間の連携を管理する司令塔。
*   **`AppState`:** アプリケーションの現在状態（表示中のファイル、ページ番号、表示モードなど）を一元管理するデータクラス。
*   **`ThreadManager`:** `QThreadPool`をラップし、アプリケーション内のすべてのワーカースレッドを一元管理します。UIの応答性を確保するために、スレッドの生成、監視、終了を制御します。
*   **`ImageCache` / `TextureCache`:** 2層キャッシュシステム。
    *   `ImageCache` (L2): デコード済みの画像データ (`QImage`) をCPUメモリ上に保持します。
    *   `TextureCache` (L1): `OpenGLView`で使用するGPUテクスチャをVRAM上に保持します。
*   **`PrefetcherWorker`:** ユーザーの閲覧パターン（次のページ、前のページ）を予測し、`ImageLoaderWorker`を介して画像データを非同期に先読みし、キャッシュに格納するワーカースレッド。

### 4.2. UI (`app/ui/`)

ユーザーインターフェースとユーザーインタラクションを担当します。

*   **`MainWindow`:** アプリケーションのメインウィンドウ。メニューバー、ツールバー、ステータスバー、シークバーなどの主要なUI要素を配置します。
*   **`UIManager`:** `AppState`の変更をシグナルとして受け取り、UI要素（ステータスバーのテキスト、ウィンドウタイトル、シークバーの位置など）を更新するロジックを統括します。
*   **Views:**
    *   `DefaultGraphicsView`: `QGraphicsView`をベースとしたCPU描画ビュー。
    *   `OpenGLView`: `QOpenGLWidget`をベースとしたGPU描画ビュー。GLSLシェーダーを動的にロードし、高品質なリサンプリングをリアルタイムに実行します。
*   **`Dialogs`:** 設定変更、ページジャンプ、情報表示などのための各種ダイアログウィンドウを提供します。

### 4.3. Image (`app/image/`)

CPUベースの画像処理、特にリサンプリングを担当します。

*   **`resampler.py`:** ストラテジーパターンを実装。Pillow, OpenCV, scikit-imageの各ライブラリが提供するリサンプリングアルゴリズムを統一されたインターフェースで呼び出せるようにカプセル化します。
*   **`resampler_mt.py`:** 画像をタイル状に分割し、`QThreadPool`を利用して各タイルのリサンプリング処理を並列実行します。これにより、マルチコアCPUの性能を最大限に引き出します。

### 4.4. IO (`app/io/`)

ファイルシステムへのアクセスと書庫ファイルの操作を担当します。

*   **`FileLoader`:** 指定されたパスがファイルか、フォルダか、書庫かを判別し、適切なリーダーを呼び出して画像ファイルリストを生成します。
*   **`archive.py`:**
    *   `IArchiveReader`: 書庫リーダーの共通インターフェースを定義。
    *   `ZipReader`, `RarReader`, `SevenZipReader`: 各書庫フォーマットに対応した具象クラス。`zipfile`, `unrar`, `py7zr`ライブラリを内部で使用します。
*   **`ImageLoaderWorker`:** `QRunnable`を継承したワーカークラス。I/Oバウンドなファイル読み込みとCPUバウンドな画像デコードを非同期で実行します。
*   **`ExtractionThread`:** 大容量の書庫ファイルをバックグラウンドで展開するための専用スレッド。ユーザーが閲覧中のページ周辺を優先的に展開することで、巨大な書庫でも待たずに閲覧を開始できます。

### 4.5. Shaders (`app/shaders/`)

`OpenGLView`で使用されるGLSL (OpenGL Shading Language) シェーダーファイル群。

*   **`vertex_shader.glsl`:** すべてのフラグメントシェーダーで共通して使用される頂点シェーダー。頂点座標の変換（ズーム、パン）やテクスチャ座標の計算を行います。
*   **フラグメントシェーダー群 (`*_fragment.glsl`):**
    *   `nearest_fragment.glsl`: 最近傍法 (Nearest Neighbor)。高速だがエイリアシングが目立つ。
    *   `bilinear_fragment.glsl`: 線形補間 (Bilinear)。中程度の品質と速度。
    *   `lanczos3_fragment.glsl`: Lanczos-3アルゴリズム。高品質だが高負荷。
    *   `quintic_fragment.glsl`: 5次補間。非常に高品質で、特にイラストの拡大に適している。

## 5. 設定項目一覧

アプリケーションの挙動は `config.json` ファイルによって制御されます。以下は主要な設定項目です。

*   **`view_mode`**: "single" | "double" - 表示モード（単一ページ／見開き）
*   **`page_direction`**: "ltr" | "rtl" - ページ進行方向（左から右／右から左）
*   **`resampling_algorithm_cpu`**: "nearest" | "bilinear" | "bicubic" | ... - CPUリサンプリング時に使用するアルゴリズム
*   **`resampling_algorithm_gpu`**: "nearest" | "bilinear" | "lanczos3" | "quintic" - GPUリサンプリング時に使用するシェーダー
*   **`use_gpu_acceleration`**: true | false - GPUアクセラレーションの有効／無効
*   **`cache_size_l2_mb`**: number - CPUキャッシュ（L2）の最大サイズ（MB）
*   **`prefetch_count`**: number - 先読みするページ数
*   **`window_size`**: [width, height] - ウィンドウのデフォルトサイズ