# Rust移植版

汎用CPU実装から始めるaXvの新しい実装です。現在のPython版は比較資料として残し、Rust版は`src/`以下に分離しています。

## 現在実装されている範囲

- フォルダ、単体画像、ZIP、外部`unrar.exe`によるRAR読込
- PNG、JPEG、WebP、BMP、GIFの一括デコードと自然順ソート
- `wgpu`を利用した画像表示
- F11全画面表示。背景と未使用領域を黒で描画
- AI差分処理のチェックボックスと差分矩形検出
- `M`キーで、現在ページの3枚前より古いデコード画像をRAMから破棄
- 新しい入力を開いた時の旧ライブラリ・GPUテクスチャ破棄

破棄するのはデコード済みRGBAだけです。元ファイルと再デコード用の圧縮データは削除しません。

## ビルド

1. [rustup](https://rustup.rs/)からRustをインストールします。
2. Visual Studio Installerで「C++によるデスクトップ開発」とWindows SDKを導入します。
3. PowerShellまたはコマンドプロンプトで`build_rust.bat`を実行します。

```powershell
build_rust.bat
```

生成物は`target\release\axv.exe`です。

## 次の実装

- 外部RealCUGANのPNG入出力アダプターと処理キュー
- 差分領域へ余白を加え、AI処理後に基準画像へ合成する処理
- `tlg6_native.dll`のFFIアダプター
- VRAM使用量に基づく表示テクスチャのLRUキャッシュ
- パスワード付き書庫と7z
