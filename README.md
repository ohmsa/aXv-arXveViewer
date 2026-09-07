# AIUpscaler

Windows向けの画像・アーカイブビューアです。フォルダ、ZIP、RAR、7z内の画像を表示し、任意に外部AIエンジンで現在画像と後続画像をアップスケールします。GUIはPySide6です。TLG5/TLG6も純Pythonデコーダーで表示できます。

## いちばん簡単なビルド方法（Windows）

現在、GitHub Releasesにはビルド済みEXEを掲載していないため、ソースZIPをダウンロードしただけではアプリは起動しません。次の手順でEXEを作成します。

1. GitHubの **Code** → **Download ZIP** でソースを取得する
2. ZIPを通常のフォルダへ展開する
3. 展開先の `build.bat` をダブルクリックする
4. 初回のPython・依存ライブラリ取得とビルドが終わるまで待つ
5. `dist\ArchiveViewer\ArchiveViewer.exe` を起動する

`build.bat` はプロジェクト内の `python_embed/` にPython 3.11を取得して使います。システム全体へPythonをインストールする必要はありません。初回はインターネット接続が必要です。

| 実行するファイル | 出力 | 用途 |
|---|---|---|
| `build.bat` | `dist\ArchiveViewer\ArchiveViewer.exe` | 通常版、ビルドが比較的速い |
| `build_debug.bat` | `dist\ArchiveViewer\ArchiveViewer.exe` | コンソール付きデバッグ版 |
| `build_final.bat` | `dist\ArchiveViewer.exe` | 配布向け単一EXE版 |

AIエンジンを使わない場合でも、画像・アーカイブ・TLGビューアとしてビルドして実行できます。RARには、別途導入した `unrar` など、rarfileが利用できるコマンドが必要です。

## Pythonから直接実行する方法

Python 3.11以降を推奨します。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python viewer.py
```

OpenVINOも使う場合は、追加で `python -m pip install -r requirements-openvino.txt` を実行します。

## AIエンジンの準備

AI実行ファイルとモデルはリポジトリに含めません。`prepare_ai_folder.bat`をダブルクリックすると、必要なサブフォルダを作成し、現在の配置状況と公式配布ページを表示します。

```text
ai_upscale/
├─ realesrgan-ncnn-vulkan.exe
├─ models/
│  ├─ *.param
│  └─ *.bin
├─ realcugan-ncnn-vulkan.exe
├─ models-se/
│  ├─ *.param
│  └─ *.bin
├─ waifu2x-ncnn-vulkan.exe
├─ models-cunet/
│  ├─ *.param
│  └─ *.bin
└─ openvino_models/
   └─ *.onnx
```

公式配布ページ:

- [Real-ESRGAN releases](https://github.com/xinntao/Real-ESRGAN/releases)
- [Real-CUGAN ncnn Vulkan releases](https://github.com/nihui/realcugan-ncnn-vulkan/releases)
- [waifu2x ncnn Vulkan releases](https://github.com/nihui/waifu2x-ncnn-vulkan/releases)
- [OpenVINO](https://github.com/openvinotoolkit/openvino)

配布物を展開し、EXEと使用するモデルを上記の位置へコピーしてください。配布物内のライセンスとREADMEも保存してください。配置後にもう一度 `prepare_ai_folder.bat` を実行すると検出状況を確認できます。

### バッチを使わず、PowerShellへコピペする場合

プロジェクトのフォルダをPowerShellで開き、次を貼り付けて実行します。

```powershell
$ai = Join-Path (Get-Location) "ai_upscale"
@("models", "models-se", "models-cunet", "openvino_models") |
    ForEach-Object {
        New-Item -ItemType Directory -Force -Path (Join-Path $ai $_) | Out-Null
    }

Write-Host "AI folder prepared: $ai"
Write-Host "Real-ESRGAN: https://github.com/xinntao/Real-ESRGAN/releases"
Write-Host "Real-CUGAN:  https://github.com/nihui/realcugan-ncnn-vulkan/releases"
Write-Host "waifu2x:     https://github.com/nihui/waifu2x-ncnn-vulkan/releases"
Write-Host "OpenVINO:    https://github.com/openvinotoolkit/openvino"
```

このコードはフォルダを作るだけで、第三者の実行ファイルやモデルをダウンロード・再配布しません。

## テスト

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest discover -p test_regressions.py -v
```

## ライセンス

このリポジトリ固有のソースは [Apache License 2.0](LICENSE) です。依存ライブラリや利用者が追加するAIエンジン・モデルには別のライセンスが適用されます。[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) も確認してください。

フォルダ作成用バッチやREADMEのコマンドをApache-2.0のプロジェクトへ収録すること自体には、第三者バイナリの再配布は伴いません。ダウンロードしたAIエンジンやモデルを再配布する場合は、それぞれのライセンス条件に従ってください。
