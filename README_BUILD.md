# HayateViewer ビルド手順

このドキュメントでは、HayateViewerの各プラットフォーム向けバイナリをビルドする方法を説明します。

## 前提条件

- Python 3.13
- pip
- PyInstaller

## ローカルビルド

### macOS

```bash
# 依存関係のインストール
python -m pip install --upgrade pip
pip install -r requirements.txt

# .appバンドルのビルド
pyinstaller HayateViewer-macOS.spec --clean --noconfirm

# ビルド結果
# dist/HayateViewer.app が作成されます
```

#### DMGの作成（オプション）

```bash
# create-dmgのインストール
brew install create-dmg

# DMGの作成
create-dmg \
  --volname "HayateViewer" \
  --window-pos 200 120 \
  --window-size 600 400 \
  --icon-size 100 \
  --icon "HayateViewer.app" 175 120 \
  --hide-extension "HayateViewer.app" \
  --app-drop-link 425 120 \
  "dist/HayateViewer-macOS.dmg" \
  "dist/HayateViewer.app"
```

### Windows

```bash
# 依存関係のインストール
python -m pip install --upgrade pip
pip install -r requirements.txt

# 実行ファイルのビルド
pyinstaller main.py --onefile --noconsole --name HayateViewer --add-data "app/shaders;app/shaders"

# ビルド結果
# dist/HayateViewer.exe が作成されます
```

## GitHub Actionsによる自動ビルド

### 使い方

1. **mainブランチにプッシュ**
   ```bash
   git add .
   git commit -m "Update"
   git push origin main
   ```
   → 自動的にビルドが開始されます

2. **手動でビルド**
   - GitHubリポジトリのActionsタブを開く
   - "Build with PyInstaller"ワークフローを選択
   - "Run workflow"をクリック

3. **ビルド成果物のダウンロード**
   - Actionsタブでビルドが完了したワークフローを開く
   - "Artifacts"セクションからダウンロード:
     - `HayateViewer-Windows`: Windows用.exe
     - `HayateViewer-macOS`: macOS用.dmg

### リリースの作成

タグをプッシュすると、自動的にGitHub Releaseが作成されます：

```bash
# バージョンタグの作成
git tag v1.0.0
git push origin v1.0.0
```

→ GitHub Releasesページに自動的にバイナリが添付されます

## トラブルシューティング

### macOS: "dylib not found"エラー

依存ライブラリが見つからない場合は、`HayateViewer-macOS.spec`の`hiddenimports`に追加してください：

```python
hiddenimports = [
    'PySide6.QtOpenGLWidgets',
    'PyOpen GL',
    # ここに追加
]
```

### Windows: "DLL load failed"エラー

Windows固有のDLLが必要な場合は、`binaries`に追加してください。

### ビルドログの確認

GitHub Actionsでビルドが失敗した場合：

1. Actionsタブでワークフローを開く
2. 失敗したジョブをクリック
3. 各ステップのログを確認

## .appバンドルの詳細

### 構造

```
HayateViewer.app/
├── Contents/
│   ├── Info.plist          # アプリケーション情報
│   ├── MacOS/
│   │   └── HayateViewer    # 実行ファイル
│   ├── Resources/          # リソースファイル
│   └── Frameworks/         # 依存ライブラリ
```

### Info.plistのカスタマイズ

`HayateViewer-macOS.spec`の`info_plist`セクションを編集してください：

```python
info_plist={
    'CFBundleName': 'HayateViewer',
    'CFBundleVersion': '1.0.0',
    'LSMinimumSystemVersion': '10.13.0',
    # その他の設定...
}
```

## 参考リンク

- [PyInstaller Documentation](https://pyinstaller.org/en/stable/)
- [GitHub Actions Documentation](https://docs.github.com/ja/actions)
- [create-dmg](https://github.com/create-dmg/create-dmg)
