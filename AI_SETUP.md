# AI機能とUnRARの導入

本リポジトリは、AI実行ファイル、学習済みモデル、`unrar.exe` を再配布しません。
利用者が各配布元のライセンスを確認したうえで導入してください。

## AIツール

Windowsでは `download_ai_tools.bat` を実行すると、公式GitHub Releasesから次を取得し、
必要な実行ファイルとモデルだけを `ai_upscale/` へ配置します。

- Real-ESRGAN ncnn Vulkan（BSD-3-Clause）
- Real-CUGAN ncnn Vulkan（MIT）
- waifu2x ncnn Vulkan（MIT）
- RealSR ncnn Vulkan（MIT）
- SRMD ncnn Vulkan（MIT）

生成される基本構成は次のとおりです。

```text
ai_upscale/
  realesrgan-ncnn-vulkan.exe
  realcugan-ncnn-vulkan.exe
  waifu2x-ncnn-vulkan.exe
  realsr-ncnn-vulkan.exe
  srmd-ncnn-vulkan.exe
  ac_cli.exe                 # 任意: Anime4KCPP CLI
  vcomp140.dll
  models/
  models-se/
  models-cunet/
  models-DF2K/
  models-srmd/
```

`ac_cli.exe` と Real-CUGAN が両方見つかる場合は「Anime4K → Real-CUGAN」が
選択肢に現れます。Anime4KCPPは動画モジュールを含まないCLIがMIT、動画モジュールを
含む構成はGPLv3です。取得したバイナリの構成とライセンスを確認してから配置してください。

OpenVINOを使う場合は `python -m pip install -r requirements-openvino.txt` を実行し、
権利を確認したONNXモデルを `ai_upscale/openvino_models/` に配置してください。

## UnRAR

RARLABのライセンスは、書面許可のないダウンロードバンドルを禁止しています。
そのため取得バッチにはUnRARの自動ダウンロードを含めていません。
[RARLAB公式ダウンロードページ](https://www.rarlab.com/download.htm)から利用者自身で取得し、
ライセンスへ同意したうえで、`unrar.exe` をプロジェクト直下へ配置してください。

`build.bat` は、ローカルに存在するこれらの任意ファイルだけを `dist` へコピーします。

