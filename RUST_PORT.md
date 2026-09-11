# Rust移植版

汎用CPU実装から始めるaXvのRust実装です。アプリケーションコードは`src/`以下にあります。

## 現在実装されている範囲

- フォルダ、単体画像、ZIP、外部`unrar.exe`によるRAR読込
- PNG、JPEG、WebP、BMP、GIFの一括デコードと自然順ソート
- 本体内蔵の純Rust TLG6デコーダ（追加DLL不要）
- `wgpu`を利用した画像表示と複数ページのGPUテクスチャLRUキャッシュ
- F11全画面表示。背景と未使用領域を黒で描画
- 差分処理なし、表示順の前後差分、類似画像探索によるAI処理順最適化の切替
- 外部RealCUGANのPNG入出力アダプター、処理キュー、差分領域の合成
- `M`キーで、現在ページの3枚前より古いデコード画像をRAMから破棄
- 新しい入力を開いた時の旧ライブラリ・GPUテクスチャ破棄
- 表示中と前後2ページを保護しながら、表示順に毎フレーム最大1ページをGPUへ先読み
- AI結果でRGBAが置き換わった際の古いGPUテクスチャ無効化

破棄するのはデコード済みRGBAだけです。元ファイルと再デコード用の圧縮データは削除しません。

## ビルド

PowerShellまたはコマンドプロンプトで`build_rust.bat`を実行します。Rustが無ければ
プロジェクト内へ公式Rustupを導入し、Microsoft C++ Build Toolsが無ければ、説明と
確認を表示した後にwinget経由で公式インストーラーを実行します。

```powershell
build_rust.bat
```

生成物は`target\release\axv.exe`です。

### Rustを通常インストールしない場合

`build_rust_portable.bat`を実行すると、公式のRustupを取得し、RustとCargoを
プロジェクト内の`.rustup/`と`.cargo/`へ配置してビルドします。PATHやユーザー領域の
Rust設定は変更しません。

```powershell
build_rust_portable.bat
```

MSVCリンカーとWindows SDKはRustに含まれません。未導入の場合は
`ensure_msvc_build_tools.bat`がMicrosoft公式のVisual Studio Build Toolsと
`Microsoft.VisualStudio.Workload.VCTools`を案内・導入します。

RustupはMITまたはApache-2.0で提供されています。Visual Studio Build Toolsには
Microsoftのライセンス条項が別途適用されます。どちらも本リポジトリへ同梱・再配布せず、
利用者が確認した場合だけ公式配布元から取得します。

GPUキャッシュ上限はオプションで128～8192 MiBに設定できます。これはアプリが作成した
RGBAテクスチャの概算値であり、GPUドライバー全体の使用量ではありません。

## 次の実装

- 内蔵ncnn/Vulkan処理とパッチアトラス
- GPUメモリ予算を取得できる環境での上限自動調整
- パスワード付き書庫
