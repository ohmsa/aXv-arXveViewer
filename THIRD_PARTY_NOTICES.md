# Third-party notices

AIUpscaler固有のソースはApache-2.0で提供します。以下の依存物は各権利者のライセンスに従います。この一覧は各ライセンス本文の代わりではありません。

| Component | Purpose | License |
|---|---|---|
| Python | Runtime | PSF License Agreement |
| PySide6 / Qt for Python | GUI bindings | LGPL-3.0-only, GPL-3.0-only, or commercial |
| Qt libraries bundled by PySide6 | GUI runtime | Component-dependent; commonly LGPL-3.0-only/GPL/commercial |
| NumPy | Image arrays | BSD-3-Clause |
| Pillow | Image codecs | HPND |
| pyzipper | Encrypted ZIP support | MIT |
| rarfile | RAR integration | ISC |
| py7zr | 7z support | LGPL-2.1-or-later |
| keyring | Credential storage | MIT |
| PyInstaller | Windows packaging | GPL-2.0-or-later with a bootloader exception |
| OpenVINO (optional) | ONNX inference | Apache-2.0 |
| Real-ESRGAN / Real-ESRGAN ncnn Vulkan (optional download) | AI upscale | BSD-3-Clause / MIT |
| Real-CUGAN ncnn Vulkan (optional download) | AI upscale | MIT |
| waifu2x ncnn Vulkan (optional download) | AI upscale | MIT |
| UnRAR (optional, user-installed) | RAR extraction | RARLAB UnRAR license |

PySide6/Qtをバイナリへ同梱して再配布する場合は、対応するライセンス本文・著作権表示・再リンクに関する条件を満たしてください。PyInstallerのbootloader exceptionも配布形態に応じて確認してください。

AIエンジン、モデル、`unrar.exe`、`tlg6_native.dll` はこのリポジトリや配布ZIPに含めません。利用者が追加した外部物のライセンス確認と通知は、追加・再配布する人の責任です。

Upstream license information:

- https://doc.qt.io/qtforpython-6/licenses.html
- https://numpy.org/doc/stable/license.html
- https://github.com/python-pillow/Pillow/blob/main/LICENSE
- https://github.com/danifus/pyzipper/blob/master/LICENSE
- https://github.com/markokr/rarfile/blob/master/LICENSE
- https://github.com/miurahr/py7zr/blob/master/LICENSE
- https://github.com/jaraco/keyring/blob/main/LICENSE
- https://pyinstaller.org/en/stable/license.html
- https://github.com/openvinotoolkit/openvino/blob/master/LICENSE
- https://github.com/xinntao/Real-ESRGAN/blob/master/LICENSE
- https://github.com/xinntao/Real-ESRGAN-ncnn-vulkan/blob/master/LICENSE
- https://github.com/nihui/realcugan-ncnn-vulkan/blob/main/LICENSE
- https://github.com/nihui/waifu2x-ncnn-vulkan/blob/master/LICENSE
- https://www.rarlab.com/license.htm
