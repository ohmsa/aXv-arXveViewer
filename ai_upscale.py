# SPDX-License-Identifier: Apache-2.0
"""
AIアップスケール / ノイズ除去（複数エンジン対応、ncnn-vulkanバイナリ呼び出し方式）

重いMLフレームワーク（PyTorch等）をアプリに同梱する代わりに、
ncnn-vulkanでコンパイルされた軽量な実行専用バイナリをサブプロセスとして呼び出す方式。
GPUがVulkan対応であればCPU版より大幅に高速。

【対応エンジン】
- realesrgan : Real-ESRGAN。汎用/アニメ向けモデル、4倍固定。ノイズ除去は暗黙的
               （復元処理の一部として行われ、単独では選べない）。
- realcugan  : RealCUGAN(bilibili製)。アニメ・イラスト特化、Real-ESRGANより新しい。
               ノイズ除去レベル(no-denoise/denoise3x)を倍率と別に選べる。
- waifu2x    : waifu2x(CUnet)。ノイズ除去単独（倍率1倍、拡大なし）にも対応する
               唯一のエンジン。純粋なノイズ除去だけしたい場合はこちらを使う。

【必要なファイル構成】(exeと同じフォルダにある ai_upscale/ の中)
    ai_upscale/
        realesrgan/
            realesrgan-ncnn-vulkan.exe, vcomp140.dll, models/*.bin,*.param
        realcugan/
            realcugan-ncnn-vulkan.exe, vcomp140.dll, models-se/*.bin,*.param
        waifu2x/
            waifu2x-ncnn-vulkan.exe, vcomp140.dll, models-cunet/*.bin,*.param

【注意】
- Windows専用（.exe）。Windows以外では自動的に無効化される。
- Vulkan対応GPU/ドライバが必要。無い場合はエラーになるので、失敗時は
  通常のQtスムーズ拡大にフォールバックする。
- 処理は毎回一時ファイル(入力PNG/出力PNG)を経由する(いずれもファイル入出力
  方式のツールのため)。
"""

import os
import sys
import subprocess
import tempfile
import threading
from pathlib import Path

import numpy as np

from PySide6.QtCore import QObject, Signal, Qt
from PySide6.QtGui import QImage

# デバッグ用の詳細出力([AI DEBUG]等)をON/OFFする状態。viewer.py側の同名の仕組みと
# 合わせて、オプション画面から両方まとめて切り替える(viewer.pyが
# ai_upscale._debug_state["enabled"]を直接書き換える)。既定はOFF。
_debug_state = {"enabled": False}


def debug_print(msg):
    if _debug_state["enabled"]:
        print(msg, flush=True)


def get_ai_upscale_dir():
    """ai_upscaleフォルダの場所を返す（exeやスクリプトと同じ場所にあるはず）"""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent
    return base / "ai_upscale"


# ============================================================
# ノイズレベル推定（AI処理をかける価値があるかの判定に使う）
# ============================================================

def estimate_noise_level(image: QImage) -> float:
    """画像のノイズレベル(ガウスノイズのσ推定値)を高速に計算する。
    Immerkjaer(1996)の手法: 3x3ラプラシアン風マスクへの応答からσを推定する。
    値が大きいほどノイズ（またはJPEG圧縮ノイズ等）が多いと考えられる。
    目安: ~2以下=ほぼノイズなし、~5前後=軽微、~10以上=かなりノイズが多い。"""
    gray = image.convertToFormat(QImage.Format_Grayscale8)
    w, h = gray.width(), gray.height()
    if w < 3 or h < 3:
        return 0.0

    # 大きい画像は計算コストを抑えるため縮小してから評価する(縦横比は保持)
    max_dim = 512
    if max(w, h) > max_dim:
        scale = max_dim / max(w, h)
        gray = gray.scaled(
            max(3, round(w * scale)), max(3, round(h * scale)),
            Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        w, h = gray.width(), gray.height()

    bytes_per_line = gray.bytesPerLine()
    ptr = gray.constBits()
    if hasattr(ptr, "setsize"):  # PyQt compatibility; PySide6 returns a sized memoryview.
        ptr.setsize(bytes_per_line * h)
    buf = bytes(ptr)
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, bytes_per_line)[:, :w].astype(np.float64)

    # 3x3マスク [[1,-2,1],[-2,4,-2],[1,-2,1]] との畳み込み(有効領域のみ、境界は除く)
    conv = (
        arr[0:-2, 0:-2] - 2 * arr[0:-2, 1:-1] + arr[0:-2, 2:]
        - 2 * arr[1:-1, 0:-2] + 4 * arr[1:-1, 1:-1] - 2 * arr[1:-1, 2:]
        + arr[2:, 0:-2] - 2 * arr[2:, 1:-1] + arr[2:, 2:]
    )
    inner_w, inner_h = w - 2, h - 2
    if inner_w <= 0 or inner_h <= 0:
        return 0.0

    sigma = np.sqrt(np.pi / 2) / (6 * inner_w * inner_h) * np.sum(np.abs(conv))
    return float(sigma)


NOISE_THRESHOLD_SKIP_AI = 1.5  # これ以下なら「十分綺麗」と判断し、軽い拡大率ならAIを見送る


def should_use_ai_upscale(noise_level: float, enlarge_factor: float) -> bool:
    """ノイズレベルと拡大率から、AI処理をかける価値があるかを判定する。
    - 拡大率が大きい場合はノイズ量に関わらずAIの恩恵が大きいので常に使う
    - 拡大率が小さく、かつ画像が既に十分綺麗な場合はAI処理を見送り、
      処理時間を節約する(通常のスムーズ拡大で十分なため)"""
    if enlarge_factor >= 2.0:
        return True
    return noise_level > NOISE_THRESHOLD_SKIP_AI


# ============================================================
# エンジン定義
# ============================================================
# 各エンジンのモデルは (表示名, model_kwargs) の形。model_kwargsはbuild_command()に渡される。
# denoise_only=Trueのモデルは倍率1倍(拡大なし)専用、それ以外はupscale_capable(倍率>1)。

ENGINES = {
    "realesrgan": {
        "label": "Real-ESRGAN",
        "exe_name": "realesrgan-ncnn-vulkan.exe",
        "native_scale": 4,  # 固定倍率
        "models": {
            "realesrgan-x4plus-anime": {"label": "アニメ/イラスト向け（軽量・高速）", "scale": 4, "denoise_only": False},
            "realesrgan-x4plus": {"label": "汎用（写真等、やや低速）", "scale": 4, "denoise_only": False},
        },
        "denoise_variants": {},  # このエンジンにはデノイズON/OFFの切替が無い(常に同じモデル)
        "denoise_only_model": None,
    },
    "realcugan": {
        "label": "RealCUGAN",
        "exe_name": "realcugan-ncnn-vulkan.exe",
        "native_scale": None,  # モデルごとに異なる
        "models": {
            "up2x-no-denoise": {"label": "2倍", "scale": 2, "denoise_only": False, "has_denoise": False},
            "up2x-denoise3x": {"label": "2倍・ノイズ除去(最大)", "scale": 2, "denoise_only": False, "has_denoise": True},
            "up3x-no-denoise": {"label": "3倍", "scale": 3, "denoise_only": False, "has_denoise": False},
            "up3x-denoise3x": {"label": "3倍・ノイズ除去(最大)", "scale": 3, "denoise_only": False, "has_denoise": True},
            "up4x-no-denoise": {"label": "4倍", "scale": 4, "denoise_only": False, "has_denoise": False},
            "up4x-denoise3x": {"label": "4倍・ノイズ除去(最大)", "scale": 4, "denoise_only": False, "has_denoise": True},
        },
        # デノイズON/OFF切替用の対応表: {no_denoiseモデル名: denoiseモデル名}(両方向)
        "denoise_variants": {
            "up2x-no-denoise": "up2x-denoise3x", "up2x-denoise3x": "up2x-no-denoise",
            "up3x-no-denoise": "up3x-denoise3x", "up3x-denoise3x": "up3x-no-denoise",
            "up4x-no-denoise": "up4x-denoise3x", "up4x-denoise3x": "up4x-no-denoise",
        },
        "denoise_only_model": None,  # このエンジンには「拡大なしノイズ除去専用」モデルが無い
    },
    "waifu2x": {
        "label": "waifu2x (CUnet)",
        "exe_name": "waifu2x-ncnn-vulkan.exe",
        "native_scale": None,
        "models": {
            "noise0_model": {"label": "ノイズ除去のみ（軽め、拡大なし）", "scale": 1, "denoise_only": True},
            "noise3_model": {"label": "ノイズ除去のみ（最大、拡大なし）", "scale": 1, "denoise_only": True},
            "scale2.0x_model": {"label": "2倍", "scale": 2, "denoise_only": False, "has_denoise": False},
            "noise3_scale2.0x_model": {"label": "2倍・ノイズ除去(最大)", "scale": 2, "denoise_only": False, "has_denoise": True},
        },
        "denoise_variants": {
            "scale2.0x_model": "noise3_scale2.0x_model", "noise3_scale2.0x_model": "scale2.0x_model",
        },
        "denoise_only_model": "noise3_model",  # 拡大なしでノイズ除去だけしたい時に使う
    },
    "openvino": {
        "label": "OpenVINO (Intel CPU/GPU/NPU)",
        "in_process": True,  # サブプロセスではなく、このPythonプロセス内で直接推論する
        "models_subdir": "openvino_models",
        "native_scale": 4,
        "models": {
            "RealESRGAN_x4_fp16": {"label": "4倍・高速(fp16)", "scale": 4, "denoise_only": False},
            "RealESRGAN_x4": {"label": "4倍・高精度(fp32、やや低速)", "scale": 4, "denoise_only": False},
        },
        "denoise_variants": {},  # このエンジンにはデノイズON/OFFの切替が無い
        "denoise_only_model": None,
    },
}

DEFAULT_ENGINE = "realesrgan"
DEFAULT_MODEL = "realesrgan-x4plus-anime"


def is_engine_available(engine_key):
    if engine_key not in ENGINES:
        return False
    engine = ENGINES[engine_key]
    if engine.get("in_process"):
        # OpenVINOはPythonパッケージとして提供されるため、Windows専用ではなく
        # パッケージがインポートできるか、モデル用フォルダがあるかで判定する
        try:
            import openvino  # noqa: F401
        except ImportError:
            return False
        models_dir = get_ai_upscale_dir() / engine["models_subdir"]
        return models_dir.exists()
    if sys.platform != "win32":
        return False
    exe = get_ai_upscale_dir() / engine["exe_name"]
    return exe.exists()


def is_ai_upscale_available():
    """いずれかのエンジンが1つでも使えるか（Windows専用）"""
    return any(is_engine_available(k) for k in ENGINES)


def get_available_engines():
    """実行ファイルが実際に存在するエンジンだけを返す {engine_key: label}"""
    return {k: v["label"] for k, v in ENGINES.items() if is_engine_available(k)}


MODEL_SUBDIR_BY_ENGINE = {
    # 各ncnn-vulkanツールは、-mで渡すフォルダ「名」の文字列でモデルの種類を
    # 判定する仕組みになっており(元の配布物でのフォルダ名がそのまま前提になっている)、
    # 一致しないと"unknown model dir type"で失敗する。フラットに1フォルダへ
    # まとめる構成にはできないため、エンジンごとに専用のサブフォルダを使う。
    "realesrgan": "models",
    "realcugan": "models-se",
    "waifu2x": "models-cunet",
}


def get_models_dir_for_engine(engine_key):
    """エンジンごとの実際のモデルフォルダを返す。"""
    engine = ENGINES.get(engine_key, {})
    if engine.get("in_process"):
        return get_ai_upscale_dir() / engine["models_subdir"]
    subdir = MODEL_SUBDIR_BY_ENGINE.get(engine_key)
    if subdir:
        return get_ai_upscale_dir() / subdir
    return get_ai_upscale_dir()  # 未知のエンジン: フラット構成にフォールバック


def get_available_models(engine_key):
    """指定エンジンで、実際にモデルファイルが揃っているものだけを返す {model_name: label}。
    このドロップダウンは「拡大の基準(倍率/スタイル)」だけを選ぶためのもの:
    - has_denoise=True(デノイズ済みバリアント)は、Dキーでの切替で自動的に使われる
      ようになったため出さない。
    - denoise_only=True(拡大なしノイズ除去専用モデル)も出さない。この用途は
      「アップスケール:なし」+「デノイズ:オン/自動」の組み合わせで実現できるため、
      ここに残すと「モデルはノイズ除去専用なのにアップスケール設定と噛み合わない」
      という混乱(そして常にscale=1で処理されてしまう実害)を招く。"""
    if engine_key not in ENGINES:
        return {}
    engine = ENGINES[engine_key]
    available = {}
    models_dir = get_models_dir_for_engine(engine_key)
    if engine.get("in_process"):
        for name, info in engine["models"].items():
            if info.get("has_denoise") is True or info.get("denoise_only"):
                continue
            if (models_dir / f"{name}.onnx").exists():
                available[name] = info["label"]
    else:
        for name, info in engine["models"].items():
            if info.get("has_denoise") is True or info.get("denoise_only"):
                continue
            if (models_dir / f"{name}.bin").exists() and (models_dir / f"{name}.param").exists():
                available[name] = info["label"]
    return available


def get_model_info(engine_key, model_name):
    return ENGINES.get(engine_key, {}).get("models", {}).get(model_name)


def build_command(engine_key, exe_path, models_dir, input_path, output_path, model_name, gpu_id=None):
    """エンジンごとのコマンドライン引数を組み立てる。
    3エンジンとも -i/-o/-m は共通だが、モデル指定方法が少し異なる:
    - realesrgan: -n <モデル名>（モデル名から倍率も一意に決まる、4倍固定）
    - realcugan/waifu2x: モデル名自体が「ファイル名」なので -m でモデルのあるフォルダを
      指定し、モデル名(拡張子抜き)がそのままファイル名に対応する形。
      いずれも内部的には-nを使わず、モデルファイル名で直接指定する簡易実装のため、
      ここではmodels_dir配下の該当ファイルを直接指せるよう -m は「そのモデルの
      ファイルがあるディレクトリ」を渡す。

    gpu_id: 明示的に使うGPUのインデックス(0,1,...)。Noneなら-gを付けず自動選択に任せる。
            内蔵GPUと外付けGPUが両方ある環境(例: Core Ultra + Arc)で、狙った方を
            確実に使いたい場合に指定する。3エンジンとも共通で-gオプションを持つ。
    """
    if engine_key == "realesrgan":
        info = get_model_info(engine_key, model_name)
        cmd = [
            str(exe_path),
            "-i", str(input_path),
            "-o", str(output_path),
            "-n", model_name,
            "-s", str(info["scale"]),
            "-m", str(models_dir),
        ]
    elif engine_key == "realcugan":
        # up{scale}x-{denoise} という名前から、-s と -n を組み立てる
        info = get_model_info(engine_key, model_name)
        denoise = "3" if "denoise3x" in model_name else "-1"
        cmd = [
            str(exe_path),
            "-i", str(input_path),
            "-o", str(output_path),
            "-s", str(info["scale"]),
            "-n", denoise,
            "-m", str(models_dir),
        ]
    elif engine_key == "waifu2x":
        info = get_model_info(engine_key, model_name)
        if "noise0" in model_name:
            noise = "0"
        elif "noise3" in model_name:
            noise = "3"
        else:
            noise = "-1"
        cmd = [
            str(exe_path),
            "-i", str(input_path),
            "-o", str(output_path),
            "-s", str(info["scale"]),
            "-n", noise,
            "-m", str(models_dir),
        ]
    else:
        raise ValueError(f"未知のエンジン: {engine_key}")

    if gpu_id is not None:
        cmd += ["-g", str(gpu_id)]
    return cmd


# ============================================================
# OpenVINO推論（サブプロセスではなく、このPythonプロセス内で直接実行する）
# ============================================================
# Intel公式ガイド"Using Real-ESRGAN to Upscale Images on the Intel Platform"
# (Document 816445-0.8, 2024年2月)の手法に基づく実装。
# CPU/内蔵GPU/Arc GPU/NPUをOpenVINOの共通APIから使い分けられる。

_openvino_core = None
_openvino_model_cache = {}  # (model_path文字列, device) -> コンパイル済みモデル
_openvino_cache_lock = threading.Lock()


def get_openvino_devices():
    """OpenVINOから見える推論デバイス一覧を返す(例: ['CPU','GPU','GPU.0','NPU'])。
    OpenVINO未インストール、またはこの環境で使えない場合は空リストを返す。"""
    try:
        import openvino as ov
        core = ov.Core()
        return list(core.available_devices)
    except Exception:
        return []


def _get_openvino_compiled_model(model_path, device):
    import openvino as ov
    global _openvino_core

    key = (str(model_path), device)
    with _openvino_cache_lock:
        if key in _openvino_model_cache:
            return _openvino_model_cache[key]
        if _openvino_core is None:
            _openvino_core = ov.Core()
        model = _openvino_core.read_model(str(model_path))
        compiled = _openvino_core.compile_model(model, device_name=device)
        _openvino_model_cache[key] = compiled
        return compiled


def run_openvino_inference(model_name, device, image: QImage) -> QImage:
    """OpenVINOで画像をアップスケールする。モデルは初回のみコンパイルし、
    以降は同じ(モデル,デバイス)の組み合わせならキャッシュを再利用する
    (コンパイル自体に時間がかかるため、ページ送りのたびに毎回コンパイルすると遅い)。
    失敗時は例外を投げる(呼び出し側でtry/exceptする想定)。"""
    models_dir = get_ai_upscale_dir() / ENGINES["openvino"]["models_subdir"]
    model_path = models_dir / f"{model_name}.onnx"
    if not model_path.exists():
        raise FileNotFoundError(f"モデルが見つかりません: {model_path}")

    compiled_model = _get_openvino_compiled_model(model_path, device)
    output_layer = compiled_model.output(0)

    # QImage -> numpy (RGB, HWC, 0-1) への変換
    rgb_image = image.convertToFormat(QImage.Format_RGB888)
    w, h = rgb_image.width(), rgb_image.height()
    bytes_per_line = rgb_image.bytesPerLine()
    ptr = rgb_image.constBits()
    if hasattr(ptr, "setsize"):
        ptr.setsize(bytes_per_line * h)
    buf = bytes(ptr)
    arr = np.frombuffer(buf, dtype=np.uint8).reshape(h, bytes_per_line)[:, :w * 3].reshape(h, w, 3)

    input_data = arr.astype(np.float32) / 255.0
    input_data = input_data.transpose(2, 0, 1)[np.newaxis, ...]  # NHWC -> NCHW

    result = compiled_model([input_data])[output_layer]

    out = result[0].transpose(1, 2, 0)  # CHW -> HWC
    out = np.clip(out, 0, 1) * 255.0
    out = np.ascontiguousarray(out.astype(np.uint8))

    out_h, out_w = out.shape[0], out.shape[1]
    out_image = QImage(out.data, out_w, out_h, out_w * 3, QImage.Format_RGB888)
    return out_image.copy()  # numpy配列(out)の寿命に依存しないようコピーする


# ============================================================
# バックグラウンドワーカー
# ============================================================

class AIUpscaleWorker(QObject):
    """1枚の画像をAI処理(アップスケール/ノイズ除去)するワーカー
    （バックグラウンドスレッドで動かす）。ファイル入出力方式のツールなので、
    一時ファイルを介して呼び出す。
    passes>1の場合、前段の出力を次段の入力として使い、繰り返し処理していく
    （「重ねがけ」。目標解像度に届くまでAI処理を重ねることで、単純なQt拡大より
    ディテールを保ったまま高倍率のズームに対応する。denoise_onlyモデルでは
    passes=1固定で使う想定）。
    request_cancel()を呼ぶと、実行中のパスの完了を待ってから(または次のパス開始前に)
    処理を打ち切る。ページ送り等で不要になった処理をVRAM/CPU資源から早めに解放するため。"""
    finished = Signal(object, QImage, str, str)  # (token, image, engine_key, model_name)
    failed = Signal(object, str)                 # (token, エラーメッセージ)
    progress = Signal(object, int, int)          # (token, 現在のパス, 総パス数)
    pass_finished = Signal(object, QImage, int, int)  # (token, 中間結果, 現在のパス, 総パス数)
    cancelled = Signal(object)                   # (token,)

    def __init__(self, request_token, pixmap_image: QImage, engine_key: str, model_name: str, passes: int = 1, gpu_id=None, device=None):
        """
        request_token: どのページ/ズーム状態への結果か識別するための任意のオブジェクト
                        （結果が古くなっていないか呼び出し側が確認するために使う）
        pixmap_image:  処理対象の元画像(QImage)
        engine_key:    ENGINESのキー("realesrgan"/"realcugan"/"waifu2x"/"openvino")
        model_name:    そのエンジンのモデル名
        passes:        何回重ねがけするか(拡大系モデルのみ有効。denoise_onlyなら1のまま使う)
        gpu_id:        使うGPUのインデックス(ncnn系エンジン向け、内蔵/外付けが両方ある環境用)。Noneなら自動選択。
        device:        OpenVINOで使うデバイス名("CPU"/"GPU"/"NPU"等、engine_key="openvino"の時のみ使う)
        """
        super().__init__()
        self.request_token = request_token
        self.image = pixmap_image
        self.engine_key = engine_key
        self.model_name = model_name
        self.passes = max(1, passes)
        self.gpu_id = gpu_id
        self.device = device
        self._cancel_requested = False
        self._current_process = None  # 実行中のsubprocess.Popen(キャンセル時にkillするため)

    def request_cancel(self):
        """メインスレッドから呼ぶ。実行中のサブプロセスがあれば即座に終了させ、
        次のパスの開始も抑止する。"""
        self._cancel_requested = True
        proc = self._current_process
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    def run(self):
        # Preparation errors must also send a terminal signal to release the queue.
        try:
            self._run()
        except Exception as exc:
            self.failed.emit(self.request_token, f"AI処理の準備に失敗しました: {exc}")

    def _run(self):
        if self.engine_key not in ENGINES:
            self.failed.emit(self.request_token, f"未知のエンジンです: {self.engine_key}")
            return

        engine = ENGINES[self.engine_key]

        if engine.get("in_process"):
            self._run_in_process(engine)
            return


        ai_dir = get_ai_upscale_dir()  # フラット構成: 全エンジンのexe/モデルが同じフォルダ直下
        exe_path = ai_dir / engine["exe_name"]

        if not exe_path.exists():
            self.failed.emit(self.request_token, f"実行ファイルが見つかりません: {exe_path}")
            return

        tmp_dir = Path(tempfile.mkdtemp(prefix="archive_viewer_ai_"))
        current_image = self.image

        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NO_WINDOW  # コンソール窓を出さない

        try:
            for pass_index in range(self.passes):
                if self._cancel_requested:
                    self.cancelled.emit(self.request_token)
                    return

                self.progress.emit(self.request_token, pass_index + 1, self.passes)

                input_path = tmp_dir / f"input_{pass_index}.png"
                output_path = tmp_dir / f"output_{pass_index}.png"

                if not current_image.save(str(input_path), "PNG"):
                    self.failed.emit(self.request_token, "入力画像の一時保存に失敗しました")
                    return

                cmd = build_command(
                    self.engine_key, exe_path, get_models_dir_for_engine(self.engine_key),
                    input_path, output_path, self.model_name,
                    gpu_id=self.gpu_id
                )
                debug_print(f"[AI DEBUG] pass {pass_index + 1}/{self.passes} cmd: {cmd}")

                try:
                    self._current_process = subprocess.Popen(
                        cmd,
                        cwd=str(ai_dir),
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        creationflags=creationflags,
                    )
                    if self._cancel_requested:
                        self._current_process.kill()
                    stdout, stderr = self._current_process.communicate(timeout=120)
                    returncode = self._current_process.returncode
                finally:
                    proc = self._current_process
                    if proc is not None:
                        if proc.poll() is None:
                            proc.kill()
                        proc.communicate()
                    self._current_process = None

                if self._cancel_requested:
                    self.cancelled.emit(self.request_token)
                    return

                if returncode != 0:
                    stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
                    stdout_text = stdout.decode("utf-8", errors="replace") if stdout else ""
                    debug_print(f"[AI DEBUG] returncode={returncode}")
                    debug_print(f"[AI DEBUG] stdout: {stdout_text}")
                    debug_print(f"[AI DEBUG] stderr: {stderr_text}")
                    self.failed.emit(
                        self.request_token,
                        f"処理が失敗しました(パス{pass_index + 1}/{self.passes}):\n{stderr_text[:300]}"
                    )
                    return

                if not output_path.exists():
                    self.failed.emit(self.request_token, "出力ファイルが生成されませんでした")
                    return

                next_image = QImage(str(output_path))
                if next_image.isNull():
                    self.failed.emit(self.request_token, "出力画像を読み込めませんでした")
                    return

                current_image = next_image

                # 1段終わるごとに中間結果を通知する(表示をすぐ更新できるように)
                self.pass_finished.emit(self.request_token, current_image, pass_index + 1, self.passes)

                # 次のパスの入力に使わないファイルは掃除しておく(容量節約)
                try:
                    input_path.unlink()
                except Exception:
                    pass
                if pass_index > 0:
                    try:
                        (tmp_dir / f"output_{pass_index - 1}.png").unlink()
                    except Exception:
                        pass

            self.finished.emit(self.request_token, current_image, self.engine_key, self.model_name)

        except subprocess.TimeoutExpired:
            if self._current_process is not None:
                try:
                    self._current_process.kill()
                except Exception:
                    pass
            self.failed.emit(self.request_token, "処理がタイムアウトしました（120秒）")
        except Exception as e:
            self.failed.emit(self.request_token, f"予期しないエラー: {e}")
        finally:
            try:
                for p in tmp_dir.glob("*"):
                    p.unlink()
                tmp_dir.rmdir()
            except Exception:
                pass

    def _run_in_process(self, engine):
        """OpenVINOのように、サブプロセスではなくこのPythonプロセス内で直接推論する
        エンジン向けの実行ルート。一時ファイルの入出力が不要な分、ncnn-vulkan系より
        オーバーヘッドが少ない。キャンセルは各パスの開始前にのみチェックする
        (1回の推論自体は短時間で終わるため、途中中断は行わない)。"""
        device = self.device or "AUTO"
        current_image = self.image

        try:
            for pass_index in range(self.passes):
                if self._cancel_requested:
                    self.cancelled.emit(self.request_token)
                    return

                self.progress.emit(self.request_token, pass_index + 1, self.passes)

                current_image = run_openvino_inference(self.model_name, device, current_image)
                if self._cancel_requested:
                    self.cancelled.emit(self.request_token)
                    return

                self.pass_finished.emit(self.request_token, current_image, pass_index + 1, self.passes)

            self.finished.emit(self.request_token, current_image, self.engine_key, self.model_name)

        except Exception as e:
            self.failed.emit(self.request_token, f"OpenVINO処理でエラーが発生しました: {e}")


class BatchAIUpscaleWorker(QObject):
    """複数の画像を1回のプロセス起動でまとめて処理する(フォルダ入出力モード)。
    realesrgan-ncnn-vulkan等は -i/-o にファイルだけでなくディレクトリも渡せるため、
    1枚ずつsubprocessを起動するオーバーヘッド(プロセス起動コスト・GPU初期化の
    やり直し)を、まとめて処理することで減らす狙い。

    現在表示中/次ページの最優先処理には使わない(レスポンスを最優先するため、
    そちらは常に単体処理のAIUpscaleWorkerを使う)。背景で先読みしている、
    同じengine/model/passesの複数ページ分をまとめる時だけ使う想定。

    in_process系エンジン(OpenVINO)はサブプロセスを起動しないので対象外。"""
    item_finished = Signal(object, QImage)  # (cache_key, 最終結果画像) - 完了したものから順次
    batch_finished = Signal()               # 全件処理完了(キャンセル・失敗以外)
    failed = Signal(str)                    # バッチ全体が失敗した場合のメッセージ
    cancelled = Signal()

    def __init__(self, items, engine_key: str, model_name: str, passes: int = 1, gpu_id=None):
        """items: [(cache_key, QImage), ...] 全て同じengine/model/passesで処理する前提。"""
        super().__init__()
        self.items = list(items)
        self.engine_key = engine_key
        self.model_name = model_name
        self.passes = max(1, passes)
        self.gpu_id = gpu_id
        self._cancel_requested = False
        self._current_process = None

    def request_cancel(self):
        self._cancel_requested = True
        proc = self._current_process
        if proc is not None:
            try:
                proc.kill()
            except Exception:
                pass

    def run(self):
        # Preparation errors must also send a terminal signal to release the queue.
        try:
            self._run()
        except Exception as exc:
            self.failed.emit(f"AI処理の準備に失敗しました: {exc}")

    def _run(self):
        if self.engine_key not in ENGINES:
            self.failed.emit(f"未知のエンジンです: {self.engine_key}")
            return
        engine = ENGINES[self.engine_key]
        if engine.get("in_process"):
            self.failed.emit("バッチ処理はin_process系エンジン(OpenVINO)には対応していません")
            return
        if not self.items:
            self.batch_finished.emit()
            return

        ai_dir = get_ai_upscale_dir()
        exe_path = ai_dir / engine["exe_name"]
        if not exe_path.exists():
            self.failed.emit(f"実行ファイルが見つかりません: {exe_path}")
            return

        tmp_root = Path(tempfile.mkdtemp(prefix="archive_viewer_ai_batch_"))
        creationflags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

        try:
            # ファイル名でcache_keyを引けるように対応表を作る(処理順序に依存しないため)
            name_to_key = {}
            current_dir = tmp_root / "pass0"
            current_dir.mkdir()
            for i, (cache_key, image) in enumerate(self.items):
                fname = f"item_{i:04d}.png"
                if not image.save(str(current_dir / fname), "PNG"):
                    self.failed.emit(f"入力画像の一時保存に失敗しました(item {i})")
                    return
                name_to_key[fname] = cache_key

            for pass_index in range(self.passes):
                if self._cancel_requested:
                    self.cancelled.emit()
                    return

                output_dir = tmp_root / f"pass{pass_index + 1}"
                output_dir.mkdir()

                cmd = build_command(
                    self.engine_key, exe_path, get_models_dir_for_engine(self.engine_key),
                    current_dir, output_dir, self.model_name, gpu_id=self.gpu_id
                )
                debug_print(f"[AI BATCH DEBUG] pass {pass_index + 1}/{self.passes} "
                            f"({len(self.items)}枚まとめて処理) cmd: {cmd}")

                try:
                    self._current_process = subprocess.Popen(
                        cmd, cwd=str(ai_dir),
                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                        creationflags=creationflags,
                    )
                    # 複数枚まとめて処理するぶん、単体処理より長めのタイムアウトにする。
                    # ただし1枚あたりの上限は抑えめにしておく(枚数が多いバッチで
                    # タイムアウトが極端に長くなると、万一キャンセルが即座に効かない
                    # 場合に、優先度の高い現在ページの処理が長時間ブロックされてしまう)。
                    if self._cancel_requested:
                        self._current_process.kill()
                    stdout, stderr = self._current_process.communicate(
                        timeout=60 * max(1, len(self.items))
                    )
                    returncode = self._current_process.returncode
                finally:
                    proc = self._current_process
                    if proc is not None:
                        if proc.poll() is None:
                            proc.kill()
                        proc.communicate()
                    self._current_process = None

                if self._cancel_requested:
                    self.cancelled.emit()
                    return

                if returncode != 0:
                    stderr_text = stderr.decode("utf-8", errors="replace") if stderr else ""
                    debug_print(f"[AI BATCH DEBUG] returncode={returncode} stderr={stderr_text}")
                    self.failed.emit(f"バッチ処理が失敗しました(パス{pass_index + 1}/{self.passes}): {stderr_text[:300]}")
                    return

                current_dir = output_dir

            # 最終パスの出力を、ファイル名経由で元のcache_keyに結びつけて順次通知する
            for fname, cache_key in name_to_key.items():
                if self._cancel_requested:
                    self.cancelled.emit()
                    return
                out_path = current_dir / fname
                if not out_path.exists():
                    self.failed.emit(f"バッチ出力が見つかりません: {fname}")
                    return
                result_image = QImage(str(out_path))
                if result_image.isNull():
                    self.failed.emit(f"バッチ出力を読み込めません: {fname}")
                    return
                self.item_finished.emit(cache_key, result_image)

            self.batch_finished.emit()

        except subprocess.TimeoutExpired:
            if self._current_process is not None:
                try:
                    self._current_process.kill()
                except Exception:
                    pass
            self.failed.emit("バッチ処理がタイムアウトしました")
        except Exception as e:
            self.failed.emit(f"予期しないエラー: {e}")
        finally:
            try:
                import shutil
                shutil.rmtree(tmp_root, ignore_errors=True)
            except Exception:
                pass

