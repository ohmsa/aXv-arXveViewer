# Third-party notices

aXv固有のソースはApache-2.0で提供します。Cargo依存ライブラリと外部ツールには、
各権利者のライセンスが適用されます。

## Rust依存ライブラリ

主要な直接依存は `anyhow`、`eframe/egui/wgpu`、`image`、`natord`、
`rfd`、`zip` です。正確なバージョンは `Cargo.lock` を参照してください。
バイナリ公開前に、その時点の全推移依存についてライセンス一覧を生成して配布物へ含めます。

## 任意の外部ツール

| Component | Purpose | License/condition |
|---|---|---|
| Real-ESRGAN ncnn Vulkan | AI upscale | MIT |
| Real-CUGAN ncnn Vulkan | AI upscale | MIT |
| waifu2x ncnn Vulkan | AI upscale | MIT（ncnnはBSD-3-Clause） |
| RealSR ncnn Vulkan | AI upscale | MIT |
| SRMD ncnn Vulkan | AI upscale | MIT |
| UnRAR | RAR extraction | RARLAB UnRAR license |
| 7-Zip command line tool | 7z extraction | 7-Zipの各構成要素のライセンス |
| Kirikiri Z TLG6 decoder | TLG6 decoding reference | Modified BSD; `LICENSES/KIRIKIRI-Z.txt` |
| tlg_rs | Pure Rust TLG6 decoding reference | Unlicense; `LICENSES/TLG_RS-UNLICENSE.txt` |

`setup_optional_tools.bat` はAIツールをaXvから再配布せず、利用者の確認後に公式の
GitHub Releaseから利用者のPCへ直接取得します。UnRARは自動取得しません。

Upstream information:

- https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan
- https://github.com/nihui/realcugan-ncnn-vulkan
- https://github.com/nihui/waifu2x-ncnn-vulkan
- https://github.com/nihui/realsr-ncnn-vulkan
- https://github.com/nihui/srmd-ncnn-vulkan
- https://github.com/Tencent/ncnn
- https://www.rarlab.com/license.htm
- https://www.7-zip.org/license.txt
- https://github.com/krkrz/krkrz/blob/master/LICENSE
- https://github.com/Forlos/tlg_rs
