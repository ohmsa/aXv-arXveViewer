# AI機能と書庫ツールの導入

本リポジトリはAI実行ファイル、学習済みモデル、`unrar.exe`、`7z.exe`を
再配布しません。任意ツールの案内は次から起動できます。

```bat
setup_optional_tools.bat
```

## 自動取得するAIツール

利用者が画面上の説明を確認して選択した場合に限り、`download_ai_tools.ps1` が公式GitHub
Releases APIからWindows向け配布物を直接取得します。取得物は `ai_upscale/` に置かれ、
配布ZIP内で検出できたライセンス文は `ai_upscale/licenses/` に保存されます。

- Real-ESRGAN ncnn Vulkan
- Real-CUGAN ncnn Vulkan
- waifu2x ncnn Vulkan
- RealSR ncnn Vulkan
- SRMD ncnn Vulkan

GitHub APIが返した最新Releaseを使うため、配布元がファイル名や構成を変更すると取得に
失敗することがあります。その場合はバッチがエラーを表示し、不完全な一時ファイルを削除します。

## 利用者が取得するツール

### UnRAR

バッチの `2` を選ぶとRARLAB公式ダウンロードページを既定ブラウザで開きます。
ライセンスを確認して取得・展開し、`unrar.exe`を `axv.exe` と同じフォルダへ置いてください。
公式ページへの案内だけを行い、aXvはUnRARをダウンロードまたは再配布しません。

### 7-Zip

バッチの `3` を選ぶと7-Zip公式ページを開きます。通常インストールして `7z` をPATHから
実行可能にするか、利用条件を確認して `7z.exe`を `axv.exe` と同じフォルダへ置きます。

TLG6デコーダはaXv本体へ組み込まれているため、追加DLLは不要です。
