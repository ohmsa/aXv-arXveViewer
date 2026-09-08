# AIUpscaler

Windows向けの画像・アーカイブビューアです。フォルダ、ZIP、RAR、7z内の画像を表示し、任意に外部AIエンジンで現在画像と後続画像をアップスケールします。GUIはPySide6です。

## 開発環境

Python 3.11以降を推奨します。

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python viewer.py
```

OpenVINOも使う場合は `python -m pip install -r requirements-openvino.txt` を実行します。

## AIエンジンの配置

AI実行ファイルとモデルはリポジトリに含めません。各配布元のライセンスを確認し、プロジェクト直下の `ai_upscale/` に自分で配置してください。`prepare_ai_folder.bat` を実行すると配置を検査できます。

詳しい配置方法と公式配布物の取得については [AI_SETUP.md](AI_SETUP.md) を参照してください。

対応候補はReal-ESRGAN ncnn Vulkan、Real-CUGAN ncnn Vulkan、waifu2x ncnn Vulkan、OpenVINO対応ONNXモデルです。AIフォルダがなくてもビューア機能は動作します。RARには、別途導入した `unrar` など、rarfileが利用できるコマンドが必要です。

## Windows実行ファイルのビルド

`build.bat` はプロジェクト内の `python_embed/` にPythonを用意し、PyInstallerでビルドします。

- `build_debug.bat`: コンソール付きフォルダ版
- `build.bat`: GUIフォルダ版
- `build_final.bat`: GUI単一EXE版

ローカルに `ai_upscale/`、`unrar.exe`、`tlg6_native.dll` がある場合だけビルド出力へコピーします。`ai_upscale/` は直下の実行ファイルと `models/`、`models-se/`、`models-cunet/`、`openvino_models/` のみをコピーし、配布物を展開したまま残っている重複サブフォルダは除外します。これらはGit管理対象外です。

## テスト

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
python -m unittest discover -p test_regressions.py -v
```

## ライセンス

このリポジトリ固有のソースは [Apache License 2.0](LICENSE) です。依存ライブラリや利用者が追加するAIエンジン・モデルには別のライセンスが適用されます。[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) も確認してください。
