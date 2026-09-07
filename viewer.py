# SPDX-License-Identifier: Apache-2.0
"""
arχveViewer (aXv)
- ZIP / RAR をドラッグ&ドロップで開く
- パスワード付きZIPは password_list.txt に書いたパスワードを順に試す
- 画像は等倍(1:1)表示（ウィンドウより大きい場合はスクロール）
- 矢印キー(← →)でページ送り、Escで終了

必要なライブラリ:
    pip install PySide6 pyzipper rarfile Pillow --break-system-packages

RAR を扱うには unrar (または unar) コマンドが別途必要です。
    Windows: https://www.rarlab.com/rar_add.htm から UnRAR をダウンロードし、
             unrar.exe を PATH の通ったフォルダに置く
    Linux:   sudo apt install unrar (または unar)
"""

APP_NAME = "arχveViewer"
APP_VERSION = "0.1.0"

import sys
import subprocess
import os
import re
import json
import time
import zipfile
import io
from pathlib import Path
from datetime import datetime

import numpy as np

from PIL import Image as PILImage

# ============================================================
# デバッグログ設定
# ============================================================
# --windowed でビルドした場合、sys.stdout/sys.stderrはNoneになり、
# print()を呼ぶとAttributeErrorでクラッシュする(コンソールが無いビルドで
# デバッグ用printを入れると危険な理由)。そこで起動時に必ずログファイルへの
# 書き込み用ストリームに置き換え、print()がどのビルド形式でも安全に動き、
# かつ内容がファイルに残るようにする。


class _TeeStream:
    """標準出力/エラーを、元のストリーム(コンソールがあれば)とログファイルの
    両方に書き込む。元のストリームがNone(--windowedビルド)でも安全に動く。"""
    def __init__(self, original, log_file):
        self.original = original
        self.log_file = log_file

    def write(self, text):
        if self.original is not None:
            try:
                self.original.write(text)
            except Exception:
                pass
        try:
            self.log_file.write(text)
        except Exception:
            pass

    def flush(self):
        if self.original is not None:
            try:
                self.original.flush()
            except Exception:
                pass
        try:
            self.log_file.flush()
        except Exception:
            pass


def _get_debug_log_path():
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent
    return base / "debug_log.txt"


def _setup_debug_logging():
    log_path = _get_debug_log_path()
    try:
        log_file = open(log_path, "a", encoding="utf-8", buffering=1)
        log_file.write(
            f"\n\n===== {APP_NAME} {APP_VERSION} 起動: {datetime.now().isoformat()} =====\n"
        )
        sys.stdout = _TeeStream(sys.stdout, log_file)
        sys.stderr = _TeeStream(sys.stderr, log_file)
    except Exception:
        pass  # ログファイルが書けない環境では諦める(通常の動作には影響しない)


_setup_debug_logging()

# デバッグ用の詳細出力([RESIZE DEBUG]/[RENDER DEBUG]等)をON/OFFする状態。
# オプション画面から切り替えられる(既定はOFF: トラブル調査が必要な時だけ有効にする)。
_debug_state = {"enabled": False}


def debug_print(msg):
    """デバッグ用出力の共通口。_debug_state["enabled"]がTrueの間だけ実際にprintする。
    ai_upscale.py側にも同名の仕組みがあり、Options画面から両方まとめて切り替える。"""
    if _debug_state["enabled"]:
        print(msg, flush=True)


from tlg_decoder import decode_tlg
import ai_upscale
from ai_upscale import (
    AIUpscaleWorker, BatchAIUpscaleWorker, is_ai_upscale_available, is_engine_available,
    get_available_engines, get_available_models, get_model_info, ENGINES,
    estimate_noise_level, should_use_ai_upscale, DEFAULT_ENGINE, DEFAULT_MODEL,
    get_openvino_devices, NOISE_THRESHOLD_SKIP_AI, BackendBenchmarkWorker
)

from PySide6.QtWidgets import (
    QApplication, QMainWindow, QLabel, QScrollArea, QVBoxLayout, QWidget,
    QMenu, QDialog, QFormLayout, QCheckBox, QDialogButtonBox,
    QMessageBox, QFileDialog, QKeySequenceEdit, QPushButton, QGroupBox,
    QHBoxLayout, QStackedWidget, QGridLayout, QToolButton, QStyle, QSizePolicy,
    QLineEdit, QButtonGroup, QComboBox, QSpinBox, QTabWidget, QSlider,
    QListWidget, QPlainTextEdit
)
from PySide6.QtGui import (
    QPixmap, QImage, QDragEnterEvent, QDropEvent, QKeySequence,
    QTransform, QPainter, QColor, QIcon, QCursor, QAction
)
from PySide6.QtCore import Qt, QEvent, QTimer, QThread, Signal, QObject, QSize
from PySide6.QtOpenGLWidgets import QOpenGLWidget

try:
    import pyzipper
    HAS_PYZIPPER = True
except ImportError:
    HAS_PYZIPPER = False

try:
    import rarfile
    HAS_RARFILE = True
except ImportError:
    HAS_RARFILE = False

try:
    import py7zr
    HAS_PY7ZR = True
except ImportError:
    HAS_PY7ZR = False

try:
    import keyring
    HAS_KEYRING = True
except ImportError:
    HAS_KEYRING = False


IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp", ".tlg", ".tlg5", ".tlg6"}

def get_app_dir():
    """実行ファイル（.exe化されている場合はexe本体）が置かれているフォルダを返す。
    PyInstallerで--onefile化すると、sys._MEIPASSは一時展開フォルダを指すため、
    そこではなく sys.executable のあるフォルダ（=exeを置いた場所）を使う。
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).parent
    return Path(__file__).parent


if HAS_RARFILE:
    # rarfileは既定でPATH上の"unrar"コマンドを探すが、このアプリと同じフォルダに
    # 置いたunrar.exeはPATHに入っていないため見つからない。明示的にフルパスを
    # 指定する(build.batが同フォルダにunrar.exeをダウンロードして配置する想定)。
    _bundled_unrar = get_app_dir() / "unrar.exe"
    if _bundled_unrar.exists():
        rarfile.UNRAR_TOOL = str(_bundled_unrar)


# パスワードの保存先。「保存していることが分かりにくい」ことを重視し、
# OSの資格情報マネージャー(Windows: 資格情報マネージャー、keyring経由)を
# 優先して使う。サービス名/ユーザー名にも「パスワード」という語を避けている。
_KEYRING_SERVICE = "aXv"
_LEGACY_KEYRING_SERVICE = "arXveViewer"
_KEYRING_USERNAME = "archive_cache"


def load_password_list():
    """パスワード一覧を読み込む。
    まずOSの資格情報マネージャー(keyring)を確認する。無ければ、従来の平文
    ファイル(password_list.txt)から読み込み、その場でkeyringへ移行して
    ファイル自体を削除する(以後は平文ファイルが存在しない状態になる)。
    keyring自体が使えない環境(未対応OS、ライブラリ未導入等)では、
    やむを得ず平文ファイルの読み込みのみにフォールバックする。"""
    if HAS_KEYRING:
        try:
            stored = keyring.get_password(_KEYRING_SERVICE, _KEYRING_USERNAME)
            if stored is None:
                stored = keyring.get_password(_LEGACY_KEYRING_SERVICE, _KEYRING_USERNAME)
                if stored is not None:
                    keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, stored)
            if stored is not None:
                return [line for line in stored.split("\n") if line.strip()]
        except Exception:
            pass  # keyringが使えない実行環境の可能性、下の平文ファイル読み込みへ続ける

    pw_file = get_app_dir() / "password_list.txt"
    passwords = []
    if pw_file.exists():
        lines = pw_file.read_text(encoding="utf-8").splitlines()
        passwords = [line.strip() for line in lines if line.strip()]
        if passwords and HAS_KEYRING:
            try:
                keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, "\n".join(passwords))
                pw_file.unlink()  # 移行できたので、平文ファイルは残さず削除する
            except Exception:
                pass  # 移行に失敗した場合は、平文ファイルをそのまま残す(次回また移行を試みる)
    return passwords


def save_password_list(passwords):
    """パスワード一覧を保存する。keyringが使える場合はそちらにのみ保存する
    (平文ファイルへは書き込まない)。戻り値: 保存できたかどうか。"""
    if not HAS_KEYRING:
        return False
    try:
        keyring.set_password(_KEYRING_SERVICE, _KEYRING_USERNAME, "\n".join(passwords))
        return True
    except Exception:
        return False


# ---- オプション（設定）の保存/読み込み ----
# レジストリを使わず、exeと同じ場所のJSONファイルに保存する
# （「環境を汚さない」という方針に合わせるため）
DEFAULT_SETTINGS = {
    "natural_sort": True,  # ファイル名を自然順(1,2,...,10)でソートするか
    "always_on_top": False,
    "zoom_mode": "100",       # "100" または "fit"、あるいは任意のパーセンテージ文字列
    "aspect_mode": "original",  # "original", "4:3", "16:9"
    "rotation": 0,            # 0, 90, 180, 270（時計回り）
    "keybinds": {},          # {action_id: "Ctrl+O", ...} 未設定分はデフォルト値を使う
    "auto_resize_window": False,  # 画像に合わせてウィンドウサイズを自動調整するか
    "ai_upscale_enabled": False,   # AIアップスケールを使うか
    "ai_upscale_engine": "realesrgan",  # 使用する推論エンジン
    "ai_upscale_model": "realesrgan-x4plus-anime",  # 使うモデル
    "prefetch_window": 2,       # 通常のデコード先読み: 前後何ページ分保持するか
    "ai_prefetch_depth": 5,     # AI処理の先読み: 現在位置から何ページ先まで事前処理するか(-1=アーカイブ全体)
    "vram_mode_enabled": False,  # VRAM展開モード(OpenGLハードウェア描画、実験的)
    "ai_target_mode": "auto",   # "auto"(現在のズーム/画面解像度に合わせる) または "manual"
    "ai_target_width": 1920,    # ai_target_mode="manual"の時の目標幅(px)
    "ai_target_height": 1080,   # ai_target_mode="manual"の時の目標高さ(px)
    "ai_gpu_id": "auto",         # 使うGPUのインデックス("auto"/"0"/"1"/...)。内蔵+外付けGPU環境向け
    "ai_openvino_device": "AUTO",  # OpenVINOエンジン用のデバイス("AUTO"/"CPU"/"GPU"/"NPU"等)
    "ai_backend_rankings": [],
    "ai_benchmark_completed": False,
    "ai_denoise_mode": "auto",    # "off"/"on"/"auto" (Dキーで切替)
    "ai_upscale_mode": "target",  # "off"/"on"/"count"/"target"/"undershoot"/"overshoot" (Uキーで切替)
    "ai_upscale_fixed_count": 1,  # ai_upscale_mode="count"の時の固定パス数
    "fullscreen_exit_mode": "keep",  # 全画面終了時: "keep"(直前のサイズ) / "100"(画像の100%サイズ)
    "ai_diff_based_enabled": False,  # 差分ベースAI高速化(前ページと似ている場合、変化部分だけ処理する)
    "skip_low_res_enabled": False,  # 低解像度画像を先読み/AI処理の対象から除外するか
    "skip_low_res_threshold": 100,  # 幅または高さがこの値未満ならスキップ対象
    "passed_pages_keep_count": 3,  # 現在位置より後ろ(通り過ぎた)のページを、何ページ分まで残すか
    "ai_batch_processing_enabled": True,  # 背景の先読み分を複数枚まとめて1回のプロセス起動で処理するか
    "ai_batch_min_size": 2,  # これ未満の件数ならバッチ化せず単体処理する
}

ASPECT_MODES = ["original", "4:3", "16:9"]
ASPECT_MODE_LABELS = {
    "original": "オリジナル",
    "4:3": "4:3",
    "16:9": "16:9",
}


def get_settings_path():
    return get_app_dir() / "settings.json"


def load_settings():
    path = get_settings_path()
    settings = dict(DEFAULT_SETTINGS)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            settings.update(data)
        except Exception:
            pass  # 壊れていてもデフォルト値で継続
    return settings


def save_settings(settings):
    path = get_settings_path()
    try:
        path.write_text(json.dumps(settings, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def natural_sort_key(name):
    """'page2.png' < 'page10.png' となるよう、数字部分を数値として比較するキーを作る"""
    parts = re.split(r'(\d+)', name)
    return [int(p) if p.isdigit() else p.lower() for p in parts]


# ---- キーバインド設定可能なアクションの一覧 ----
# (action_id, 表示名, デフォルトのキーシーケンス, ハンドラのメソッド名)
# メニュー(拡大縮小/アスペクト比/ページ等)とオプション画面のキーバインド設定、
# 両方がこのリストを共通の元データとして使う。
KEYBINDABLE_ACTIONS = [
    ("open_file",       "開く...",              "Ctrl+O",       "open_archive_dialog"),
    ("next_page",       "次のページ",            "Right",        "next_image"),
    ("prev_page",       "前のページ",            "Left",         "prev_image"),
    ("first_page",      "最初のページ",          "Home",         "first_image"),
    ("last_page",       "最後のページ",          "End",          "last_image"),
    ("zoom_100",        "実際のサイズ(100%)",    "Ctrl+1",       None),  # ハンドラはset_zoom_modeで個別対応
    ("zoom_fit",        "ウィンドウに合わせる",   "Ctrl+0",       None),
    ("zoom_200",        "200%",                  "Ctrl+2",       None),
    ("zoom_300",        "300%",                  "Ctrl+3",       None),
    ("zoom_400",        "400%",                  "Ctrl+4",       None),
    ("zoom_500",        "500%",                  "Ctrl+5",       None),
    ("toggle_fullscreen", "全画面表示切替",       "F11",          "toggle_fullscreen"),
    ("rotate_right",    "右に90度回転",          "R",            "rotate_right"),
    ("rotate_left",     "左に90度回転",          "L",            "rotate_left"),
    ("toggle_aspect",    "アスペクト比切替(順に切替)", "Ctrl+A",  "cycle_aspect_mode"),
    ("toggle_always_on_top", "常に手前に表示切替", "Ctrl+T",       None),  # チェック可能アクションのため個別対応
    ("quit",            "終了",                  "Esc",          "close"),
]

ZOOM_ACTION_IDS = {
    "zoom_100": "100", "zoom_fit": "fit",
    "zoom_200": "200", "zoom_300": "300", "zoom_400": "400", "zoom_500": "500",
}

# KEYBINDABLE_ACTIONSは設定変更可能なものだけを載せている。以下は固定の
# キー/マウス操作(変更不可、主にデバッグ用や直感的な操作)。ショートカット一覧
# タブでは両方をまとめて表示する。
# 注意: next_folder/prev_folder/go_up(上のフォルダへ)には、現時点で
# キーボードショートカットが割り当てられていない(画面クリックまたは
# 右クリックメニュー/ツールバーボタンのみ)。一覧作成時にこれが判明した。
FIXED_SHORTCUTS = [
    ("次の画像へ", "Space"),
    ("デノイズモード切替", "D"),
    ("アップスケールモード切替", "U"),
    ("デバッグ: AI処理前/後の比較表示切替", "O"),
    ("デバッグ: 差分ハイライト表示切替(前ページとの変化領域を色反転)", "H"),
    ("AIキュー/キャッシュをリセットして現在ページから再開", "E"),
    ("前の画像/次の画像", "画面クリック(左上=前/右上=次)"),
    ("前のフォルダ/次のフォルダ", "画面クリック(左下=前/右下=次)　※キー割り当て無し"),
    ("上のフォルダへ", "右クリックメニューまたはツールバーの上ボタン　※キー割り当て無し"),
    ("ダブルクリック", "全画面表示の切替"),
    ("マウスホイール", "拡大/縮小(ズーム)"),
    ("右クリック", "メニューを開く"),
]


def get_keybind(settings, action_id, default):
    return settings.get("keybinds", {}).get(action_id, default)


def human_size(n):
    """バイト数を読みやすい単位（KB/MB等）の文字列にする"""
    if n is None:
        return "不明"
    n = float(n)
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024 or unit == "GB":
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


ZIP_COMPRESSION_NAMES = {
    0: "格納（無圧縮）",
    8: "Deflate",
    12: "Bzip2",
    14: "LZMA",
    99: "AES暗号化（実際の圧縮方式は暗号化ヘッダ内に格納）",
}


def format_entry_date(date_time):
    """ZipInfo/RarInfoのdate_timeタプルを 'YYYY-MM-DD HH:MM:SS' 形式にする"""
    try:
        return "%04d-%02d-%02d %02d:%02d:%02d" % tuple(date_time)
    except Exception:
        return "不明"


def get_pil_image_info(data):
    """PIL経由で画像の詳細情報（フォーマット、カラーモード、DPI、EXIF等）を取得する。
    QImageより詳細なメタデータが得られるため、プロパティ表示用に使う。"""
    try:
        img = PILImage.open(io.BytesIO(data))
        img.load()
        info = {
            "format": img.format,
            "mode": img.mode,
            "size": img.size,
            "dpi": img.info.get("dpi"),
            "icc_profile": "あり" if img.info.get("icc_profile") else "なし",
            "exif_orientation": None,
        }
        try:
            exif = img.getexif()
            if exif:
                info["exif_orientation"] = exif.get(274)  # Orientation タグ
        except Exception:
            pass
        return info
    except Exception:
        return None


class ArchiveReader:
    """ZIP / RAR を透過的に扱うための薄いラッパー。
    image_names: 中の画像ファイル名（拡張子で判定）をソートして保持
    read(name): 指定した画像のバイト列を返す（パスワード試行込み）
    """

    def __init__(self, path: str, passwords, sort_key=None):
        self.path = path
        self.passwords = passwords
        self.sort_key = sort_key  # Noneならデフォルトの文字列ソート
        self.kind = self._detect_kind(path)
        self.image_names = []
        self._working_password = None  # 見つかったパスワードをキャッシュ
        self._zf = None  # 開いたままにするアーカイブハンドル（毎回開き直さないため）
        # 表示用に文字コードを補正した名前 -> zipfile内部で実際に使われている名前、の対応表。
        # 補正が不要な場合(UTF-8フラグが立っている、RAR等)は登録しない。
        self._display_to_raw_name = {}
        self._open_and_list()

    @staticmethod
    def _fix_zip_filename_encoding(zipinfo):
        """Pythonのzipfileは、UTF-8フラグ(汎用ビットフラグのbit 11)が立っていない
        エントリの名前をcp437(IBM PC OEMコードページ)でデコードする。日本語Windows
        環境の古いツールや7-Zip等で作られたZIPは、このフラグが立たないまま
        Shift-JIS(cp932)で名前がエンコードされていることが多く、文字化けの原因になる。
        フラグが立っていない場合のみ、cp437への誤デコードを一度バイト列に戻し、
        cp932で再デコードして正しい名前を復元する(失敗したら元の名前のまま諦める)。"""
        name = zipinfo.filename
        if zipinfo.flag_bits & 0x800:
            return name  # UTF-8フラグが立っている場合は既に正しくデコードされている
        try:
            return name.encode("cp437").decode("cp932")
        except (UnicodeDecodeError, UnicodeEncodeError):
            return name

    def _resolve_raw_name(self, name):
        """表示用に補正した名前から、zipfile内部で実際に使われている(補正前の)
        名前を取得する。補正していない場合はそのまま返す。"""
        return self._display_to_raw_name.get(name, name)

    def _get_open_handle(self):
        """アーカイブを開いたハンドルを返す。既に開いていれば再利用する
        （ページ送りのたびに毎回開き直すと遅いため、プロセス生存中は開いたままにする）。"""
        if self._zf is not None:
            return self._zf
        if self.kind == "zip":
            if self._working_password is not None:
                self._zf = pyzipper.AESZipFile(self.path)
                self._zf.setpassword(self._working_password.encode("utf-8"))
            else:
                self._zf = zipfile.ZipFile(self.path)
        else:
            self._zf = rarfile.RarFile(self.path)
            if self._working_password is not None:
                self._zf.setpassword(self._working_password)
        return self._zf

    def close(self):
        if self._zf is not None:
            try:
                self._zf.close()
            except Exception:
                pass
            self._zf = None

    def _sorted(self, names):
        return sorted(names, key=self.sort_key) if self.sort_key else sorted(names)

    @staticmethod
    def _detect_kind(path):
        p = Path(path)
        if p.is_dir():
            return "folder"
        if p.is_file() and ArchiveReader._is_image(p.name):
            return "single_image"
        # まずマジックバイトで判定する(拡張子が.zip/.rar/.7zでなくても開けるようにするため、
        # 例: .pak拡張子のZIP形式ファイル等)
        kind = detect_archive_kind(p)
        if kind is not None:
            return kind
        # マジックバイト判定に失敗した場合のみ、拡張子でフォールバック判定する
        ext = p.suffix.lower()
        if ext == ".zip":
            return "zip"
        if ext == ".rar":
            return "rar"
        if ext == ".7z":
            return "7z"
        raise ValueError(f"未対応の形式です（ZIP/RAR/7z/フォルダ/画像として認識できませんでした）: {path}")

    def _open_and_list(self):
        if self.kind == "zip":
            self._open_and_list_zip()
        elif self.kind == "rar":
            self._open_and_list_rar()
        elif self.kind == "folder":
            self._open_and_list_folder()
        elif self.kind == "single_image":
            self._open_and_list_single_image()
        elif self.kind == "7z":
            self._open_and_list_7z()
        self._detect_internal_folders()

    def _open_and_list_folder(self):
        """フォルダを、そのまま「無圧縮アーカイブ」として扱う。
        サブフォルダは再帰的に見ず、直下の画像ファイルのみを対象にする
        (サブフォルダがある場合は、複数フォルダ入りZIPと同様に internal_folders の
        仕組みで扱いたいところだが、まずは直下の画像一覧をそのまま見せる簡易実装)。"""
        folder = Path(self.path)
        names = [p.name for p in folder.iterdir() if p.is_file() and self._is_image(p.name)]
        self.image_names = self._sorted(names)

    def _open_and_list_single_image(self):
        """画像ファイル1枚だけを、要素数1の「アーカイブ」として扱う。"""
        self.image_names = [Path(self.path).name]

    def _open_and_list_7z(self):
        """7zアーカイブを開く。ソリッド圧縮の特性上、ZIPほど個別ファイルの
        ランダムアクセスは高速ではない点に注意(読み出しのたびに必要な範囲を
        展開し直す)。パスワード付き7zにも対応する。"""
        if not HAS_PY7ZR:
            raise RuntimeError("7z形式を開くには py7zr が必要です（pip install py7zr）。")

        last_error = None
        for pw in [None] + list(self.passwords):
            try:
                with py7zr.SevenZipFile(self.path, mode="r", password=pw) as zf:
                    names = [n for n in zf.getnames() if self._is_image(n)]
                    self.image_names = self._sorted(names)
                    self._working_password = pw
                    return
            except Exception as e:
                last_error = e
                continue

        raise RuntimeError(f"7zを開けませんでした（パスワード付きの可能性があります）: {last_error}")

    def _detect_internal_folders(self):
        """image_names(ソート済み)から、アーカイブ内部のトップレベルフォルダ構造を
        検出する。1つのアーカイブに複数のフォルダ(例: Chapter1/, Chapter2/)が
        入っている場合、「次のフォルダ」ボタンでZIP内の次のフォルダへ、
        「上に上がる」でZIP内の1つ上の階層へ移動できるようにするための下ごしらえ。
        フラット(フォルダ分けなし)なアーカイブの場合はself.internal_foldersは
        空リストのままになる。"""
        self.internal_folders = []  # フォルダ名(先頭からの相対パス)の並び順リスト
        self.image_folder_index = []  # image_names[i]がどのinternal_foldersに属すか(フラットならNone)

        folder_of = []
        seen_folders = []
        for name in self.image_names:
            if "/" in name:
                folder = name.rsplit("/", 1)[0]
            else:
                folder = None
            folder_of.append(folder)
            if folder is not None and folder not in seen_folders:
                seen_folders.append(folder)

        if len(seen_folders) <= 1:
            # フォルダが無い、または実質1つしか無いなら「フォルダ分けあり」とは扱わない
            self.image_folder_index = [None] * len(self.image_names)
            return

        self.internal_folders = seen_folders
        folder_to_idx = {f: i for i, f in enumerate(seen_folders)}
        self.image_folder_index = [folder_to_idx.get(f) for f in folder_of]

    def get_folder_of_index(self, index):
        """指定した画像インデックスが、アーカイブ内部のどのフォルダ(の番号)に
        属すかを返す。フォルダ分けが無い/該当項目がフォルダ直下ならNone。"""
        if 0 <= index < len(self.image_folder_index):
            return self.image_folder_index[index]
        return None

    def get_first_index_of_folder(self, folder_number):
        """internal_folders[folder_number]の最初の画像のインデックスを返す。
        見つからなければNone。"""
        for i, f in enumerate(self.image_folder_index):
            if f == folder_number:
                return i
        return None

    # ---------- ZIP ----------
    def _open_and_list_zip(self):
        # まず暗号化なしとして通常のzipfileで試す
        try:
            with zipfile.ZipFile(self.path) as zf:
                infos = [info for info in zf.infolist() if self._is_image(info.filename)]
                names = []
                for info in infos:
                    fixed = self._fix_zip_filename_encoding(info)
                    names.append(fixed)
                    if fixed != info.filename:
                        self._display_to_raw_name[fixed] = info.filename
                # パスワード不要かどうか、先頭画像を読んでみて確認
                if names:
                    zf.read(self._resolve_raw_name(names[0]))  # 読めなければ例外が飛ぶ
                self.image_names = self._sorted(names)
                return
        except RuntimeError:
            pass  # 暗号化されている可能性 -> pyzipperで再挑戦
        except Exception:
            pass

        if not HAS_PYZIPPER:
            raise RuntimeError(
                "このZIPはパスワード付きの可能性がありますが、"
                "pyzipperがインストールされていません。"
            )

        last_error = None
        for pw in self.passwords:
            try:
                with pyzipper.AESZipFile(self.path) as zf:
                    zf.setpassword(pw.encode("utf-8"))
                    infos = [info for info in zf.infolist() if self._is_image(info.filename)]
                    names = []
                    self._display_to_raw_name = {}
                    for info in infos:
                        fixed = self._fix_zip_filename_encoding(info)
                        names.append(fixed)
                        if fixed != info.filename:
                            self._display_to_raw_name[fixed] = info.filename
                    if names:
                        zf.read(self._resolve_raw_name(names[0]))  # パスワードが正しいか実際に読んで確認
                    self.image_names = self._sorted(names)
                    self._working_password = pw
                    return
            except Exception as e:
                last_error = e
                continue

        raise RuntimeError(
            f"パスワード付きZIPを開けませんでした（password_list.txtの{len(self.passwords)}件を試行）: {last_error}"
        )

    def _read_zip(self, name):
        return self._get_open_handle().read(self._resolve_raw_name(name))

    # ---------- RAR ----------
    def _open_and_list_rar(self):
        if not HAS_RARFILE:
            raise RuntimeError("rarfileがインストールされていません。pip install rarfile を実行してください。")

        try:
            with rarfile.RarFile(self.path) as rf:
                names = [n for n in rf.namelist() if self._is_image(n)]
                if rf.needs_password():
                    raise rarfile.PasswordRequired("password needed")
                self.image_names = self._sorted(names)
                return
        except rarfile.PasswordRequired:
            pass
        except rarfile.NeedFirstVolume:
            raise RuntimeError("分割RARの最初のボリュームを開いてください。")

        last_error = None
        for pw in self.passwords:
            try:
                with rarfile.RarFile(self.path) as rf:
                    rf.setpassword(pw)
                    names = [n for n in rf.namelist() if self._is_image(n)]
                    if names:
                        rf.read(names[0])
                    self.image_names = self._sorted(names)
                    self._working_password = pw
                    return
            except Exception as e:
                last_error = e
                continue

        raise RuntimeError(
            f"パスワード付きRARを開けませんでした（password_list.txtの{len(self.passwords)}件を試行）: {last_error}"
        )

    def _read_rar(self, name):
        return self._get_open_handle().read(name)

    # ---------- 共通 ----------
    @staticmethod
    def _is_image(name):
        return Path(name).suffix.lower() in IMAGE_EXTENSIONS and not name.endswith("/")

    def read(self, name):
        if self.kind == "zip":
            return self._read_zip(name)
        elif self.kind == "rar":
            return self._read_rar(name)
        elif self.kind == "folder":
            return (Path(self.path) / name).read_bytes()
        elif self.kind == "single_image":
            return Path(self.path).read_bytes()
        elif self.kind == "7z":
            return self._read_7z(name)
        raise ValueError(f"未対応の種別です: {self.kind}")

    def _read_7z(self, name):
        import tempfile
        with py7zr.SevenZipFile(self.path, mode="r", password=self._working_password) as zf:
            with tempfile.TemporaryDirectory(prefix="archive_viewer_7z_") as tmpdir:
                zf.extract(path=tmpdir, targets=[name])
                return (Path(tmpdir) / name).read_bytes()

    def read_independent(self, name):
        """バックグラウンドスレッドからの先読み用。self._zf(メインスレッドが使う
        永続ハンドル)には触れず、専用に新しいハンドルを開いて読む。
        zipfile/rarfileのハンドルはスレッド安全ではないため、共有しないことで
        競合を避ける（先読み1回だけの一時オープンなので、コストは許容範囲）。"""
        if self.kind == "zip":
            raw_name = self._resolve_raw_name(name)
            if self._working_password is not None:
                with pyzipper.AESZipFile(self.path) as zf:
                    zf.setpassword(self._working_password.encode("utf-8"))
                    return zf.read(raw_name)
            with zipfile.ZipFile(self.path) as zf:
                return zf.read(raw_name)
        elif self.kind == "rar":
            with rarfile.RarFile(self.path) as rf:
                if self._working_password is not None:
                    rf.setpassword(self._working_password)
                return rf.read(name)
        elif self.kind == "folder":
            return (Path(self.path) / name).read_bytes()
        elif self.kind == "single_image":
            return Path(self.path).read_bytes()
        elif self.kind == "7z":
            return self._read_7z(name)  # 7zは内部でハンドルを都度開くので、共有ハンドルの心配は元々無い
        raise ValueError(f"未対応の種別です: {self.kind}")

    def get_entry_info(self, name):
        """アーカイブ内エントリのメタデータ（圧縮方式、サイズ、CRC、更新日時等）を取得する。
        ZIP/RARどちらも zipfile 互換の getinfo() スタイルAPIを持つため、共通ロジックで扱える。
        folder/single_image/7zは、呼び出し側(プロパティダイアログ等)がNoneを
        想定通り扱える設計になっているため、詳細情報が無い場合は素直にNoneを返す。"""
        if self.kind in ("folder", "single_image"):
            return None  # ファイルシステムのstat情報を別途取るのは呼び出し側の対応が必要なため今回は省略
        if self.kind == "7z":
            try:
                with py7zr.SevenZipFile(self.path, mode="r", password=self._working_password) as zf:
                    for entry in zf.list():
                        if entry.filename == self._resolve_raw_name(name):
                            return entry
            except Exception:
                return None
            return None
        try:
            return self._get_open_handle().getinfo(self._resolve_raw_name(name))
        except Exception:
            return None

    def get_total_uncompressed_size(self):
        """アーカイブ内の全画像の、展開後サイズの合計を返す(バイト数)。
        取得できないエントリがあっても、できる範囲で合計する。"""
        if self.kind == "folder":
            total = 0
            for name in self.image_names:
                try:
                    total += (Path(self.path) / name).stat().st_size
                except Exception:
                    continue
            return total
        if self.kind == "single_image":
            try:
                return Path(self.path).stat().st_size
            except Exception:
                return None
        if self.kind == "7z":
            total = 0
            try:
                with py7zr.SevenZipFile(self.path, mode="r", password=self._working_password) as zf:
                    for entry in zf.list():
                        if self._is_image(entry.filename):
                            total += getattr(entry, "uncompressed", 0) or 0
            except Exception:
                return None
            return total

        total = 0
        try:
            handle = self._get_open_handle()
            for name in self.image_names:
                try:
                    info = handle.getinfo(self._resolve_raw_name(name))
                    total += getattr(info, "file_size", 0) or 0
                except Exception:
                    continue
        except Exception:
            return None
        return total


class PrefetchWorker(QObject):
    """隣接ページをバックグラウンドで先読み・デコードするワーカー。
    QThread上で動かし、結果はシグナル経由でメインスレッドに渡す
    （QImage/QPixmapはメインスレッド以外での生成・操作が問題になることがあるため、
    デコードはこのワーカースレッド内で行うが、結果の保存・表示はメインスレッド側で行う）。
    """
    decoded = Signal(str, int, object, QImage)  # (archive_path, index, raw_data, image)
    failed = Signal(str, int)  # (archive_path, index)

    def __init__(self, reader, index, name, archive_path):
        super().__init__()
        self.reader = reader
        self.index = index
        self.name = name
        self.archive_path = archive_path

    def run(self):
        try:
            self._run()
        except Exception as exc:
            debug_print(f"[DECODE FAILED] page={self.index}: {exc}")
            self.failed.emit(self.archive_path, self.index)

    def _run(self):
        try:
            data = self.reader.read_independent(self.name)
        except Exception:
            self.failed.emit(self.archive_path, self.index)
            return

        image = QImage()
        if not image.loadFromData(data):
            tlg_image = decode_tlg(data)
            if tlg_image is not None:
                image = tlg_image
            else:
                self.failed.emit(self.archive_path, self.index)
                return

        self.decoded.emit(self.archive_path, self.index, data, image)


class ThumbnailWorker(QObject):
    """アーカイブ内の指定ページ(既定は最初)を読み込み、デコードするワーカー。
    フォルダ一覧のサムネイル用途と、隣接アーカイブの境界ページの先読み用途、
    両方で使う。QImageで受け渡す(QPixmapはGUIスレッド以外での生成が安全でないため)。"""
    loaded = Signal(str, QImage, bytes, str)  # (path, image, raw_data, entry_name)
    failed = Signal(str)

    def __init__(self, path, passwords, which="first", sort_key=None):
        """which: "first"(最初のページ) または "last"(最後のページ)
        sort_key: 本体側のArchiveReaderと同じソート順を使うために渡す
        (自然順ソート設定が有効な場合、渡さないと文字列ソートとの食い違いで
        「最初/最後のページ」の判定がズレるバグになる)。"""
        super().__init__()
        self.path = path
        self.passwords = passwords
        self.which = which
        self.sort_key = sort_key

    def run(self):
        try:
            reader = ArchiveReader(self.path, self.passwords, sort_key=self.sort_key)
            if not reader.image_names:
                reader.close()
                self.failed.emit(self.path)
                return
            target_name = reader.image_names[0] if self.which == "first" else reader.image_names[-1]
            data = reader.read_independent(target_name)
            reader.close()
        except Exception:
            self.failed.emit(self.path)
            return

        image = QImage()
        if not image.loadFromData(data):
            tlg_image = decode_tlg(data)
            if tlg_image is None:
                self.failed.emit(self.path)
                return
            image = tlg_image

        self.loaded.emit(self.path, image, data, target_name)


def detect_archive_kind(path):
    """拡張子に関係なく、ファイル先頭のマジックバイトでZIP/RAR/7zかどうかを判定する。
    どれでもなければNoneを返す。"""
    try:
        with open(path, "rb") as f:
            header = f.read(8)
    except Exception:
        return None
    if header.startswith(b"PK\x03\x04") or header.startswith(b"PK\x05\x06") or header.startswith(b"PK\x07\x08"):
        return "zip"
    if header.startswith(b"Rar!\x1a\x07"):
        return "rar"
    if header.startswith(b"7z\xbc\xaf\x27\x1c"):
        return "7z"
    return None


THUMBNAIL_SIZE = 96
TILE_SIZE = QSize(132, 128)


class ExplorerTile(QToolButton):
    """フォルダ一覧の1タイル。シングルクリック=選択、ダブルクリック=開く、
    右クリック=専用コンテキストメニュー、という挙動にするためQToolButtonを拡張する。"""
    activated = Signal()          # ダブルクリック(または選択後のEnter等)で「開く」
    right_clicked = Signal(object)  # 右クリック、引数はグローバル座標(QPoint)

    def __init__(self, entry_path, entry_kind, parent=None):
        super().__init__(parent)
        self.entry_path = entry_path  # Path
        self.entry_kind = entry_kind  # "folder" または "zip"/"rar"
        self.setCheckable(True)  # 選択状態の視覚表現に使う

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.activated.emit()
        super().mouseDoubleClickEvent(event)

    def contextMenuEvent(self, event):
        self.setChecked(True)  # 右クリック時もそのタイルを選択状態にする
        self.right_clicked.emit(event.globalPos())


class FolderBrowserWidget(QWidget):
    """フォルダ内のZIP/RAR(サムネイル付き、拡張子は無視してマジックバイトで判定)と
    サブフォルダを、アイコン表示のグリッドで見せるビュー。
    シングルクリック=選択、ダブルクリック=開く、右クリック=専用メニュー。"""
    archive_activated = Signal(str)
    folder_activated = Signal(str)
    # 右クリックメニュー用: (path_str, kind) kindは"folder"/"zip"/"rar"
    tile_context_menu_requested = Signal(str, str, object)

    ICON_SIZES = {
        "small":  {"thumb": 48,  "tile": QSize(96, 88)},
        "medium": {"thumb": 96,  "tile": QSize(132, 128)},
        "large":  {"thumb": 144, "tile": QSize(180, 176)},
        "xlarge": {"thumb": 240, "tile": QSize(280, 280)},  # 4K等の高解像度ディスプレイ向け
    }

    def __init__(self, passwords, parent=None):
        super().__init__(parent)
        self.passwords = passwords
        self.current_path = None
        self.icon_size_level = "medium"

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setStyleSheet("background-color: white; border: none;")
        outer.addWidget(self.scroll)

        self.container = QWidget()
        self.grid = QGridLayout(self.container)
        self.grid.setSpacing(10)
        self.grid.setAlignment(Qt.AlignTop | Qt.AlignLeft)
        self.scroll.setWidget(self.container)

        self.button_group = QButtonGroup(self)
        self.button_group.setExclusive(True)  # クリックで選択、他は自動的に選択解除

        self._thumbnail_threads = {}  # path -> (QThread, ThumbnailWorker) 現在のフォルダ表示分
        self._retiring_threads = []   # まだ終わっていないが表示からは外れた分(参照保持だけ目的)
        self._buttons_by_path = {}    # path -> ExplorerTile (サムネイル反映用)

    ICON_SIZE_ORDER = ["small", "medium", "large", "xlarge"]

    def set_icon_size(self, level):
        """アイコンサイズを変更する(小/中/大/特大)。現在のフォルダを同じ大きさで再表示する。"""
        if level not in self.ICON_SIZES or level == self.icon_size_level:
            return
        self.icon_size_level = level
        if self.current_path:
            self.show_folder(self.current_path)

    def zoom_icon_size(self, delta):
        """アイコンサイズを1段階だけ拡大(delta=+1)/縮小(delta=-1)する。
        既に最大/最小の場合は何もしない。"""
        idx = self.ICON_SIZE_ORDER.index(self.icon_size_level)
        new_idx = max(0, min(len(self.ICON_SIZE_ORDER) - 1, idx + delta))
        if new_idx != idx:
            self.set_icon_size(self.ICON_SIZE_ORDER[new_idx])

    def show_folder(self, folder_path):
        self.current_path = folder_path
        self._clear()

        try:
            entries = sorted(
                Path(folder_path).iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower())
            )
        except Exception:
            entries = []

        col_count = 5
        row = col = 0

        for entry in entries:
            try:
                is_dir = entry.is_dir()
            except OSError:
                continue

            if is_dir:
                btn = self._make_tile(entry, "folder")
            elif entry.is_file():
                # 拡張子は無視し、ファイル先頭のマジックバイトでZIP/RAR/7zかどうかを判定する
                kind = detect_archive_kind(entry)
                if kind is None:
                    # アーカイブでは無くても、画像ファイル単体なら「1枚アーカイブ」として開けるようにする
                    if entry.suffix.lower() in IMAGE_EXTENSIONS:
                        kind = "single_image"
                    else:
                        continue
                btn = self._make_tile(entry, kind)
            else:
                continue

            self.grid.addWidget(btn, row, col)
            col += 1
            if col >= col_count:
                col = 0
                row += 1

    def _clear(self):
        # 進行中のサムネイル読み込みスレッドは、参照を失う前にきちんと停止させる。
        # wait()がタイムアウトした場合は_retiring_threadsに移して参照を保持し続け、
        # スレッド自身のfinishedシグナルによる自然な後始末に任せる。
        # (同じパスを再度スキャンした際に、辞書のキー衝突で参照を上書き＆消失させないため
        # 「今のフォルダ用」の辞書とは別のリストで持つ)
        for path_str, (thread, worker) in list(self._thumbnail_threads.items()):
            thread.quit()
            if thread.wait(500):
                thread.deleteLater()
                worker.deleteLater()
            else:
                self._retiring_threads.append((thread, worker))
                thread.finished.connect(lambda t=thread, w=worker: self._cleanup_retiring_thread(t, w))
        self._thumbnail_threads = {}

        self._buttons_by_path.clear()
        while self.grid.count():
            item = self.grid.takeAt(0)
            w = item.widget()
            if w is not None:
                self.button_group.removeButton(w)
                w.deleteLater()

    def _cleanup_retiring_thread(self, thread, worker):
        try:
            self._retiring_threads.remove((thread, worker))
        except ValueError:
            pass
        thread.deleteLater()
        worker.deleteLater()

    def _tile_qss(self):
        return """
            QToolButton { border: 1px solid transparent; border-radius: 4px; }
            QToolButton:hover { background: #f0f0f0; }
            QToolButton:checked { background: #cce4ff; border: 1px solid #99c7ff; }
        """

    def _make_tile(self, path: Path, kind: str):
        sizes = self.ICON_SIZES[self.icon_size_level]
        btn = ExplorerTile(path, kind)
        btn.setToolButtonStyle(Qt.ToolButtonTextUnderIcon)
        btn.setIconSize(QSize(sizes["thumb"], sizes["thumb"]))
        btn.setFixedSize(sizes["tile"])
        btn.setStyleSheet(self._tile_qss())

        if kind == "folder":
            btn.setIcon(self.style().standardIcon(QStyle.SP_DirIcon))
            btn.setText(path.name)
            btn.setToolTip(str(path))
            btn.activated.connect(lambda: self.folder_activated.emit(str(path)))
        else:
            btn.setIcon(self.style().standardIcon(QStyle.SP_FileIcon))  # 読み込み中の仮アイコン
            btn.setText(path.stem)  # 表示には拡張子は不要(ツールチップにはフルネームを残す)
            btn.setToolTip(path.name)
            btn.activated.connect(lambda: self.archive_activated.emit(str(path)))
            self._buttons_by_path[str(path)] = btn
            self._start_thumbnail_load(str(path))

        self.button_group.addButton(btn)
        btn.right_clicked.connect(
            lambda gpos, p=str(path), k=kind: self.tile_context_menu_requested.emit(p, k, gpos)
        )
        return btn

    def _start_thumbnail_load(self, path_str):
        thread = QThread(self)
        worker = ThumbnailWorker(path_str, self.passwords)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.loaded.connect(self._on_thumbnail_loaded)
        worker.failed.connect(self._on_thumbnail_failed)
        worker.loaded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(lambda: self._cleanup_thumb_thread(path_str))

        self._thumbnail_threads[path_str] = (thread, worker)
        thread.start()

    def _cleanup_thumb_thread(self, path_str):
        entry = self._thumbnail_threads.pop(path_str, None)
        if entry is not None:
            thread, worker = entry
            thread.deleteLater()
            worker.deleteLater()

    def _on_thumbnail_loaded(self, path_str, image, raw_data, entry_name):
        btn = self._buttons_by_path.get(path_str)
        if btn is None:
            return  # 既に別フォルダに移動済みなどで、このボタンは破棄されている
        thumb_px = self.ICON_SIZES[self.icon_size_level]["thumb"]
        pixmap = QPixmap.fromImage(image).scaled(
            thumb_px, thumb_px, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        btn.setIcon(QIcon(pixmap))

    def _on_thumbnail_failed(self, path_str):
        pass  # 仮アイコンのままにする


class ImageViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(f"{APP_NAME} {APP_VERSION}")
        self.resize(900, 700)
        self.setAcceptDrops(True)

        self.passwords = load_password_list()
        self.settings = load_settings()
        self.reader = None
        self.index = 0
        self.current_archive_path = None
        self._reader_generation = 0
        self._prefetch_failed_indices = set()
        self._archive_generation = 0  # open_archive()のたびに+1。AIジョブ発行時の世代を記録しておき、
                                       # 結果が返ってきた時点の世代と食い違えば(=別のアーカイブに
                                       # 切り替わった後の遅延到着)、その結果は捨てる。
        self._ai_job_generation = {}  # cache_key -> 発行時の_archive_generation

        self.zoom_mode = self.settings.get("zoom_mode", "100")
        self.aspect_mode = self.settings.get("aspect_mode", "original")
        self.rotation = self.settings.get("rotation", 0)
        self.auto_resize_window = self.settings.get("auto_resize_window", False)
        self.vram_mode_enabled = self.settings.get("vram_mode_enabled", False)

        # 「ウィンドウに合わせる」モードでの手動リサイズ後、余白を消す方向に
        # ウィンドウサイズを微調整するためのデバウンスタイマー
        # (リサイズが連続して来ている間は待ち、止まってから一度だけ実行する)
        self._fit_resize_debounce_timer = QTimer(self)
        self._fit_resize_debounce_timer.setSingleShot(True)
        self._fit_resize_debounce_timer.timeout.connect(self._snap_fit_window_to_remove_margin)
        self.ai_target_mode = self.settings.get("ai_target_mode", "auto")
        self.ai_target_width = self.settings.get("ai_target_width", 1920)
        self.ai_target_height = self.settings.get("ai_target_height", 1080)
        self.ai_gpu_id = self.settings.get("ai_gpu_id", "auto")
        self.ai_openvino_device = self.settings.get("ai_openvino_device", "AUTO")
        self.ai_backend_rankings = self.settings.get("ai_backend_rankings", [])
        self._benchmark_thread_ref = None
        self._benchmark_rank_label = None
        self._benchmark_engine_combo = None
        self._benchmark_model_combo = None
        self._benchmark_openvino_device_combo = None
        self.ai_denoise_mode = self.settings.get("ai_denoise_mode", "auto")
        self.ai_upscale_mode = self.settings.get("ai_upscale_mode", "target")
        self.ai_upscale_fixed_count = self.settings.get("ai_upscale_fixed_count", 1)
        self.fullscreen_exit_mode = self.settings.get("fullscreen_exit_mode", "keep")
        self.ai_diff_based_enabled = self.settings.get("ai_diff_based_enabled", False)
        self._pending_diff_composite = {}  # cache_key -> {"base_image":..., "region_scaled":...}

        # ---- AIアップスケール関連 ----
        self.ai_upscale_enabled = self.settings.get("ai_upscale_enabled", False)
        self.ai_upscale_engine = self.settings.get("ai_upscale_engine", DEFAULT_ENGINE)
        self.ai_upscale_model = self.settings.get("ai_upscale_model", DEFAULT_MODEL)
        self._ai_cache = {}   # index -> ((rotation, aspect_mode), QPixmap) 先読み結果も含む
        self._ai_pending_key = None   # 現在バックグラウンドで処理中の状態(重複リクエスト防止用)
        self._closing = False
        self._ai_failed_keys = set()
        self._ai_queue = []            # [(QImage, cache_key, passes), ...] 待機中のAIジョブ
        self._ai_thread_ref = None    # (QThread, AIUpscaleWorker) 実行中スレッドの参照保持用
        self._batch_pending_keys = set()  # バッチ処理中の全cache_key(単体処理の_ai_pending_keyとは別枠)
        self._batch_pending_entries = []  # バッチ処理中の全エントリ(キャンセル時に再キューするため保持)
        self._batch_failure_count = {}  # cache_key -> バッチ失敗回数(無限リトライを避けるため)
        self._batch_thread_ref = None     # (QThread, BatchAIUpscaleWorker) 実行中バッチの参照保持用
        self._ai_processing_start_time = {}  # cache_key -> AI処理開始時刻(デバッグ情報表示用)
        self._last_ai_processing_seconds = None  # 現在ページの直近のAI処理所要時間(秒)
        self.ai_batch_processing_enabled = self.settings.get("ai_batch_processing_enabled", True)
        self.ai_batch_min_size = self.settings.get("ai_batch_min_size", 2)  # これ未満ならバッチ化しない
        self.current_noise_level = None  # 現在のページのノイズレベル推定値(キャッシュ)
        self.last_ai_error = None  # 直近のAI処理失敗の詳細(プロパティダイアログで確認用)
        self.debug_show_pre_ai = False  # oキーで切替: True中はAI処理前の画像を強制的に表示する
        self.debug_show_diff_highlight = False  # hキーで切替: 前ページとの差分領域を色反転表示する
        self.skip_low_res_enabled = self.settings.get("skip_low_res_enabled", False)
        self._nav_direction = 0  # next_image/prev_imageから来た時だけ1/-1、それ以外は0(スキップしない)
        self.skip_low_res_threshold = self.settings.get("skip_low_res_threshold", 100)
        self.passed_pages_keep_count = self.settings.get("passed_pages_keep_count", 3)
        self._waiting_for_index = None  # 「読み込み中」表示のまま非同期デコード待ちのページ番号

        # 保存されているデバッグログの設定を、起動時にviewer.py/ai_upscale.py
        # 両方の状態に反映する(既定はOFF)。
        _debug_state["enabled"] = self.settings.get("debug_logging_enabled", False)
        ai_upscale._debug_state["enabled"] = self.settings.get("debug_logging_enabled", False)
        self._noise_cache_key = None      # ノイズレベルがどのページ分か(indexのみで判定)
        self.original_pixmap = None  # デコードしたそのままの画像（回転・レターボックス前）
        # プロパティ表示用にキャッシュしておく情報（画像を切り替えるたびに更新）
        self.current_entry_info = None
        self.current_pil_info = None
        self.current_qimage_depth = None
        self.current_qimage_has_alpha = None
        self.current_data_size = None
        self.current_raw_data = None

        # ---- 先読み(プリフェッチ)関連 ----
        # 表示は即座に行い、隣接ページの読み込み・デコードは裏で進める。
        self._image_cache = {}       # index -> QImage (デコード済み、まだoriginal_pixmap化前)
        self._active_prefetch_threads = {}  # index -> (QThread, PrefetchWorker)
        self._sibling_prefetch = {}   # "next"/"prev" -> {"path":str, "data":bytes, "image":QImage}
        self._sibling_prefetch_threads = {}  # "next"/"prev" -> (QThread, ThumbnailWorker)
        self._prefetch_window = self.settings.get("prefetch_window", 2)  # 現在位置から前後何ページまでキャッシュを保持するか
        self.ai_prefetch_depth = self.settings.get("ai_prefetch_depth", 5)  # AI処理を何ページ先まで先読みするか

        # ---- ナビゲーション履歴（戻る/進む/上へ） ----
        self.current_folder_path = None
        self.history = []       # [{'type': 'archive'|'folder', 'path': str}, ...]
        self.history_index = -1

        # スクロール可能な表示エリア（画像のない領域は常に黒にする）
        self.scroll_area = QScrollArea()
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.setStyleSheet("background-color: black; border: none;")
        self.image_label = QLabel("ZIP / RAR ファイルをドラッグ&ドロップしてください")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setStyleSheet("background-color: black; color: white;")
        self.scroll_area.setWidget(self.image_label)
        # setWidgetResizable(False)にする: Trueだと、Qtがimage_labelを常にビューポート
        # いっぱいに引き伸ばそうとし、render_current_pixmap側で手動設定するサイズ
        # (fitモードでのアスペクト比維持、100%以上でのスクロール用サイズ等)と競合して
        # しまい、スクロールバーの出現/消失を誘発 → ビューポートサイズが微妙に変化
        # → 「ウィンドウに合わせる」の余白解消処理が誤判定を繰り返す、という
        # ウィンドウがどんどん縮むバグの原因になっていた。Falseにすることで、
        # image_labelの実サイズは常にこちら側の計算通りになり、AlignCenterの設定も
        # (widgetResizable=Falseの時だけ効果を持つため)正しく機能するようになる。
        self.scroll_area.setWidgetResizable(False)
        self._update_scrollbar_policy()

        if self.vram_mode_enabled:
            self._apply_vram_mode(True)

        # フォルダ一覧ビュー（「上へ」でアーカイブ/画像から切り替える）
        self.folder_browser = FolderBrowserWidget(self.passwords)
        self.folder_browser.archive_activated.connect(self.navigate_to_archive)
        self.folder_browser.folder_activated.connect(self.navigate_to_folder)
        self.folder_browser.tile_context_menu_requested.connect(self._show_tile_context_menu)

        self.stacked = QStackedWidget()
        self.stacked.addWidget(self.scroll_area)     # index 0: 画像表示
        self.stacked.addWidget(self.folder_browser)   # index 1: フォルダ一覧

        # ---- ナビゲーションツールバー（戻る/進む/上へ + 現在のパス） ----
        self.back_button = QToolButton()
        self.back_button.setIcon(self.style().standardIcon(QStyle.SP_ArrowBack))
        self.back_button.setToolTip("戻る")
        self.back_button.setAutoRaise(True)
        self.back_button.setIconSize(QSize(20, 20))

        self.forward_button = QToolButton()
        self.forward_button.setIcon(self.style().standardIcon(QStyle.SP_ArrowForward))
        self.forward_button.setToolTip("進む")
        self.forward_button.setAutoRaise(True)
        self.forward_button.setIconSize(QSize(20, 20))

        self.up_button = QToolButton()
        self.up_button.setIcon(self.style().standardIcon(QStyle.SP_ArrowUp))
        self.up_button.setToolTip("上のフォルダへ")
        self.up_button.setAutoRaise(True)
        self.up_button.setIconSize(QSize(20, 20))

        for btn in (self.back_button, self.forward_button, self.up_button):
            btn.setFixedSize(32, 28)
            btn.setStyleSheet("""
                QToolButton { border: none; border-radius: 4px; background: transparent; }
                QToolButton:hover:!disabled { background: #e0e0e0; }
                QToolButton:pressed { background: #cfcfcf; }
            """)

        self.back_button.clicked.connect(self.go_back)
        self.forward_button.clicked.connect(self.go_forward)
        self.up_button.clicked.connect(self.go_up)

        self.path_label = QLineEdit("")
        self.path_label.setStyleSheet(
            "QLineEdit { color: #333; padding-left: 10px; border: 1px solid transparent; background: transparent; }"
            "QLineEdit:focus { border: 1px solid #999; background: white; }"
        )
        self.path_label.returnPressed.connect(self._on_path_bar_entered)

        self.nav_bar = QWidget()
        self.nav_bar.setStyleSheet("background-color: #f2f2f2; border-bottom: 1px solid #ddd;")
        nav_layout = QHBoxLayout(self.nav_bar)
        nav_layout.setContentsMargins(8, 4, 8, 4)
        nav_layout.setSpacing(4)
        nav_layout.addWidget(self.back_button)
        nav_layout.addWidget(self.forward_button)
        nav_layout.addWidget(self.up_button)
        nav_layout.addWidget(self.path_label, stretch=1)

        central = QWidget()
        central_layout = QVBoxLayout(central)
        central_layout.setContentsMargins(0, 0, 0, 0)
        central_layout.setSpacing(0)
        central_layout.addWidget(self.nav_bar)
        central_layout.addWidget(self.stacked)

        self.setCentralWidget(central)
        self._update_nav_buttons()

        # ---- OSD(オンスクリーン表示): D/Uキーでのモード切替を1秒間表示する ----
        # ナビバー/ステータスバーは全画面時に隠すため、それらとは独立した専用の
        # フローティングラベルとして中央ウィジェットの上に重ねて表示する。
        self.osd_label = QLabel("", central)
        self.osd_label.setStyleSheet(
            "background-color: rgba(0, 0, 0, 180); color: white;"
            "padding: 10px 18px; border-radius: 6px; font-size: 14px;"
        )
        self.osd_label.setAlignment(Qt.AlignCenter)
        self.osd_label.hide()
        self._osd_timer = QTimer(self)
        self._osd_timer.setSingleShot(True)
        self._osd_timer.timeout.connect(self.osd_label.hide)

        # ---- デバッグ情報ラベル: デバッグログ有効時、画面左上の余白に
        # 現在ページの「元データ量→現在のデータ量、AI処理にかかった時間」を
        # 常時表示する(OSDと違いタイマーで消えない、ページが変わるたびに更新)。
        self.debug_info_label = QLabel("", central)
        self.debug_info_label.setStyleSheet(
            "background-color: rgba(0, 0, 0, 160); color: #7CFC7C;"
            "padding: 6px 10px; border-radius: 4px; font-size: 12px; font-family: monospace;"
        )
        self.debug_info_label.hide()

        # ---- ページ送りスライダー ----
        # 通常時: ステータスバーの領域を活用する(AI処理のコメント表示と同じ場所)。
        self.page_slider = QSlider(Qt.Horizontal)
        self.page_slider.setMinimum(0)
        self.page_slider.setMaximum(0)
        self.page_slider.setFixedWidth(240)
        self.page_slider.sliderMoved.connect(self._on_page_slider_moved)
        self.page_slider.sliderReleased.connect(self._on_page_slider_released)
        self.statusBar().addPermanentWidget(self.page_slider)

        # 全画面時: ステータスバー自体を隠すため、独立したフローティングスライダーを
        # 画面下端に用意し、マウスが下端に近づいた時だけ半透明で表示する。
        self.fullscreen_slider = QSlider(Qt.Horizontal, central)
        self.fullscreen_slider.setMinimum(0)
        self.fullscreen_slider.setMaximum(0)
        self.fullscreen_slider.setStyleSheet(
            "QSlider::groove:horizontal { background: rgba(255,255,255,60); height: 6px; }"
            "QSlider::handle:horizontal { background: rgba(255,255,255,200); width: 14px; margin: -4px 0; border-radius: 7px; }"
        )
        self.fullscreen_slider.sliderMoved.connect(self._on_page_slider_moved)
        self.fullscreen_slider.sliderReleased.connect(self._on_page_slider_released)
        self.fullscreen_slider.hide()
        self.setMouseTracking(True)
        self._fullscreen_slider_check_timer = QTimer(self)
        self._last_cursor_pos = None
        self._cursor_idle_ticks = 0
        self._fullscreen_slider_check_timer.timeout.connect(self._check_fullscreen_slider_hover)

        # ドラッグ中に表示するサムネイルプレビュー
        self.thumb_preview_label = QLabel("", central)
        self.thumb_preview_label.setStyleSheet(
            "background-color: rgba(0, 0, 0, 200); border: 1px solid rgba(255,255,255,80);"
        )
        self.thumb_preview_label.hide()

        # ---- クリック領域でのページ/フォルダ送り、ダブルクリックで最大化 ----
        # 画像の左上/右上/左下/右下クリックで前後の画像・フォルダに移動する
        # (マンガビューア等で一般的なUI)。ビューポート全体を4分割して判定する。
        self._pending_click_pos = None
        self._pan_start_pos = None       # 中ボタンドラッグでのパン操作用
        self._pan_start_scroll = (0, 0)
        self.scroll_area.viewport().installEventFilter(self)

        # ---- キーバインド可能なアクションを作成（ウィンドウに直接ぶら下げる） ----
        # これにより、右クリックメニューを開いていない時でもショートカットキーが機能する。
        # メニュー側はここで作ったアクションを再利用するので、表示上のショートカット文言と
        # 実際の動作が常に一致する。
        self.actions_by_id = {}
        self._build_keybindable_actions()

        # ---- 右クリックメニュー ----
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.show_context_menu)

        # 前回終了時の「常に手前に表示」設定を復元
        if self.settings.get("always_on_top", False):
            self.setWindowFlags(self.windowFlags() | Qt.WindowStaysOnTopHint)

        if not self.settings.get("ai_benchmark_completed", False):
            QTimer.singleShot(1500, self._start_backend_benchmark)

    def _format_backend_rankings(self):
        if not self.ai_backend_rankings:
            return "未測定（AIモデルを配置すると測定できます）"
        lines = []
        rank = 0
        for item in self.ai_backend_rankings:
            if item.get("ok"):
                rank += 1
                lines.append(f"{rank}位: {item['label']} — {item['seconds']:.3f}秒")
            else:
                lines.append(f"除外: {item['label']} — {item.get('error', '利用不可')}")
        return "\n".join(lines)

    def _start_backend_benchmark(self, force=False):
        if self._benchmark_thread_ref is not None:
            return
        if not force and self.settings.get("ai_benchmark_completed", False):
            return
        if not is_ai_upscale_available():
            return
        if self._benchmark_rank_label is not None:
            self._benchmark_rank_label.setText("測定中…")
        thread = QThread(self)
        worker = BackendBenchmarkWorker()
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_backend_benchmark_finished)
        worker.finished.connect(thread.quit)
        thread.finished.connect(lambda: self._cleanup_benchmark_thread(thread, worker))
        self._benchmark_thread_ref = (thread, worker)
        thread.start()

    def _on_backend_benchmark_finished(self, results):
        self.ai_backend_rankings = list(results)
        successful = [item for item in results if item.get("ok")]
        self.settings["ai_backend_rankings"] = self.ai_backend_rankings
        self.settings["ai_benchmark_completed"] = bool(successful)
        if successful:
            best = successful[0]
            previous_engine = self.ai_upscale_engine
            self.ai_upscale_engine = best["engine"]
            self.ai_upscale_model = best["model"]
            self.settings["ai_upscale_engine"] = self.ai_upscale_engine
            self.settings["ai_upscale_model"] = self.ai_upscale_model
            if best["engine"] == "openvino" and best.get("device"):
                self.ai_openvino_device = best["device"]
                self.settings["ai_openvino_device"] = self.ai_openvino_device
            if previous_engine != self.ai_upscale_engine and self.reader is not None:
                self._invalidate_ai_work()
        save_settings(self.settings)
        if self._benchmark_rank_label is not None:
            self._benchmark_rank_label.setText(self._format_backend_rankings())
        if self._benchmark_engine_combo is not None:
            index = self._benchmark_engine_combo.findData(self.ai_upscale_engine)
            if index >= 0:
                self._benchmark_engine_combo.setCurrentIndex(index)
                model_index = self._benchmark_model_combo.findData(self.ai_upscale_model)
                if model_index >= 0:
                    self._benchmark_model_combo.setCurrentIndex(model_index)
        if self._benchmark_openvino_device_combo is not None:
            index = self._benchmark_openvino_device_combo.findData(self.ai_openvino_device)
            if index >= 0:
                self._benchmark_openvino_device_combo.setCurrentIndex(index)

    def _cleanup_benchmark_thread(self, thread, worker):
        if self._benchmark_thread_ref == (thread, worker):
            self._benchmark_thread_ref = None
        worker.deleteLater()
        thread.deleteLater()

    def _build_keybindable_actions(self):
        """KEYBINDABLE_ACTIONSからQActionを作り、self.addAction()でウィンドウに
        直接ぶら下げる（メニューを開いていなくてもショートカットが効くようにするため）。"""
        for action_id, label, default_key, handler_name in KEYBINDABLE_ACTIONS:
            action = QAction(label, self)
            key = get_keybind(self.settings, action_id, default_key)
            if key:
                action.setShortcut(QKeySequence(key))
            action.setShortcutContext(Qt.WindowShortcut)

            if action_id in ZOOM_ACTION_IDS:
                mode = ZOOM_ACTION_IDS[action_id]
                action.setCheckable(True)
                action.setChecked(self.zoom_mode == mode)
                action.triggered.connect(lambda checked=False, m=mode: self.set_zoom_mode(m))
            elif action_id == "toggle_always_on_top":
                action.setCheckable(True)
                action.setChecked(self.settings.get("always_on_top", False))
                action.triggered.connect(self.toggle_always_on_top)
            elif handler_name:
                action.triggered.connect(getattr(self, handler_name))

            self.actions_by_id[action_id] = action
            self.addAction(action)  # ウィンドウ直下に追加 -> メニュー非表示時もショートカット有効

        # ズームアクションは手動でチェック状態を管理する（set_zoom_modeで一括更新）

        # ---- アスペクト比: オリジナル / 4:3 / 16:9 の3択（排他選択） ----
        self.aspect_actions = {}
        for mode in ASPECT_MODES:
            action = QAction(ASPECT_MODE_LABELS[mode], self)
            action.setCheckable(True)
            action.setChecked(self.aspect_mode == mode)
            action.triggered.connect(lambda checked=False, m=mode: self.set_aspect_mode(m))
            self.aspect_actions[mode] = action

    # ---- クリック領域でのページ/フォルダ送り、ダブルクリックで最大化 ----
    def eventFilter(self, obj, event):
        if obj is self.scroll_area.viewport():
            if event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                # すぐには処理せず、ダブルクリック判定の猶予時間だけ待つ
                # (この間にダブルクリックが来たらシングルクリックの処理はキャンセルする)
                self._pending_click_pos = event.pos()
                self._pending_click_size = obj.size()
                QTimer.singleShot(
                    QApplication.instance().doubleClickInterval(),
                    self._process_pending_click
                )
                return False
            elif event.type() == QEvent.MouseButtonDblClick and event.button() == Qt.LeftButton:
                self._pending_click_pos = None  # 保留中のシングルクリック処理をキャンセル
                self.toggle_fullscreen()
                return True
            elif event.type() == QEvent.Wheel:
                if event.modifiers() & Qt.ControlModifier:
                    # Ctrl+ホイール: ズーム
                    steps = event.angleDelta().y() / 120.0  # 1ノッチ=120、小数のまま渡す
                    self.zoom_by_step(steps)
                elif event.angleDelta().y() > 0:
                    self.prev_image()  # 上に回す: 前の画像
                elif event.angleDelta().y() < 0:
                    self.next_image()  # 下に回す: 次の画像
                return True  # 通常のスクロール動作は行わない(ページ送りを優先)
            elif event.type() == QEvent.MouseButtonPress and event.button() == Qt.MiddleButton:
                # 中ボタンドラッグでの画像パン(大きい画像を掴んで動かす)開始
                self._pan_start_pos = event.pos()
                self._pan_start_scroll = (
                    self.scroll_area.horizontalScrollBar().value(),
                    self.scroll_area.verticalScrollBar().value(),
                )
                self.scroll_area.viewport().setCursor(Qt.ClosedHandCursor)
                return True
            elif event.type() == QEvent.MouseMove and self._pan_start_pos is not None:
                delta = event.pos() - self._pan_start_pos
                h_start, v_start = self._pan_start_scroll
                self.scroll_area.horizontalScrollBar().setValue(h_start - delta.x())
                self.scroll_area.verticalScrollBar().setValue(v_start - delta.y())
                return True
            elif event.type() == QEvent.MouseButtonRelease and event.button() == Qt.MiddleButton:
                self._pan_start_pos = None
                self.scroll_area.viewport().setCursor(Qt.ArrowCursor)
                return True
        return super().eventFilter(obj, event)

    def _process_pending_click(self):
        if self._pending_click_pos is None:
            return  # ダブルクリックでキャンセルされた
        pos = self._pending_click_pos
        size = self._pending_click_size
        self._pending_click_pos = None

        if size.width() <= 0 or size.height() <= 0:
            return

        is_top = pos.y() < size.height() / 2
        is_left = pos.x() < size.width() / 2

        if is_top and is_left:
            self.prev_image()       # 左上: 前の画像
        elif is_top and not is_left:
            self.next_image()       # 右上: 次の画像
        elif not is_top and is_left:
            self.prev_folder()      # 左下: 前のフォルダ（未実装）
        else:
            self.next_folder()      # 右下: 次のフォルダ（未実装）

    def toggle_maximize_restore(self):
        if self.isMaximized():
            self.showNormal()
        else:
            self.showMaximized()

    def _sibling_archives(self):
        """現在のアーカイブと同じフォルダ内にある、ZIP/RARファイルの一覧(名前順)を返す。"""
        if not self.current_archive_path:
            return []
        folder = Path(self.current_archive_path).parent
        try:
            files = sorted(
                p for p in folder.iterdir()
                if p.is_file() and p.suffix.lower() in (".zip", ".rar", ".7z")
            )
        except Exception:
            return []
        return files

    def next_folder(self):
        """次のフォルダへ移動する。開いているアーカイブ内部に複数のフォルダ
        (例: Chapter1/, Chapter2/)がある場合は、まずそちらを優先して移動する
        （同じZIP内の次のフォルダの先頭ページへ）。内部フォルダが無い/既に
        最後の内部フォルダにいる場合は、従来通り同じOSフォルダ内の次のアーカイブへ。"""
        if self.reader is not None and self.reader.internal_folders:
            current_folder = self.reader.get_folder_of_index(self.index)
            if current_folder is not None and current_folder + 1 < len(self.reader.internal_folders):
                next_index = self.reader.get_first_index_of_folder(current_folder + 1)
                if next_index is not None:
                    self.index = next_index
                    self.show_current_image()
                    return
                # 次の内部フォルダが見つからない場合のみ、下のOSフォルダ間移動にフォールバックする

        siblings = self._sibling_archives()
        if not siblings or not self.current_archive_path:
            return
        try:
            idx = siblings.index(Path(self.current_archive_path))
        except ValueError:
            return
        if idx + 1 < len(siblings):
            self.navigate_to_archive(str(siblings[idx + 1]))
        else:
            self._show_osd("これ以上先のアーカイブはありません")

    def prev_folder(self):
        """前のフォルダへ移動する。開いているアーカイブ内部に複数のフォルダが
        ある場合は、まずそちらを優先する(同じZIP内の前のフォルダの先頭ページへ)。
        内部フォルダが無い/既に最初の内部フォルダにいる場合は、従来通り同じ
        OSフォルダ内の前のアーカイブへ(最後のページから逆読みを継続)。"""
        if self.reader is not None and self.reader.internal_folders:
            current_folder = self.reader.get_folder_of_index(self.index)
            if current_folder is not None and current_folder > 0:
                prev_index = self.reader.get_first_index_of_folder(current_folder - 1)
                if prev_index is not None:
                    self.index = prev_index
                    self.show_current_image()
                    return

        siblings = self._sibling_archives()
        if not siblings or not self.current_archive_path:
            return
        try:
            idx = siblings.index(Path(self.current_archive_path))
        except ValueError:
            return
        if idx > 0:
            self.navigate_to_archive(str(siblings[idx - 1]), start_at_end=True)
        else:
            self._show_osd("これより前のアーカイブはありません")

    # ---- ドラッグ&ドロップ ----
    def dragEnterEvent(self, event: QDragEnterEvent):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent):
        urls = event.mimeData().urls()
        if not urls:
            return
        path = urls[0].toLocalFile()
        self.navigate_to_archive(path)

    def _cleanup_archive_state(self):
        """開いているアーカイブに関連する状態を全て後片付けする。
        新しいアーカイブを開く時(open_archive)だけでなく、フォルダ一覧表示に
        切り替える時(navigate_to_folder)にも必ず呼ぶこと。後者を素通りすると、
        アーカイブビューアを閉じずにフォルダ間を行き来しただけで、前のアーカイブの
        画像キャッシュ・実行中のAIスレッドがどんどん積み上がってしまう
        (実際にユーザー報告のあったメモリリークの原因)。"""
        self._ai_failed_keys.clear()
        self._reader_generation += 1
        self._prefetch_failed_indices.clear()
        self.original_pixmap = None
        self.current_noise_level = None
        self._noise_cache_key = None
        self._waiting_for_index = None
        self._archive_generation += 1  # 実行中/遅延到着のAI結果を無効化するための世代を進める
        if self.reader is not None:
            self.reader.close()  # 前のアーカイブのファイルハンドルを閉じる
            self.reader = None
        self._image_cache.clear()  # 前のアーカイブの先読みキャッシュは無効になる
        self._ai_cache.clear()
        self._ai_queue.clear()  # 前のアーカイブ宛のAI先読みジョブは意味がなくなる
        self._ai_job_generation.clear()
        if self._ai_pending_key is not None and self._ai_thread_ref is not None:
            # 実行中のAIジョブも、別のアーカイブに切り替わるなら不要なので
            # キャンセルする(放置すると新アーカイブのAI処理が開始待ちになってしまう)
            _, worker = self._ai_thread_ref
            worker.request_cancel()

        if self._batch_thread_ref is not None:
            # バッチ処理も同様にキャンセルする。ここを忘れると、旧アーカイブの
            # 画像データ(QImageのコピー)を実行中のバッチジョブがそのまま保持し
            # 続けてしまい(メモリ/VRAM展開モード時はVRAMテクスチャも)、しかも
            # 完了時に新アーカイブの_ai_cacheへ誤ったインデックスで書き込まれる
            # おそれもある(切り替え前のindex番号のまま結果が返ってくるため)。
            _, batch_worker = self._batch_thread_ref
            batch_worker.request_cancel()
        self._batch_pending_keys = set()
        self._batch_pending_entries = []
        self._batch_failure_count.clear()  # 別アーカイブの同じページ番号に前の失敗回数を引き継がないようにする
        self._ai_processing_start_time.clear()  # 別アーカイブの同じページ番号に前の開始時刻を引き継ぐと、
                                                 # デバッグ情報の処理時間表示が実際とかけ離れた値になるため
        self._pending_diff_composite.clear()  # 旧アーカイブの差分合成待ち情報も無効になる

    # ---- アーカイブを開く ----
    def open_archive(self, path, start_at_end=False):
        self._cleanup_archive_state()

        # 隣接アーカイブの先読みキャッシュが、これから開くアーカイブと一致するか確認
        sibling_hit = None
        for direction, entry in list(self._sibling_prefetch.items()):
            if entry["path"] == path:
                sibling_hit = entry
        self._sibling_prefetch.clear()  # 別のアーカイブを開くので、境界ページ先読みは全部無効になる

        self.current_archive_path = path
        sort_key = natural_sort_key if self.settings.get("natural_sort", True) else None
        try:
            self.reader = ArchiveReader(path, self.passwords, sort_key=sort_key)
        except Exception as e:
            self.image_label.setText(f"開けませんでした:\n{e}")
            self.reader = None
            return

        if not self.reader.image_names:
            self.image_label.setText("画像ファイルが見つかりませんでした")
            return

        self.index = len(self.reader.image_names) - 1 if start_at_end else 0
        max_index = max(0, len(self.reader.image_names) - 1)
        self.page_slider.setMaximum(max_index)
        self.fullscreen_slider.setMaximum(max_index)

        # 先読みキャッシュが今回表示するページと一致していれば、そのまま使う。
        # (sort_keyの食い違い等で「最初/最後のページ」の判定がズレていないか、
        # 実際のファイル名を比較して確認する安全策)
        if sibling_hit is not None:
            expected_name = self.reader.image_names[self.index]
            if sibling_hit.get("name") == expected_name:
                self._image_cache[self.index] = (sibling_hit["data"], sibling_hit["image"])
            else:
                debug_print(f"[SIBLING PREFETCH] ページ不一致のためキャッシュを使わず再読み込みする: "
                      f"期待={expected_name} 先読み結果={sibling_hit.get('name')}")

        self.show_current_image()

    def open_archive_dialog(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "アーカイブを開く", "",
            "Archives and Images (*.zip *.rar *.7z *.cbz *.cbr *.pak *.jpg *.jpeg *.png *.gif *.bmp *.webp *.tlg);;All Files (*)"
        )
        if path:
            self.navigate_to_archive(path)

    # ---- ナビゲーション（戻る/進む/上へ、履歴管理） ----
    def navigate_to_archive(self, path, push=True, start_at_end=False):
        """アーカイブを開き、画像表示に切り替える。履歴にも記録する。"""
        self.open_archive(path, start_at_end=start_at_end)
        self.current_folder_path = str(Path(path).resolve().parent)
        self.stacked.setCurrentIndex(0)
        if push:
            self._push_history({"type": "archive", "path": path})
        self._update_nav_buttons()
        self._update_path_label()

    def navigate_to_folder(self, path, push=True):
        """フォルダ一覧表示に切り替える。履歴にも記録する。
        画像表示中に呼ばれた場合、開いていたアーカイブの状態を後片付けする
        (でないと、アーカイブを閉じずにフォルダ間を行き来しただけで、
        画像キャッシュや実行中のAIスレッドが積み上がってしまう)。"""
        if self.reader is not None:
            self._cleanup_archive_state()
        path = str(Path(path).resolve())
        self.current_folder_path = path
        self.folder_browser.show_folder(path)
        self.stacked.setCurrentIndex(1)
        if push:
            self._push_history({"type": "folder", "path": path})
        self._update_nav_buttons()
        self._update_path_label()

    def go_up(self):
        """画像表示中なら、そのアーカイブが入っているフォルダを表示する。
        ただし、開いているアーカイブ内部に複数のフォルダ(例: Chapter1/, Chapter2/)が
        ある場合は、まずZIP内のフォルダ一覧を表示する(OSのフォルダへ出る前に、
        同じZIP内の別フォルダへ直接移動できるようにするため)。
        既にフォルダ表示中なら、さらに1つ上の親フォルダに移動する。"""
        if self.current_folder_path is None:
            return

        if self.stacked.currentIndex() == 0:
            # 画像表示中で、かつアーカイブ内部に複数フォルダがあるなら、
            # まずそのフォルダ一覧を出す(OSフォルダへ出るのは、そこから
            # 明示的に選ぶか、もう一度go_upする形にする)。
            if self.reader is not None and self.reader.internal_folders:
                self._show_internal_folder_picker()
                return
            # 内部フォルダが無い(フラットなアーカイブ) -> そのアーカイブのフォルダ自体を表示する
            self.navigate_to_folder(self.current_folder_path)
            return

        current = Path(self.current_folder_path)
        parent = current.parent
        if parent == current:
            return  # ファイルシステムのルートに到達、これ以上は上がれない
        self.navigate_to_folder(str(parent))

    def _show_internal_folder_picker(self):
        """アーカイブ内部の複数フォルダ(例: Chapter1/, Chapter2/)を一覧表示し、
        選んだフォルダの先頭ページへ直接移動できるダイアログを出す。"""
        dialog = QDialog(self)
        dialog.setWindowTitle("アーカイブ内のフォルダ")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("移動先のフォルダを選んでください:"))

        list_widget = QListWidget()
        current_folder = self.reader.get_folder_of_index(self.index)
        for i, folder_name in enumerate(self.reader.internal_folders):
            item_text = folder_name + ("  (現在)" if i == current_folder else "")
            list_widget.addItem(item_text)
        if current_folder is not None:
            list_widget.setCurrentRow(current_folder)
        layout.addWidget(list_widget)

        os_folder_button = QPushButton("OSのフォルダ表示に切り替える")
        layout.addWidget(os_folder_button)

        def jump_to_folder(row):
            if row < 0:
                return
            target_index = self.reader.get_first_index_of_folder(row)
            if target_index is not None:
                self.index = target_index
                self.show_current_image()
            dialog.accept()

        list_widget.itemDoubleClicked.connect(lambda _item: jump_to_folder(list_widget.currentRow()))
        os_folder_button.clicked.connect(lambda: (dialog.accept(), self.navigate_to_folder(self.current_folder_path)))

        dialog.resize(320, 300)
        dialog.exec()

    def go_back(self):
        if self.history_index > 0:
            self.history_index -= 1
            self._restore_history_entry(self.history[self.history_index])
            self._update_nav_buttons()

    def go_forward(self):
        if self.history_index < len(self.history) - 1:
            self.history_index += 1
            self._restore_history_entry(self.history[self.history_index])
            self._update_nav_buttons()

    def _restore_history_entry(self, entry):
        if entry["type"] == "archive":
            self.navigate_to_archive(entry["path"], push=False)
        else:
            self.navigate_to_folder(entry["path"], push=False)

    def _push_history(self, entry):
        # 現在位置より先(「進む」で戻れたはずの履歴)は、新しい行き先が決まった時点で捨てる
        self.history = self.history[:self.history_index + 1]
        self.history.append(entry)
        self.history_index = len(self.history) - 1

    def _update_nav_buttons(self):
        self.back_button.setEnabled(self.history_index > 0)
        self.forward_button.setEnabled(self.history_index < len(self.history) - 1)
        can_go_up = False
        if self.current_folder_path is not None:
            current = Path(self.current_folder_path)
            can_go_up = current.parent != current
        self.up_button.setEnabled(can_go_up)

    def _update_path_label(self):
        if self.stacked.currentIndex() == 0 and self.current_archive_path:
            self.path_label.setText(self.current_archive_path)
        elif self.current_folder_path:
            self.path_label.setText(self.current_folder_path)
        else:
            self.path_label.setText("")

    def _on_path_bar_entered(self):
        """パスバーに入力(貼り付け)されたパスでEnterが押されたら、そこに移動する。"""
        text = self.path_label.text().strip().strip('"')
        if not text:
            return
        p = Path(text)
        if p.is_file() and p.suffix.lower() in (".zip", ".rar", ".7z"):
            self.navigate_to_archive(str(p))
        elif p.is_dir():
            self.navigate_to_folder(str(p))
        else:
            self.statusBar().showMessage(f"見つかりません: {text}", 3000)
            self._update_path_label()  # 入力を元の表示に戻す

    # ---- 画像表示（等倍） ----
    def show_current_image(self):
        if self.reader is None or not self.reader.image_names:
            return
        self._prune_ai_work_for_relevance()  # 不要になったAIジョブをキャンセル/削除する
        self._evict_passed_pages()  # 通り過ぎたページのキャッシュを解放する
        self.page_slider.setValue(self.index)
        self.fullscreen_slider.setValue(self.index)
        name = self.reader.image_names[self.index]

        cached = self._image_cache.get(self.index)
        if cached is not None:
            # 既に裏で先読み済み -> 読み込み・デコードを待たず即座に表示できる
            data, image = cached
        else:
            # キャッシュにまだ無い(先読みが追いついていない、またはTLG6等の
            # デコードが重い形式)。ここで同期的にブロックすると、ページ切り替え
            # そのものが固まって見えるため、代わりに即座に「読み込み中」を表示し、
            # デコードはバックグラウンドスレッドに任せる(完了したら表示を更新する)。
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText(f"読み込み中...\n{name}")
            self._waiting_for_index = self.index
            if self.index not in self._active_prefetch_threads:
                self._start_prefetch(self.index)  # 完了時、_on_prefetch_decodedが表示を更新する
            return

        self._waiting_for_index = None
        self.original_pixmap = QPixmap.fromImage(image)

        # 低解像度スキップ(表示側): next_image/prev_imageから来た場合のみ、
        # 現在ページが低解像度なら、来た方向へさらに進む(なかったことにする)。
        # サムネイル/スライダー上には引き続き表示されるので、そちらへの影響は無い。
        # ページジャンプ・スライダークリック等(_nav_direction=0)では発動しない。
        if self.skip_low_res_enabled and self._nav_direction != 0:
            w, h = self.original_pixmap.width(), self.original_pixmap.height()
            if w < self.skip_low_res_threshold or h < self.skip_low_res_threshold:
                next_index = self.index + self._nav_direction
                if 0 <= next_index < len(self.reader.image_names):
                    debug_print(f"[SKIP DEBUG] page{self.index}は低解像度({w}x{h})のため表示をスキップ、"
                                f"{'次' if self._nav_direction > 0 else '前'}へ進みます")
                    self.index = next_index
                    self.show_current_image()
                    return
                # 端に達してこれ以上進めない場合は、そのまま(低解像度でも)表示する
        self._nav_direction = 0  # スキップ判定が終わったので、次回の別操作に影響しないようリセットする

        # プロパティ表示用の情報: 軽い処理はここでキャッシュしておくが、
        # PILでの再デコード(画像フォーマット/DPI/EXIF等の取得)は重いので、
        # 実際にプロパティダイアログを開いた時だけ計算する(遅延評価)。
        self.current_data_size = len(data)
        self.current_raw_data = data  # プロパティダイアログで必要な時だけPILデコードする
        self.current_qimage_depth = image.depth()
        self.current_qimage_has_alpha = image.hasAlphaChannel()
        self.current_entry_info = self.reader.get_entry_info(name)  # 既に開いたハンドルなので軽い
        self.current_pil_info = None  # 遅延評価、show_properties_dialogで初めて計算する

        self.render_current_pixmap()

        self.setWindowTitle(
            f"{APP_NAME} - {name} "
            f"({self.index + 1}/{len(self.reader.image_names)}) "
            f"[{self.original_pixmap.width()}x{self.original_pixmap.height()}] "
            f"zoom={self.zoom_mode} aspect={self.aspect_mode} rot={self.rotation}"
        )

        # 表示はここまでで完了。隣接ページの先読みは、この描画をブロックしないよう
        # 少し遅らせて(イベントループに一度制御を返してから)裏で開始する。
        self._evict_distant_cache_entries()
        QTimer.singleShot(0, self._schedule_prefetch)

    # ---- 先読み(プリフェッチ) ----
    MAX_CONCURRENT_PREFETCH_THREADS = 4  # 同時に走らせる先読みスレッドの上限

    def _schedule_prefetch(self):
        """アーカイブ全体を、現在位置に近いページから優先して先読みする
        （「ZIP内の画像を全てVRAMに展開しておく」という方針のため、
        prefetch_windowでは範囲を絞らず、いずれ全ページをカバーする）。
        同時実行数はMAX_CONCURRENT_PREFETCH_THREADSで抑え、1つ終わるごとに
        次のページの先読みを続ける(パイプラインを常に満たしておく)。"""
        if self._closing or self.reader is None or not self.reader.image_names:
            return

        self._refresh_ai_prefetch()
        total = len(self.reader.image_names)

        if len(self._active_prefetch_threads) >= self.MAX_CONCURRENT_PREFETCH_THREADS:
            pass  # 同時実行数の上限に達している間は新規開始しない(次の完了時に再度呼ばれる)
        else:
            # 現在位置に近い順(次を優先、その後前)に、全ページを対象にする。
            # ただし後方(通り過ぎた)方向は passed_pages_keep_count までに制限する
            # (それ以上は_evict_passed_pagesで解放される想定なので、際限なく
            # 先読み→即解放を繰り返す無駄なループを避ける)。
            backward_limit = self.passed_pages_keep_count if self.passed_pages_keep_count >= 0 else total
            offsets = []
            for d in range(1, total + 1):
                if self.index + d < total:
                    offsets.append(self.index + d)
                if self.index - d >= 0 and d <= backward_limit:
                    offsets.append(self.index - d)

            for target_index in offsets:
                if len(self._active_prefetch_threads) >= self.MAX_CONCURRENT_PREFETCH_THREADS:
                    break
                if target_index in self._image_cache:
                    continue
                if target_index in self._prefetch_failed_indices:
                    continue
                if target_index in self._active_prefetch_threads:
                    continue
                self._start_prefetch(target_index)

        # ---- アーカイブの端に近い場合、隣接するZIP/RARの境界ページも先読みする ----
        if self.index + self._prefetch_window >= total - 1:
            self._start_sibling_prefetch("next")
        if self.index - self._prefetch_window <= 0:
            self._start_sibling_prefetch("prev")

    def _start_sibling_prefetch(self, direction):
        """同じフォルダ内の次/前のアーカイブの境界ページ(次なら最初、前なら最後)を
        バックグラウンドで先読みする。next_folder/prev_folderで実際に移動した時、
        すぐ表示できるようにするため。"""
        if direction in self._sibling_prefetch or direction in self._sibling_prefetch_threads:
            return  # 既に先読み済み/先読み中

        siblings = self._sibling_archives()
        if not siblings or not self.current_archive_path:
            return
        try:
            current_pos = siblings.index(Path(self.current_archive_path))
        except ValueError:
            return

        if direction == "next":
            if current_pos + 1 >= len(siblings):
                return
            target_path = str(siblings[current_pos + 1])
            which = "first"
        else:
            if current_pos - 1 < 0:
                return
            target_path = str(siblings[current_pos - 1])
            which = "last"

        thread = QThread(self)
        sort_key = natural_sort_key if self.settings.get("natural_sort", True) else None
        worker = ThumbnailWorker(target_path, self.passwords, which=which, sort_key=sort_key)
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.loaded.connect(lambda path, image, data, name, d=direction: self._on_sibling_prefetch_loaded(d, path, image, data, name))
        worker.failed.connect(lambda path, d=direction: self._on_sibling_prefetch_failed(d))
        worker.loaded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(lambda: self._cleanup_sibling_prefetch_thread(direction, thread, worker))

        self._sibling_prefetch_threads[direction] = (thread, worker)
        thread.start()

    def _cleanup_sibling_prefetch_thread(self, direction, thread, worker):
        entry = self._sibling_prefetch_threads.get(direction)
        if entry == (thread, worker):
            del self._sibling_prefetch_threads[direction]
        thread.deleteLater()
        worker.deleteLater()

    def _on_sibling_prefetch_loaded(self, direction, path, image, data, name):
        self._sibling_prefetch[direction] = {"path": path, "data": data, "image": image, "name": name}

    def _on_sibling_prefetch_failed(self, direction):
        pass  # 先読み失敗は無視する。実際に移動した時に通常の同期読み込みで再試行される。

    def _start_prefetch(self, index):
        name = self.reader.image_names[index]
        archive_path = self.current_archive_path

        thread = QThread(self)
        worker = PrefetchWorker(self.reader, index, name, archive_path)
        worker._reader_generation = self._reader_generation
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.decoded.connect(self._on_prefetch_decoded)
        worker.failed.connect(self._on_prefetch_failed)
        worker.decoded.connect(thread.quit)
        worker.failed.connect(thread.quit)
        thread.finished.connect(lambda: self._cleanup_prefetch_thread(index))

        self._active_prefetch_threads[index] = (thread, worker)
        thread.start()

    def _cleanup_prefetch_thread(self, index):
        entry = self._active_prefetch_threads.pop(index, None)
        if entry is not None:
            thread, worker = entry
            thread.deleteLater()
            worker.deleteLater()
        if (not self._closing and self.reader is not None and self._waiting_for_index == index
                and index not in self._image_cache and index not in self._prefetch_failed_indices):
            self._start_prefetch(index)
        self._schedule_prefetch()  # 空いた分、次のページの先読みを続ける(全ページ埋まるまで)

    def _on_prefetch_decoded(self, archive_path, index, data, image):
        # アーカイブを切り替えた後に古いスレッドの結果が届いた場合は無視する
        worker = self.sender()
        if (self._closing or archive_path != self.current_archive_path
                or (worker is not None and worker._reader_generation != self._reader_generation)):
            return
        if index not in self._image_cache:
            self._image_cache[index] = (data, image)
        self._maybe_prefetch_ai(index, image)
        if index == self.index and self._waiting_for_index == index:
            # 「読み込み中」表示のまま待っていたページの結果が届いた -> 表示を更新する
            self.show_current_image()

    def _on_prefetch_failed(self, archive_path, index):
        worker = self.sender()
        if (self._closing or archive_path != self.current_archive_path
                or (worker is not None and worker._reader_generation != self._reader_generation)):
            return
        self._prefetch_failed_indices.add(index)
        debug_print(f"[DECODE FAILED] generation={self._reader_generation} page={index}")
        if (archive_path == self.current_archive_path and index == self.index
                and self._waiting_for_index == index):
            self._waiting_for_index = None
            name = self.reader.image_names[index] if self.reader else ""
            self.image_label.setText(f"デコードできませんでした: {name}")

    def _evict_distant_cache_entries(self):
        """(方針変更) 同じアーカイブを開いている間は、デコード済みキャッシュを
        削除しない。別のアーカイブを開いた時(open_archive)にまとめてクリアされる。
        メモリ使用量は「アーカイブ全体を先読みする」設計と割り切って許容する。"""
        pass

    def _evict_distant_ai_cache_entries(self):
        """(方針変更) AIキャッシュも同様に、同じアーカイブを開いている間は保持し続ける。
        既にAIアップスケールした画像は、別のZIPを開くまで取っておく、という要望のため。"""
        pass

    def _compute_canvas(self, pixmap):
        """回転・アスペクト比処理を適用したキャンバスを返す(現在ページ以外にも使える汎用版)。"""
        rotated = pixmap
        if self.rotation:
            transform = QTransform().rotate(self.rotation)
            rotated = pixmap.transformed(transform, Qt.SmoothTransformation)

        img_w, img_h = rotated.width(), rotated.height()
        if self.aspect_mode == "original" or img_w == 0 or img_h == 0:
            return rotated

        ratio_w, ratio_h = (4, 3) if self.aspect_mode == "4:3" else (16, 9)
        new_h = max(1, round(img_w * ratio_h / ratio_w))
        return rotated.scaled(img_w, new_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

    def render_current_pixmap(self):
        """self.original_pixmap に回転とアスペクト比のレターボックス(黒帯)処理を適用し、
        さらにズームモードに従って最終的な表示サイズにスケーリングする。
        ズームモード/アスペクト比/回転の変更時や、ウィンドウサイズ変更(fit時)に呼び出す。"""
        if self.original_pixmap is None or self.original_pixmap.isNull():
            return

        canvas = self._compute_canvas(self.original_pixmap)

        # ---- ズームモードに応じた最終スケーリング ----
        if self.zoom_mode == "fit":
            target_size = self.scroll_area.viewport().size()
            target_w, target_h = target_size.width(), target_size.height()
        else:
            try:
                pct = int(self.zoom_mode)
            except ValueError:
                pct = 100
            target_w = max(1, round(canvas.width() * pct / 100))
            target_h = max(1, round(canvas.height() * pct / 100))

        is_enlarging = target_w > canvas.width() or target_h > canvas.height()
        cache_key = (self.index, self.rotation, self.aspect_mode)

        source = canvas
        source_desc = "canvas"

        if self.debug_show_diff_highlight:
            highlighted = self._render_diff_highlight_image()
            if highlighted is not None:
                source = QPixmap.fromImage(highlighted)
                source_desc = "diff_highlight"

        is_low_res = (self.skip_low_res_enabled and
                      (canvas.width() < self.skip_low_res_threshold or canvas.height() < self.skip_low_res_threshold))
        if (source_desc != "diff_highlight" and self.ai_upscale_enabled
                and not self.debug_show_pre_ai and not is_low_res):
            cached = self._ai_cache.get(self.index)
            if cached is not None and cached[0] == (self.rotation, self.aspect_mode):
                # キャッシュにはQImage(メインメモリの生データ)として保持しており、
                # 表示するこの瞬間だけQPixmap化する(VRAM上のテクスチャとして残るのは
                # 常に「今表示している1枚」だけにするため。他の先読みキャッシュは
                # QImageのままメインメモリに留め、VRAMを圧迫しないようにしている)。
                source = QPixmap.fromImage(cached[1])
                source_desc = f"ai_cache(size={source.width()}x{source.height()})"
            else:
                noise_level = self._get_current_noise_level(cache_key)
                plan = self._resolve_ai_plan(canvas, target_w, target_h, noise_level, is_enlarging)
                if plan is not None:
                    model_name, passes = plan
                    diff_plan = self._try_diff_based_plan(canvas, self.index, self.original_pixmap.toImage(), model_name)
                    if diff_plan is not None:
                        crop = diff_plan["cropped_canvas"]
                        queued = self._request_ai_upscale(crop, cache_key, diff_plan["passes"], model_name)
                        if queued:
                            # 実際に新しくジョブを起動した場合のみメタデータを登録する。
                            # 既に同じcache_keyのジョブが実行中/待機中で今回は何もしなかった
                            # 場合にここで上書きすると、後で古いジョブの結果が完了した時に
                            # 「新しいが無関係なbase_pixmap/region_scaled」で合成してしまう
                            # (前後のページ移動でこのページに戻ってきた時に起きるバグの原因だった)。
                            self._pending_diff_composite[cache_key] = diff_plan
                            debug_print(f"[DIFF DISPATCH] page{self.index}(表示中): canvas={canvas.width()}x{canvas.height()} "
                                        f"crop={crop.width()}x{crop.height()} region_scaled={diff_plan['region_scaled']} "
                                        f"passes={diff_plan['passes']} -> AI送信サイズ={crop.width()}x{crop.height()}")
                    else:
                        self._request_ai_upscale(canvas, cache_key, passes, model_name)  # 裏で処理、結果は後で反映

        debug_print(f"[RENDER DEBUG] zoom_mode={self.zoom_mode} canvas={canvas.width()}x{canvas.height()} "
              f"target={target_w}x{target_h} source={source_desc} auto_resize_window={self.auto_resize_window}")

        if self.zoom_mode == "fit":
            scaled = source.scaled(target_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        elif source is canvas and target_w == canvas.width() and target_h == canvas.height():
            scaled = canvas  # 等倍で加工不要
        else:
            scaled = source.scaled(target_w, target_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)

        debug_print(f"[RENDER DEBUG] -> scaled={scaled.width()}x{scaled.height()}")

        self.image_label.setPixmap(scaled)
        self.image_label.resize(scaled.size())

        if self.auto_resize_window and self.zoom_mode != "fit":
            # "fit"モードは逆方向(ウィンドウ->画像)に合わせる仕組みなので、
            # 同時に有効化すると無限ループになるため、fit以外の時だけ動かす。
            QTimer.singleShot(0, self._resize_window_to_image)

        self._update_debug_info_label()


    # ---- キー操作 ----
    def closeEvent(self, event):
        """Cancel workers and let Qt deliver their termination before destruction."""
        self._closing = True
        self._ai_queue.clear()
        entries = list(self._active_prefetch_threads.values())
        entries += list(self.folder_browser._thumbnail_threads.values())
        entries += list(self.folder_browser._retiring_threads)
        entries += list(self._sibling_prefetch_threads.values())
        for entry in (self._ai_thread_ref, self._batch_thread_ref):
            if entry is not None:
                entry[1].request_cancel()
                entries.append(entry)
        if self._benchmark_thread_ref is not None:
            entries.append(self._benchmark_thread_ref)
        for thread, worker in entries:
            thread.quit()
        if any(thread.isRunning() for thread, worker in entries):
            event.ignore()
            QTimer.singleShot(100, self.close)
            return
        if self.reader is not None:
            self.reader.close()
        super().closeEvent(event)

    DENOISE_MODE_CYCLE = ["off", "on", "auto"]
    DENOISE_MODE_LABELS = {"off": "デノイズ: オフ", "on": "デノイズ: オン", "auto": "デノイズ: 自動"}
    UPSCALE_MODE_CYCLE = ["off", "on", "count", "target", "undershoot", "overshoot"]
    UPSCALE_MODE_LABELS = {
        "off": "アップスケール: なし",
        "on": "アップスケール: あり(1回)",
        "count": "アップスケール: 固定回数",
        "target": "アップスケール: 目標解像度まで",
        "undershoot": "アップスケール: 手前で止めて拡大",
        "overshoot": "アップスケール: 超えて縮小",
    }

    def keyPressEvent(self, event):
        # Esc / Right / Left は self.actions_by_id 経由のQActionショートカットが処理する。
        # Spaceだけはキーバインド一覧に含めていない「ついで」の操作なのでここで直接処理する。
        if event.key() == Qt.Key_Space:
            self.next_image()
        elif event.key() == Qt.Key_D and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.cycle_denoise_mode()
        elif event.key() == Qt.Key_U and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.cycle_upscale_mode()
        elif event.key() == Qt.Key_O and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.toggle_debug_pre_ai_view()
        elif event.key() == Qt.Key_H and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.toggle_debug_diff_highlight()
        elif event.key() == Qt.Key_E and not (event.modifiers() & (Qt.ControlModifier | Qt.AltModifier)):
            self.reset_ai_processing()
        else:
            super().keyPressEvent(event)

    def toggle_debug_pre_ai_view(self):
        """デバッグ用: oキーでAI処理前/後の画像を切り替えて見比べる。
        設定には保存しない(その場限りの一時的な確認用)。"""
        self.debug_show_pre_ai = not self.debug_show_pre_ai
        self._show_osd("AI処理前(比較モード)" if self.debug_show_pre_ai else "AI処理後")
        self.render_current_pixmap()

    def _invalidate_ai_work(self):
        self._archive_generation += 1
        for entry in (self._ai_thread_ref, self._batch_thread_ref):
            if entry is not None:
                entry[1].request_cancel()
        self._ai_queue.clear()
        self._ai_cache.clear()
        self._ai_failed_keys.clear()
        self._batch_pending_keys.clear()
        self._batch_pending_entries = []
        self._batch_failure_count.clear()
        self._ai_processing_start_time.clear()
        self._ai_job_generation.clear()
        self._pending_diff_composite.clear()
        QTimer.singleShot(0, self._refresh_ai_prefetch)

    def reset_ai_processing(self):
        """Eキー: 古い結果を無効化し、表示中のページから再処理する。"""
        self._invalidate_ai_work()
        self._show_osd("AI処理をリセットしました")
        self.render_current_pixmap()

    def toggle_debug_diff_highlight(self):
        """デバッグ用: hキーで、前ページとの差分検出結果(変化したと判定された
        領域)を色反転して表示する。差分ベースAI処理がどこを「変化あり」と
        見なしているかを目視確認するための機能。設定には保存しない。"""
        self.debug_show_diff_highlight = not self.debug_show_diff_highlight
        self._show_osd("差分ハイライト表示" if self.debug_show_diff_highlight else "通常表示")
        self.render_current_pixmap()

    def _render_diff_highlight_image(self):
        """現在ページと前ページ(canvas同士)の差分を計算し、変化ありと判定された
        ピクセルの色を反転したQImageを返す。前ページが無い/サイズが違う等で
        比較できない場合はNoneを返す。"""
        if self.original_pixmap is None or self.index == 0:
            return None
        prev_raw = self._image_cache.get(self.index - 1)
        if prev_raw is None:
            return None
        _, prev_image = prev_raw

        curr_canvas = self._compute_canvas(self.original_pixmap)
        prev_canvas = self._compute_canvas(QPixmap.fromImage(prev_image))
        if prev_canvas.size() != curr_canvas.size():
            return None

        ratio, bbox, mask = self._compute_image_diff(
            prev_canvas.toImage(), curr_canvas.toImage(), return_mask=True
        )

        result = curr_canvas.toImage().convertToFormat(QImage.Format_RGB888)
        bpl = result.bytesPerLine()
        ptr = result.bits()
        if hasattr(ptr, "setsize"):
            ptr.setsize(bpl * result.height())
        arr = np.frombuffer(ptr, dtype=np.uint8).reshape(result.height(), bpl)[:, :result.width() * 3]
        arr = arr.reshape(result.height(), result.width(), 3)
        arr[mask] = 255 - arr[mask]  # 変化ありと判定された画素だけ色反転する

        debug_print(f"[DIFF DEBUG] ハイライト表示: 変化率={ratio:.1%} bbox={bbox}")
        return result

    def cycle_denoise_mode(self):
        idx = self.DENOISE_MODE_CYCLE.index(self.ai_denoise_mode)
        self.ai_denoise_mode = self.DENOISE_MODE_CYCLE[(idx + 1) % len(self.DENOISE_MODE_CYCLE)]
        self.settings["ai_denoise_mode"] = self.ai_denoise_mode
        save_settings(self.settings)
        self._invalidate_ai_work()  # モードが変わったので古いAI結果は使えない
        self._show_osd(self.DENOISE_MODE_LABELS[self.ai_denoise_mode])
        self.render_current_pixmap()

    def cycle_upscale_mode(self):
        idx = self.UPSCALE_MODE_CYCLE.index(self.ai_upscale_mode)
        self.ai_upscale_mode = self.UPSCALE_MODE_CYCLE[(idx + 1) % len(self.UPSCALE_MODE_CYCLE)]
        self.settings["ai_upscale_mode"] = self.ai_upscale_mode
        save_settings(self.settings)
        self._invalidate_ai_work()
        self._show_osd(self.UPSCALE_MODE_LABELS[self.ai_upscale_mode])
        self.render_current_pixmap()

    def next_image(self):
        if self.reader and self.index < len(self.reader.image_names) - 1:
            self.index += 1
            self._nav_direction = 1  # 低解像度スキップの方向判定に使う(「次へ」から来た)
            self.show_current_image()
        else:
            self._show_osd("ファイルの末尾です")

    def prev_image(self):
        if self.reader and self.index > 0:
            self.index -= 1
            self._nav_direction = -1  # 低解像度スキップの方向判定に使う(「前へ」から来た)
            self.show_current_image()
        else:
            self._show_osd("ファイルの先頭です")

    def first_image(self):
        if self.reader and self.reader.image_names:
            self.index = 0
            self.show_current_image()

    def last_image(self):
        if self.reader and self.reader.image_names:
            self.index = len(self.reader.image_names) - 1
            self.show_current_image()

    # ---- 拡大縮小 / アスペクト比 ----
    def set_zoom_mode(self, mode):
        self.zoom_mode = mode
        self.settings["zoom_mode"] = mode
        save_settings(self.settings)

        # メニュー/永続アクション側のチェック状態を同期
        for action_id, m in ZOOM_ACTION_IDS.items():
            action = self.actions_by_id.get(action_id)
            if action is not None:
                action.setChecked(m == mode)

        self.render_current_pixmap()

    # ---- AIアップスケール ----
    MAX_AI_PASSES = 2  # 4^2=16倍まで。これ以上はレイテンシが大きくなるため打ち切り、
                        # 残りはQtでの通常スケーリングに委ねる
    MAX_AI_QUEUED = 32
    MAX_AI_BATCH = 8

    def _refresh_ai_prefetch(self):
        """Reconsider decoded pages after navigation, reset and worker completion."""
        if self._closing or not self.ai_upscale_enabled or self.reader is None:
            return
        for index in sorted(self._image_cache, key=lambda i: (abs(i - self.index), i < self.index)):
            if len(self._ai_queue) >= self.MAX_AI_QUEUED:
                break
            self._maybe_prefetch_ai(index, self._image_cache[index][1])

    def _maybe_prefetch_ai(self, index, image):
        """先読みでデコードが終わったページについて、条件を満たせばAI処理も
        事前にキューしておく(先行メモリ展開)。現在ページから ai_prefetch_depth
        ページ以内、かつAIをかける価値がある場合のみ。ai_prefetch_depth=-1なら
        アーカイブ全体を対象にする(距離チェックを行わない)。"""
        if not self.ai_upscale_enabled or self.ai_prefetch_depth == 0:
            return
        cache_key = (index, self.rotation, self.aspect_mode)
        if (cache_key in self._ai_failed_keys or cache_key == self._ai_pending_key
                or cache_key in self._batch_pending_keys
                or any(entry[1] == cache_key for entry in self._ai_queue)):
            return
        if self.passed_pages_keep_count >= 0 and index < self.index - self.passed_pages_keep_count:
            return
        if index == self.index:
            return  # 現在ページはrender_current_pixmap側で処理されるので対象外
        if self.ai_prefetch_depth > 0 and abs(index - self.index) > self.ai_prefetch_depth:
            return
        if self.skip_low_res_enabled:
            if image.width() < self.skip_low_res_threshold or image.height() < self.skip_low_res_threshold:
                debug_print(f"[AI DEBUG] page{index}: 低解像度({image.width()}x{image.height()})のためAI処理をスキップ")
                return

        cached = self._ai_cache.get(index)
        if cached is not None and cached[0] == (self.rotation, self.aspect_mode):
            return  # 既にキャッシュ済み

        pixmap = QPixmap.fromImage(image)
        canvas = self._compute_canvas(pixmap)

        if self.zoom_mode == "fit":
            target_size = self.scroll_area.viewport().size()
            target_w, target_h = target_size.width(), target_size.height()
        else:
            try:
                pct = int(self.zoom_mode)
            except ValueError:
                pct = 100
            target_w = max(1, round(canvas.width() * pct / 100))
            target_h = max(1, round(canvas.height() * pct / 100))

        is_enlarging = target_w > canvas.width() or target_h > canvas.height()
        noise_level = estimate_noise_level(image)
        plan = self._resolve_ai_plan(canvas, target_w, target_h, noise_level, is_enlarging)
        if plan is None:
            return
        model_name, passes = plan

        cache_key = (index, self.rotation, self.aspect_mode)
        diff_plan = self._try_diff_based_plan(canvas, index, image, model_name)
        if diff_plan is not None:
            crop = diff_plan["cropped_canvas"]
            queued = self._request_ai_upscale(crop, cache_key, diff_plan["passes"], model_name)
            if queued:
                self._pending_diff_composite[cache_key] = diff_plan
                debug_print(f"[DIFF DISPATCH] page{index}(先読み): canvas={canvas.width()}x{canvas.height()} "
                            f"crop={crop.width()}x{crop.height()} region_scaled={diff_plan['region_scaled']} "
                            f"passes={diff_plan['passes']} -> AI送信サイズ={crop.width()}x{crop.height()}")
        else:
            self._request_ai_upscale(canvas, cache_key, passes, model_name)

    def _get_effective_denoise_flag(self, noise_level):
        """デノイズ設定(off/on/auto)と実際のノイズレベルから、デノイズを
        有効にすべきかどうかを決める。"""
        if self.ai_denoise_mode == "off":
            return False
        if self.ai_denoise_mode == "on":
            return True
        return noise_level is not None and noise_level > NOISE_THRESHOLD_SKIP_AI  # "auto"

    def _resolve_denoise_model(self, engine_info, base_model, want_denoise):
        """base_modelを、デノイズON/OFFの希望に応じて、そのエンジンの対応する
        バリアントに変換する。切替ができないエンジン/モデルならbase_modelのまま返す。"""
        base_info = engine_info["models"].get(base_model)
        if base_info is None:
            return base_model
        has_denoise = base_info.get("has_denoise")
        if has_denoise is None:
            return base_model  # このモデルはデノイズ切替の対象外(denoise_only等)
        if has_denoise == want_denoise:
            return base_model
        variants = engine_info.get("denoise_variants", {})
        return variants.get(base_model, base_model)

    # ---- 差分ベースAI高速化 ----
    # 前ページと現在ページがよく似ている(差分CG等)場合、前ページの既にAI処理済みの
    # 結果を土台にして、変化した領域だけAI処理することで高速化する。
    # 違いすぎる場合は通常の全体処理にフォールバックする。
    DIFF_PIXEL_THRESHOLD = 24     # このRGB差分強度(0-765)を超えたピクセルを「変化した」と見なす
    DIFF_TOO_DIFFERENT_RATIO = 0.35  # 変化ピクセルの割合がこれを超えたら「別画像」として全体処理にフォールバック
    DIFF_REGION_PADDING = 24      # 変化領域の周囲に付ける余白(AIにコンテキストを持たせるため)
    DIFF_MIN_BBOX_DENSITY = 0.5   # バウンディングボックス内で実際に変化したピクセルの割合。これを下回ると
                                  # 「変化点が広範囲に散らばっている(=実質的に別の画像)」と判断しフォールバックする
                                  # (背景色が偶然似ている無関係な2枚を、差分CGと誤判定するバグの対策)

    def _compute_image_diff(self, img_a, img_b, return_mask=False):
        """2枚の画像(同サイズ前提)のピクセル差分を計算する。
        戻り値: (変化ピクセルの割合, 変化領域のバウンディングボックス(x0,y0,x1,y1))。
        完全に同一なら bbox は None。
        return_mask=Trueの場合、3つ目の戻り値として変化マスク(numpy bool配列、
        h x w)も返す(デバッグ用の色反転表示等に使う)。"""
        a = img_a.convertToFormat(QImage.Format_RGB888)
        b = img_b.convertToFormat(QImage.Format_RGB888)
        w, h = a.width(), a.height()

        def to_array(img):
            bpl = img.bytesPerLine()
            ptr = img.constBits()
            if hasattr(ptr, "setsize"):
                ptr.setsize(bpl * h)
            return np.frombuffer(bytes(ptr), dtype=np.uint8).reshape(h, bpl)[:, :w * 3].reshape(h, w, 3)

        arr_a = to_array(a).astype(np.int16)
        arr_b = to_array(b).astype(np.int16)
        diff = np.abs(arr_a - arr_b).sum(axis=2)
        changed_mask = diff > self.DIFF_PIXEL_THRESHOLD

        total = w * h
        changed_count = int(changed_mask.sum())
        ratio = changed_count / total if total > 0 else 0.0

        if changed_count == 0:
            return (ratio, None, changed_mask) if return_mask else (ratio, None)

        ys, xs = np.where(changed_mask)
        bbox = (int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1)
        return (ratio, bbox, changed_mask) if return_mask else (ratio, bbox)

    def _try_diff_based_plan(self, canvas, index, curr_image, model_name):
        """直前ページ(index-1)を土台にした差分ベース処理の計画を作る。
        curr_image: indexページの元画像(QImage)。呼び出し側から明示的に渡す
        (self.original_pixmapに依存すると、現在表示中でない先読み対象ページを
        処理する際に不整合が起きるため)。
        model_name: 今回使う予定のモデル名(クロップに必要なパス数の逆算に使う)。
        適用できない/割に合わない場合はNoneを返す(呼び出し側は通常の全体処理を行う)。"""
        if not self.ai_diff_based_enabled:
            return None

        prev_index = index - 1
        prev_raw = self._image_cache.get(prev_index)
        prev_ai = self._ai_cache.get(prev_index)
        if prev_raw is None or prev_ai is None:
            return None  # 前ページの生画像/AI結果がまだ無い

        (prev_rotation, prev_aspect), prev_upscaled = prev_ai
        if (prev_rotation, prev_aspect) != (self.rotation, self.aspect_mode):
            return None  # 回転/アスペクト比が違うと座標がズレるので対象外

        _, prev_image = prev_raw
        if curr_image is None:
            return None

        prev_canvas = self._compute_canvas(QPixmap.fromImage(prev_image))
        if prev_canvas.size() != canvas.size():
            return None  # サイズが違う画像は単純比較できない

        curr_canvas_image = canvas.toImage()
        prev_canvas_image = prev_canvas.toImage()
        ratio, bbox = self._compute_image_diff(prev_canvas_image, curr_canvas_image)

        if bbox is None:
            return None  # 完全に同一(差分なし) -> 通常経路にお任せする
        if ratio > self.DIFF_TOO_DIFFERENT_RATIO:
            return None  # 違いすぎるので通常の全体処理にフォールバック

        # bbox内での変化密度をチェックする(パディング前の生のbboxで計算する)。
        # 密度が低い場合、変化点が画面全体に散らばっている(=偶然背景色が似ている
        # だけの、実質無関係な別画像)と判断し、diff処理を諦めて全体処理に任せる。
        raw_bbox_w = bbox[2] - bbox[0]
        raw_bbox_h = bbox[3] - bbox[1]
        raw_bbox_area = raw_bbox_w * raw_bbox_h
        total_pixels = canvas.width() * canvas.height()
        if raw_bbox_area > 0:
            changed_count = ratio * total_pixels
            bbox_density = changed_count / raw_bbox_area
            if bbox_density < self.DIFF_MIN_BBOX_DENSITY:
                debug_print(f"[DIFF DEBUG] page{index}: bbox密度不足({bbox_density:.1%})のため"
                            f"全体処理にフォールバック(変化点が広範囲に散らばっている)")
                return None

        x0, y0, x1, y1 = bbox
        x0 = max(0, x0 - self.DIFF_REGION_PADDING)
        y0 = max(0, y0 - self.DIFF_REGION_PADDING)
        x1 = min(canvas.width(), x1 + self.DIFF_REGION_PADDING)
        y1 = min(canvas.height(), y1 + self.DIFF_REGION_PADDING)
        crop_w, crop_h = x1 - x0, y1 - y0
        if crop_w <= 0 or crop_h <= 0:
            return None

        scale_x = prev_upscaled.width() / canvas.width()
        scale_y = prev_upscaled.height() / canvas.height()
        region_scaled = (
            round(x0 * scale_x), round(y0 * scale_y),
            round(crop_w * scale_x), round(crop_h * scale_y),
        )

        # 前ページが何パス重ねがけして今の拡大率になったのかを逆算し、クロップ側も
        # 同じパス数で処理する(常に1回だけで処理すると、前ページが2回以上の
        # 重ねがけだった場合にクロップ部分だけ拡大率が足りず、合成時に引き伸ばされて
        # ボケてしまうバグがあった)。
        model_info = get_model_info(self.ai_upscale_engine, model_name)
        native_scale = model_info["scale"] if model_info else 1
        effective_scale = max(scale_x, scale_y)
        passes_needed = self._compute_ai_passes(effective_scale, native_scale) if native_scale > 1 else 1

        cropped_canvas = canvas.copy(x0, y0, crop_w, crop_h)
        debug_print(f"[DIFF DEBUG] page{index}: 変化率={ratio:.1%} crop={crop_w}x{crop_h} "
                    f"(元画像{canvas.width()}x{canvas.height()}の一部) region_scaled={region_scaled} "
                    f"passes={passes_needed}")

        return {
            "cropped_canvas": cropped_canvas,
            "base_image": prev_upscaled,
            "region_scaled": region_scaled,
            "passes": passes_needed,
        }

    def _composite_diff_result(self, base_image, region_scaled, upscaled_crop):
        """前ページの土台(base_image)をコピーし、AI処理済みの差分クロップを
        正しい位置に貼り付けて、現在ページの完成形を作る。
        引数・戻り値は共にQImage(メインメモリ上の生データ)。合成処理そのものは
        QPainterがQPixmap上でしか描画できないため、内部で一時的にQPixmap化するが、
        そのQPixmapは合成が終わり次第すぐ手放す(戻り値としてキャッシュに長期間
        居座らせるのはQImageの方にする、VRAM圧迫を避けるため)。"""
        result_pixmap = QPixmap.fromImage(base_image)
        painter = QPainter(result_pixmap)
        x, y, w, h = region_scaled
        if upscaled_crop.width() != w or upscaled_crop.height() != h:
            upscaled_crop = upscaled_crop.scaled(w, h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        painter.drawImage(x, y, upscaled_crop)
        painter.end()
        return result_pixmap.toImage()

    def _resolve_ai_plan(self, canvas, target_w, target_h, noise_level, is_enlarging):
        """現在のデノイズ/アップスケール設定から、実際に使うモデル名とパス数を決める。
        処理不要なら None を返す。"""
        engine_info = ENGINES.get(self.ai_upscale_engine)
        if engine_info is None or noise_level is None:
            return None

        want_denoise = self._get_effective_denoise_flag(noise_level)

        base_model = self.ai_upscale_model
        base_info = get_model_info(self.ai_upscale_engine, base_model)
        if base_info is None:
            return None

        if base_info.get("denoise_only"):
            # ユーザーがOptionsで「ノイズ除去専用」モデルをそのまま選んでいる場合、
            # アップスケールモードの設定に関わらずデノイズ設定だけで判断する
            return (base_model, 1) if want_denoise else None

        if self.ai_upscale_mode == "off":
            # アップスケールはしないが、デノイズだけ単独でかけられるなら実行する
            denoise_only_model = engine_info.get("denoise_only_model")
            if want_denoise and denoise_only_model:
                return (denoise_only_model, 1)
            return None

        if self.ai_target_mode == "manual":
            is_enlarging = self._compute_ai_target_factor(canvas, target_w, target_h) > 1.0
        if self.ai_upscale_mode in ("on", "count"):
            is_enlarging = True
        if not is_enlarging:
            return None  # 拡大の必要が無く、かつ拡大系モードなので何もしない

        resolved_model = self._resolve_denoise_model(engine_info, base_model, want_denoise)
        resolved_info = get_model_info(self.ai_upscale_engine, resolved_model)
        if resolved_info is None:
            resolved_model, resolved_info = base_model, base_info
        native_scale = resolved_info["scale"]

        enlarge_factor = self._compute_ai_target_factor(canvas, target_w, target_h)

        # (以前はここでノイズが少ない/拡大率が小さい場合にAIを見送る判定をしていたが、
        # アップスケールは既にai_upscale_modeで明示的にユーザーが選んでいる以上、
        # ノイズ量で上書きして黒黙って見送るのは誤り(「デノイズOFFだとアップスケールが
        # 起きない」バグの原因だった)。ノイズ量による判断は既にwant_denoiseの決定でのみ使う。

        target_passes = self._compute_ai_passes(enlarge_factor, native_scale)

        if self.ai_upscale_mode == "on":
            passes = 1
        elif self.ai_upscale_mode == "count":
            passes = max(1, self.ai_upscale_fixed_count)
        elif self.ai_upscale_mode == "undershoot":
            passes = max(1, target_passes - 1)
        elif self.ai_upscale_mode == "overshoot":
            passes = target_passes + 1
        else:  # "target"(既定)
            passes = target_passes

        return (resolved_model, passes)

    def _compute_ai_target_factor(self, canvas, auto_target_w, auto_target_h):
        """AI処理でどこまで拡大すべきかの倍率を決める。
        - "auto"(既定): 現在のズーム/画面解像度に合わせる(今まで通り)
        - "manual": ユーザーが指定した目標解像度(幅x高さ、例: 2560x1600)に対して、
          幅・高さのどちらか先に収まる方(＝両方の比率のうち小さい方)を基準に
          拡大率を決める(アスペクト比を保ったまま、指定した箱に収まる最大サイズにする)。"""
        if self.ai_target_mode == "manual" and canvas.width() > 0 and canvas.height() > 0:
            ratio_w = self.ai_target_width / canvas.width()
            ratio_h = self.ai_target_height / canvas.height()
            return max(1.0, min(ratio_w, ratio_h))
        return max(
            auto_target_w / max(1, canvas.width()),
            auto_target_h / max(1, canvas.height()),
        )

    def _compute_ai_passes(self, enlarge_factor, native_scale):
        """必要な拡大率に届くまで、モデルの倍率を何回重ねがけすればよいかを計算する。"""
        passes = 0
        cur_scale = 1.0
        while cur_scale < enlarge_factor and passes < self.MAX_AI_PASSES:
            cur_scale *= native_scale
            passes += 1
        return max(1, passes)

    def _get_current_noise_level(self, cache_key):
        """現在ページのノイズレベル推定値を返す(ページごとにキャッシュし、
        毎回再計算しないようにする)。回転/アスペクト比の変更ではノイズ量自体は
        変わらないので、indexだけをキーにする。"""
        if self.current_noise_level is None or self._noise_cache_key != cache_key[:1]:
            if self.original_pixmap is not None and not self.original_pixmap.isNull():
                self.current_noise_level = estimate_noise_level(self.original_pixmap.toImage())
                self._noise_cache_key = cache_key[:1]
            else:
                return None
        return self.current_noise_level

    def _request_ai_upscale(self, canvas_pixmap, cache_key, passes=1, model_name=None):
        """canvas_pixmapのAI処理をキューに追加する(passes回重ねがけ)。
        model_nameを省略した場合はself.ai_upscale_modelを使う
        (デノイズON/OFF切替で別のモデルバリアントを使う時にmodel_nameを指定する)。
        同じ状態(cache_key)が既に実行中/待機中なら追加しない(重複防止)。
        現在表示中のページ宛のジョブは、先読み分より優先してキュー先頭に入れる
        （GPU競合を避けるため、AIジョブは常に1つずつしか実行しない設計）。
        戻り値: 実際に新しくキューに追加した(=True)か、重複等で何もしなかったか(=False)。
        呼び出し側は、この戻り値がFalseの場合、そのジョブに紐づくメタデータ
        (差分合成用のbase_pixmap/region_scaled等)を上書きしてはいけない
        （既に実行中の古いジョブが、後から書き換わったメタデータで誤って合成されるバグの原因になる）。"""
        if self._closing or not self.ai_upscale_enabled or cache_key in self._ai_failed_keys:
            return False
        if not is_engine_available(self.ai_upscale_engine):
            self.last_ai_error = f"AIエンジンを利用できません: {self.ai_upscale_engine}"
            debug_print(f"[AI QUEUE] skipped {cache_key}: {self.last_ai_error}")
            return False
        if cache_key == self._ai_pending_key or cache_key in self._batch_pending_keys:
            return False
        if any(existing_key == cache_key for _, existing_key, _, _ in self._ai_queue):
            return False
        if cache_key[0] != self.index and len(self._ai_queue) >= self.MAX_AI_QUEUED:
            return False  # 完了時にデコード済みキャッシュから補充する

        if model_name is None:
            model_name = self.ai_upscale_model

        image_copy = canvas_pixmap.toImage()  # スレッド間で渡すのでQImageにする
        entry = (image_copy, cache_key, passes, model_name)

        if cache_key[0] == self.index:
            self._ai_queue.insert(0, entry)  # 現在ページを最優先
        elif cache_key[0] == self.index + 1:
            # 次ページは、現在ページの直後(現在ページが並んでいなければ先頭)に優先的に入れる
            insert_pos = 1 if self._ai_queue and self._ai_queue[0][1][0] == self.index else 0
            self._ai_queue.insert(insert_pos, entry)
        else:
            self._ai_queue.append(entry)  # それ以外の先読み分は後回し

        self._ai_processing_start_time[cache_key] = time.time()
        self._ai_job_generation[cache_key] = self._archive_generation
        debug_print(f"[AI QUEUE] queued {cache_key} passes={passes} model={model_name} generation={self._archive_generation}")
        self._process_ai_queue()
        return True

    def _process_ai_queue(self):
        """キューの先頭にあるAIジョブを、他に実行中のものが無ければ開始する。
        現在ページ/次ページ(優先度が高い)は常に単体処理し、それ以外の背景分は
        条件(同じmodel_name/passes)が揃えばまとめてバッチ処理する
        (GPUプロセス起動オーバーヘッドの削減が狙い)。"""
        if (self._closing or not self.ai_upscale_enabled or self._ai_thread_ref is not None
                or self._ai_pending_key is not None or self._batch_thread_ref is not None):
            return  # 既に1件(または1バッチ)実行中(GPU競合を避けるため同時に2件以上は動かさない)
        if not self._ai_queue:
            # AI処理が完全に落ち着いた(キューも実行中も無い)ので、その間保留していた
            # ウィンドウサイズの調整があれば、ここでまとめて行う。
            if self.zoom_mode == "fit":
                QTimer.singleShot(0, self._snap_fit_window_to_remove_margin)
            elif self.auto_resize_window:
                QTimer.singleShot(0, self._resize_window_to_image)
            return

        front_index = self._ai_queue[0][1][0]
        is_priority = front_index in (self.index, self.index + 1)

        engine_info = ENGINES.get(self.ai_upscale_engine, {})
        engine_is_in_process = bool(engine_info.get("in_process"))

        if (is_priority or not self.ai_batch_processing_enabled or engine_is_in_process
                or self._batch_failure_count.get(self._ai_queue[0][1], 0)):
            # in_process系エンジン(OpenVINO)はBatchAIUpscaleWorkerが最初から
            # 対応していない(サブプロセスではなくこのプロセス内で推論するため、
            # 複数枚をフォルダ入出力でまとめて処理する仕組みに乗らない)。
            # バッチ化を試みると必ず失敗するだけなので、そもそも試みずに
            # 常に単体処理する(無駄な失敗→エントリ消失サイクルを避けるため)。
            self._start_single_ai_job(self._ai_queue.pop(0))
            return

        # 先頭が背景(優先度低)のジョブなら、同条件(model_name/passes)の他の
        # 背景ジョブをキューから探し集めてバッチにまとめる。優先度の高い
        # ジョブ(現在/次ページ)はバッチ対象から除外し、キューにそのまま残す。
        front_model_name = self._ai_queue[0][3]
        front_passes = self._ai_queue[0][2]
        batch_items = []
        remaining_queue = []
        for entry in self._ai_queue:
            image_copy, cache_key, passes, model_name = entry
            idx = cache_key[0]
            matches = (model_name == front_model_name and passes == front_passes
                       and idx not in (self.index, self.index + 1))
            if matches and len(batch_items) < self.MAX_AI_BATCH and not self._batch_failure_count.get(cache_key, 0):
                batch_items.append(entry)
            else:
                remaining_queue.append(entry)

        if len(batch_items) < max(1, self.ai_batch_min_size):
            # まとめても意味が薄いくらい少数なら、先頭の1件だけ単体処理する
            self._start_single_ai_job(self._ai_queue.pop(0))
            return

        self._ai_queue = remaining_queue
        self._start_batch_ai_job(batch_items, front_model_name, front_passes)

    def _start_single_ai_job(self, entry):
        """1件のAIジョブを単体処理する(既存の経路。現在ページ/次ページや、
        バッチ化するほどの件数が無い背景ジョブに使う)。"""
        image_copy, cache_key, passes, model_name = entry
        self._ai_pending_key = cache_key
        debug_print(f"[AI QUEUE] single start {cache_key} queued={len(self._ai_queue)}")

        thread = QThread(self)
        engine_info = ENGINES.get(self.ai_upscale_engine, {})
        if engine_info.get("in_process"):
            gpu_id = None
            device = self.ai_openvino_device if self.ai_upscale_engine == "openvino" else None
        else:
            gpu_id = None if self.ai_gpu_id == "auto" else int(self.ai_gpu_id)
            device = None
        worker = AIUpscaleWorker(
            cache_key, image_copy, self.ai_upscale_engine, model_name, passes=passes,
            gpu_id=gpu_id, device=device
        )
        worker._generation = self._archive_generation
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_ai_upscale_finished)
        worker.failed.connect(self._on_ai_upscale_failed)
        worker.progress.connect(self._on_ai_upscale_progress)
        worker.pass_finished.connect(self._on_ai_upscale_pass_finished)
        worker.cancelled.connect(self._on_ai_upscale_cancelled)
        worker.finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(lambda: self._cleanup_ai_thread(thread, worker))

        self._ai_thread_ref = (thread, worker)
        thread.start()

    def _cleanup_ai_thread(self, thread, worker):
        if self._ai_thread_ref == (thread, worker):
            self._ai_thread_ref = None
            self._ai_pending_key = None
        retry_current = worker._cancel_requested or worker._generation != self._archive_generation
        thread.deleteLater()
        worker.deleteLater()
        self._resume_ai_work(retry_current)

    def _start_batch_ai_job(self, batch_items, model_name, passes):
        """背景の先読み分をまとめて1回のプロセス起動で処理する。
        現在ページ/次ページはこの経路を通らない(常に_start_single_ai_jobで
        即座に処理される)ので、レスポンスには影響しない。"""
        items = [(cache_key, image_copy) for image_copy, cache_key, _passes, _model in batch_items]
        self._batch_pending_keys = {cache_key for cache_key, _image in items}
        self._batch_pending_entries = batch_items  # キャンセル時にキューへ戻すため保持しておく

        debug_print(f"[AI BATCH DEBUG] {len(items)}件をバッチ処理開始 "
                    f"(model={model_name}, passes={passes}): {sorted(k[0] for k in self._batch_pending_keys)}")

        thread = QThread(self)
        if ENGINES.get(self.ai_upscale_engine, {}).get("in_process"):
            gpu_id = None
        else:
            gpu_id = None if self.ai_gpu_id == "auto" else int(self.ai_gpu_id)
        worker = BatchAIUpscaleWorker(items, self.ai_upscale_engine, model_name, passes=passes, gpu_id=gpu_id)
        worker._generation = self._archive_generation
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.item_finished.connect(self._on_batch_ai_item_finished)
        worker.batch_finished.connect(self._on_batch_ai_finished)
        worker.failed.connect(self._on_batch_ai_failed)
        worker.cancelled.connect(self._on_batch_ai_cancelled)
        worker.batch_finished.connect(thread.quit)
        worker.failed.connect(thread.quit)
        worker.cancelled.connect(thread.quit)
        thread.finished.connect(lambda: self._cleanup_batch_ai_thread(thread, worker))

        self._batch_thread_ref = (thread, worker)
        thread.start()

    def _cleanup_batch_ai_thread(self, thread, worker):
        if self._batch_thread_ref == (thread, worker):
            self._batch_thread_ref = None
        retry_current = worker._cancel_requested or worker._generation != self._archive_generation
        thread.deleteLater()
        worker.deleteLater()
        self._resume_ai_work(retry_current)

    def _resume_ai_work(self, retry_current=False):
        if self._closing:
            return
        debug_print(f"[AI QUEUE] released: queued={len(self._ai_queue)} generation={self._archive_generation}")
        if retry_current and self.reader is not None and self._waiting_for_index is None:
            self.render_current_pixmap()
        self._prune_ai_work_for_relevance()
        self._refresh_ai_prefetch()
        self._process_ai_queue()

    def _ai_sender_is_stale(self):
        worker = self.sender()
        return self._closing or (worker is not None and getattr(worker, "_generation", None) != self._archive_generation)

    def _on_batch_ai_item_finished(self, cache_key, image):
        """バッチ内の1件が完了するたびに呼ばれる(単体処理の_on_ai_upscale_finishedと
        ほぼ同じロジックだが、キューの次ジョブ起動は行わない=バッチ全体の完了を待つ)。"""
        if self._ai_sender_is_stale():
            return
        self._batch_pending_keys.discard(cache_key)
        index, rotation, aspect_mode = cache_key
        elapsed = self._pop_ai_processing_elapsed(cache_key)
        if self._is_ai_result_stale(cache_key):
            debug_print(f"[AI BATCH DEBUG] page{index}: 別アーカイブに切り替わった後の古い結果のため破棄します")
            self._pending_diff_composite.pop(cache_key, None)
            return
        diff_plan = self._pending_diff_composite.pop(cache_key, None)
        if diff_plan is not None:
            composited = self._composite_diff_result(
                diff_plan["base_image"], diff_plan["region_scaled"], image
            )
            self._ai_cache[index] = ((rotation, aspect_mode), composited)
        else:
            self._ai_cache[index] = ((rotation, aspect_mode), image)
        self._evict_distant_ai_cache_entries()
        if index == self.index and rotation == self.rotation and aspect_mode == self.aspect_mode:
            self._last_ai_processing_seconds = elapsed
            self.render_current_pixmap()

    def _on_batch_ai_finished(self):
        if self._ai_sender_is_stale():
            return
        self._batch_pending_keys = set()
        self._batch_pending_entries = []
        self._process_ai_queue()  # バッチが空けたので次のジョブ(単体/別バッチ)を続ける

    def _on_batch_ai_failed(self, message):
        if self._ai_sender_is_stale():
            return
        debug_print(f"[AI BATCH DEBUG] バッチ処理が失敗: {message}")
        self.last_ai_error = message

        # 完了通知済みの画像は残し、未完了分だけ単体処理へ切り替える。
        for entry in self._batch_pending_entries:
            cache_key = entry[1]
            if cache_key not in self._batch_pending_keys:
                continue
            failure_count = self._batch_failure_count.get(cache_key, 0) + 1
            self._batch_failure_count[cache_key] = failure_count
            if not any(existing_key == cache_key for _, existing_key, _, _ in self._ai_queue):
                self._ai_queue.append(entry)

        self._batch_pending_keys = set()
        self._batch_pending_entries = []
        self._process_ai_queue()

    def _on_batch_ai_cancelled(self):
        # 最終出力の通知途中にキャンセルされた場合も、未完了分だけ戻す。
        if self._ai_sender_is_stale():
            return
        for entry in self._batch_pending_entries:
            cache_key = entry[1]
            if cache_key not in self._batch_pending_keys:
                continue
            if not any(existing_key == cache_key for _, existing_key, _, _ in self._ai_queue):
                self._ai_queue.append(entry)
        self._batch_pending_entries = []
        self._batch_pending_keys = set()
        self._process_ai_queue()

    def _on_ai_upscale_progress(self, cache_key, current_pass, total_passes):
        if self._ai_sender_is_stale():
            return
        if self.isFullScreen():
            return  # 全画面時は表示しない(邪魔にならないように)
        if cache_key != (self.index, self.rotation, self.aspect_mode):
            return  # 既に別のページ等に移動している場合は表示しない
        if total_passes > 1:
            self.statusBar().showMessage(f"AI処理中... ({current_pass}/{total_passes}パス)")
        else:
            self.statusBar().showMessage("AI処理中...")

    def _prune_ai_work_for_relevance(self):
        """現在位置から離れすぎたAIジョブは、キューから削除し、実行中のものはキャンセルする。
        ページ送りでVRAM/CPU資源を無駄に使い続けないようにするため
        （AI処理が終わる前に別のページへ移動したら、その処理は打ち切る）。
        実行中のジョブが現在ページ/次ページのどちらでもない場合は、先読み設定に
        関わらず常にキャンセルする(実際に見ているページを最優先するため)。"""
        previous_keys = {entry[1] for entry in self._ai_queue}
        if self._ai_pending_key is not None:
            pending_index = self._ai_pending_key[0]
            if pending_index not in (self.index, self.index + 1):
                if self._ai_thread_ref is not None:
                    _, worker = self._ai_thread_ref
                    worker.request_cancel()

        # バッチジョブ(常に背景=優先度の低いページのみを含む)が実行中で、
        # かつ現在ページがまだAI処理されていない場合は、バッチを打ち切って
        # 実行枠を空け、現在ページの処理をすぐ始められるようにする。
        # (現在ページが既に処理済みなら、わざわざ背景バッチの進行を無駄にする
        # 必要は無いので、そのまま継続させる。)
        if self._batch_thread_ref is not None:
            current_cached = self._ai_cache.get(self.index)
            current_needs_ai = not (
                current_cached is not None and current_cached[0] == (self.rotation, self.aspect_mode)
            )
            if current_needs_ai:
                _, batch_worker = self._batch_thread_ref
                batch_worker.request_cancel()

        # 通り過ぎたページ(現在位置より後ろ、passed_pages_keep_countを超える分)の
        # キュー項目は、先読み範囲設定(ai_prefetch_depth)に関わらず常に取り除く。
        # 駆け足でページを送った場合、これらは既に見終わっており、AI処理する
        # 価値が薄いため(「前のアプスケはキューから外していい」という要望に対応)。
        if self.passed_pages_keep_count >= 0:
            passed_cutoff = self.index - self.passed_pages_keep_count
            self._ai_queue = [
                entry for entry in self._ai_queue
                if entry[1][0] >= passed_cutoff
            ]

        if self.ai_prefetch_depth < 0:
            # 全体を先読み対象にしている場合でも、キューの並び順は現在位置から
            # 近い順にしておく(そうしないと、駆け足で通り過ぎた直後に停止した時、
            # 停止位置からの続きより先に、既に通り過ぎた古い項目が処理されて
            # しまい、「止まった場所から先に進んでいく」という期待に反する)。
            self._ai_queue.sort(key=lambda entry: abs(entry[1][0] - self.index))
            self._discard_pruned_ai_metadata(previous_keys)
            return

        relevant_min = self.index - self.ai_prefetch_depth
        relevant_max = self.index + self.ai_prefetch_depth

        self._ai_queue = [
            entry for entry in self._ai_queue
            if relevant_min <= entry[1][0] <= relevant_max
        ]
        self._ai_queue.sort(key=lambda entry: abs(entry[1][0] - self.index))
        self._discard_pruned_ai_metadata(previous_keys)

    def _discard_pruned_ai_metadata(self, previous_keys):
        removed = previous_keys - {entry[1] for entry in self._ai_queue}
        for key in removed:
            self._pending_diff_composite.pop(key, None)
            self._ai_processing_start_time.pop(key, None)
            self._ai_job_generation.pop(key, None)

    def _evict_passed_pages(self):
        """現在位置より後ろ(既に通り過ぎた)のページを、passed_pages_keep_countより
        多く保持している場合、古い方から_image_cache/_ai_cacheを解放する。
        前方(まだ見ていない)のページは対象外(そちらは「メモリが潤沢な前提で
        全ページキャッシュしておく」という方針を維持する)。"""
        keep_count = self.passed_pages_keep_count
        if keep_count < 0:
            return  # 負の値なら無制限(解放しない)

        cutoff = self.index - keep_count  # これより小さいindexは解放対象
        evicted_raw = 0
        evicted_ai = 0
        for idx in list(self._image_cache.keys()):
            if idx < cutoff:
                del self._image_cache[idx]
                evicted_raw += 1
        for idx in list(self._ai_cache.keys()):
            if idx < cutoff:
                del self._ai_cache[idx]
                evicted_ai += 1

        if evicted_raw or evicted_ai:
            debug_print(f"[MEMORY] 通り過ぎたページを解放: 生画像={evicted_raw}件 AI結果={evicted_ai}件 "
                        f"(index={self.index}, cutoff={cutoff})")

    def _on_ai_upscale_pass_finished(self, cache_key, image, current_pass, total_passes):
        """多段(重ねがけ)AI処理の、1段完了ごとの中間結果。現在ページ宛なら
        その時点の品質ですぐ表示を更新する(全パス完了を待たない)。
        差分ベース処理中の場合は、クロップ結果を土台に合成してから保存する。"""
        if self._ai_sender_is_stale():
            return
        index, rotation, aspect_mode = cache_key
        if self._is_ai_result_stale(cache_key, consume=False):
            # consume=False: このcache_keyについて後で最終的なfinished通知が
            # 来るので、ここではまだ世代情報を消費しない(読み取りのみ)。
            debug_print(f"[AI DEBUG] page{index}: 別アーカイブに切り替わった後の古い中間結果のため破棄します")
            return
        diff_plan = self._pending_diff_composite.get(cache_key)
        if diff_plan is not None:
            composited = self._composite_diff_result(
                diff_plan["base_image"], diff_plan["region_scaled"], image
            )
            self._ai_cache[index] = ((rotation, aspect_mode), composited)
        else:
            self._ai_cache[index] = ((rotation, aspect_mode), image)
        if index == self.index and rotation == self.rotation and aspect_mode == self.aspect_mode:
            self.render_current_pixmap()
        if total_passes > 1 and not self.isFullScreen():
            self.statusBar().showMessage(f"AI処理中... ({current_pass}/{total_passes}パス完了)")

    def _on_ai_upscale_cancelled(self, cache_key):
        if self._ai_sender_is_stale():
            return
        cached = self._ai_cache.get(cache_key[0])
        if cached is not None and cached[0] == cache_key[1:]:
            self._ai_cache.pop(cache_key[0], None)  # 中間パスを完成済みとして残さない
        self._pop_ai_processing_elapsed(cache_key)
        debug_print(f"[AI DEBUG] page{cache_key[0]}: AI処理がキャンセルされました")
        self._ai_pending_key = None
        self._ai_job_generation.pop(cache_key, None)
        self._pending_diff_composite.pop(cache_key, None)
        if not self.isFullScreen() and cache_key == (self.index, self.rotation, self.aspect_mode):
            self.statusBar().clearMessage()
        self._process_ai_queue()

    def _on_ai_upscale_finished(self, cache_key, image, engine_key, model_name):
        if self._ai_sender_is_stale():
            return
        self._ai_pending_key = None
        index, rotation, aspect_mode = cache_key
        elapsed = self._pop_ai_processing_elapsed(cache_key)
        if self._is_ai_result_stale(cache_key):
            debug_print(f"[AI DEBUG] page{index}: 別アーカイブに切り替わった後の古い結果のため破棄します")
            self._pending_diff_composite.pop(cache_key, None)
            self._process_ai_queue()
            return
        diff_plan = self._pending_diff_composite.pop(cache_key, None)
        if diff_plan is not None:
            debug_print(f"[DIFF DISPATCH] page{index}: AIから返ってきた画像サイズ={image.width()}x{image.height()} "
                        f"(期待していたクロップサイズ={diff_plan['cropped_canvas'].width()}x{diff_plan['cropped_canvas'].height()}, "
                        f"土台サイズ={diff_plan['base_image'].width()}x{diff_plan['base_image'].height()}, "
                        f"region_scaled={diff_plan['region_scaled']})")
            composited = self._composite_diff_result(
                diff_plan["base_image"], diff_plan["region_scaled"], image
            )
            self._ai_cache[index] = ((rotation, aspect_mode), composited)
        else:
            self._ai_cache[index] = ((rotation, aspect_mode), image)
        self._evict_distant_ai_cache_entries()
        if index == self.index and rotation == self.rotation and aspect_mode == self.aspect_mode:
            if not self.isFullScreen():
                self.statusBar().clearMessage()
            self._last_ai_processing_seconds = elapsed
            self.render_current_pixmap()  # 現在表示中のページの結果なら差し替えて再表示
        self._process_ai_queue()  # キューに次のジョブがあれば続けて処理する

    def _is_ai_result_stale(self, cache_key, consume=True):
        """AI処理の結果が、既に切り替わった後の別アーカイブ向けの、遅れて届いた
        古い結果でないかを確認する。request_cancel()は非同期(即座には止まらない)
        ため、キャンセル要求と処理完了がレースし、別アーカイブに切り替わった後で
        古いアーカイブ向けの結果がfinished/item_finishedシグナルとして届くことがある。
        cache_key(index, rotation, aspect_mode)だけでは別アーカイブの同じ番号の
        ページと区別がつかず、誤って新アーカイブのキャッシュを汚染してしまうため、
        ジョブ発行時に記録しておいた世代(_archive_generation)と比較する。

        consume=True(既定): 確認後にエントリを削除する。最終的な完了/失敗/
            キャンセル通知(そのcache_keyについてもう後続の通知が来ない)で使う。
        consume=False: 読み取りのみ行い、エントリは残す。多段パス処理の
            中間進捗通知(同じcache_keyについて後で本当の完了通知が来る)で使う
            -ここでpopしてしまうと、後続の完了通知が世代情報を参照できなくなる。"""
        if consume:
            job_generation = self._ai_job_generation.pop(cache_key, None)
        else:
            job_generation = self._ai_job_generation.get(cache_key)
        if job_generation is None:
            return True  # 発行記録のない結果は採用しない
        return job_generation != self._archive_generation

    def _pop_ai_processing_elapsed(self, cache_key):
        """cache_keyに対応するAI処理の開始時刻を取り出し、経過秒数を返す。
        記録が無ければNone(先読み等で開始時刻を記録し損ねた場合の保険)。"""
        start_time = self._ai_processing_start_time.pop(cache_key, None)
        if start_time is None:
            return None
        return time.time() - start_time

    def _update_debug_info_label(self):
        """デバッグログ有効時、画面左上に現在ページの元データ量/現在のデータ量/
        AI処理所要時間を表示する。デバッグ無効時は非表示にする。"""
        if not _debug_state.get("enabled"):
            self.debug_info_label.hide()
            return
        if self.original_pixmap is None:
            self.debug_info_label.hide()
            return

        original_bytes = self.original_pixmap.toImage().sizeInBytes()
        lines = [f"元データ: {human_size(original_bytes)}"]

        cached = self._ai_cache.get(self.index)
        if cached is not None and cached[0] == (self.rotation, self.aspect_mode):
            current_bytes = cached[1].sizeInBytes()
            lines.append(f"現在: {human_size(current_bytes)} (AI処理済み)")
        else:
            lines.append(f"現在: {human_size(original_bytes)} (未処理)")

        if self._last_ai_processing_seconds is not None:
            lines.append(f"処理時間: {self._last_ai_processing_seconds:.2f}秒")

        self.debug_info_label.setText("\n".join(lines))
        self.debug_info_label.adjustSize()
        self.debug_info_label.move(10, 10)
        self.debug_info_label.raise_()
        self.debug_info_label.show()

    def _on_ai_upscale_failed(self, cache_key, message):
        if self._ai_sender_is_stale():
            return
        debug_print(f"[AI DEBUG] page{cache_key[0]}: AI処理が失敗しました: {message}")
        self._ai_pending_key = None
        self._ai_job_generation.pop(cache_key, None)
        self._pending_diff_composite.pop(cache_key, None)
        self._ai_failed_keys.add(cache_key)
        self._pop_ai_processing_elapsed(cache_key)
        cached = self._ai_cache.get(cache_key[0])
        if cached is not None and cached[0] == cache_key[1:]:
            self._ai_cache.pop(cache_key[0], None)
        self.last_ai_error = message  # プロパティダイアログで詳細を確認できるように保持する
        if not self.isFullScreen() and cache_key == (self.index, self.rotation, self.aspect_mode):
            self.statusBar().showMessage("AI処理に失敗しました（詳細はプロパティ参照）", 4000)
        # 失敗時はキャッシュを更新しない(既に表示されている通常スケーリング版のままにする)
        self._process_ai_queue()

    def _apply_vram_mode(self, enabled):
        """VRAM展開モード(実験的): スクロールエリアのビューポートをQOpenGLWidgetに
        切り替え、ハードウェア(GPU)アクセラレーションによる描画を使う。
        GPU/ドライバの状況によっては失敗することがあるため、失敗時は通常の
        (CPU側の)描画に自動的にフォールバックする。"""
        try:
            if enabled:
                gl_widget = QOpenGLWidget()
                self.scroll_area.setViewport(gl_widget)
                # ビューポートを差し替えると、クリック領域判定等のイベントフィルタが
                # 新しいビューポートに対しても効くよう、再インストールする必要がある
                self.scroll_area.viewport().installEventFilter(self)
            else:
                self.scroll_area.setViewport(QWidget())
                self.scroll_area.viewport().installEventFilter(self)
            return True
        except Exception as e:
            self.statusBar().showMessage(f"VRAM展開モードの切替に失敗しました: {e}", 5000)
            return False

    def toggle_vram_mode(self, checked):
        ok = self._apply_vram_mode(checked)
        self.vram_mode_enabled = checked if ok else False
        self.settings["vram_mode_enabled"] = self.vram_mode_enabled
        save_settings(self.settings)
        self.render_current_pixmap()

    def toggle_ai_upscale(self, checked):
        self.ai_upscale_enabled = checked
        self._invalidate_ai_work()
        self.settings["ai_upscale_enabled"] = checked
        save_settings(self.settings)
        self.render_current_pixmap()

    def set_ai_upscale_engine_and_model(self, engine_key, model_name):
        self.ai_upscale_engine = engine_key
        self.ai_upscale_model = model_name
        self.settings["ai_upscale_engine"] = engine_key
        self.settings["ai_upscale_model"] = model_name
        save_settings(self.settings)
        # モデルが変わったのでキャッシュは無効化し、次の描画で再計算させる
        self._invalidate_ai_work()
        self.render_current_pixmap()

    def zoom_by_step(self, steps, step_size=10, min_pct=10, max_pct=800):
        """Ctrl+ホイール等での段階的なズーム。現在の実効表示倍率を基準に、
        ステップ数(正=拡大、負=縮小)だけパーセントを変化させる。"""
        if self.original_pixmap is None or self.original_pixmap.isNull() or steps == 0:
            return

        if self.zoom_mode == "fit":
            # "ウィンドウに合わせる"表示中は、今の実効倍率を基準に切り替える
            displayed = self.image_label.pixmap()
            if displayed is not None and not displayed.isNull() and self.original_pixmap.width() > 0:
                current_pct = round(displayed.width() / self.original_pixmap.width() * 100)
            else:
                current_pct = 100
        else:
            try:
                current_pct = int(self.zoom_mode)
            except ValueError:
                current_pct = 100

        new_pct = current_pct + steps * step_size
        new_pct = int(round(max(min_pct, min(max_pct, new_pct))))
        self.set_zoom_mode(str(new_pct))

    def set_aspect_mode(self, mode):
        self.aspect_mode = mode
        self.settings["aspect_mode"] = mode
        save_settings(self.settings)
        for m, action in self.aspect_actions.items():
            action.setChecked(m == mode)
        self.render_current_pixmap()

    def cycle_aspect_mode(self):
        idx = ASPECT_MODES.index(self.aspect_mode)
        next_mode = ASPECT_MODES[(idx + 1) % len(ASPECT_MODES)]
        self.set_aspect_mode(next_mode)

    def rotate_right(self):
        self.rotation = (self.rotation + 90) % 360
        self.settings["rotation"] = self.rotation
        save_settings(self.settings)
        self.render_current_pixmap()

    def rotate_left(self):
        self.rotation = (self.rotation - 90) % 360
        self.settings["rotation"] = self.rotation
        save_settings(self.settings)
        self.render_current_pixmap()

    def _check_fullscreen_slider_hover(self):
        """全画面時、マウスが画面下端に近づいたらスライダーを半透明表示し、
        離れたら少し待って隠す(ポーリング方式。子ウィジェットへのイベント伝播に
        依存する方式より確実に動く)。また、マウスが一定時間動かなければ
        カーソル自体も隠す(動画プレイヤー等でおなじみの挙動)。"""
        if not self.isFullScreen():
            self._fullscreen_slider_check_timer.stop()
            return

        current_pos = QCursor.pos()
        if current_pos == self._last_cursor_pos:
            self._cursor_idle_ticks += 1
        else:
            self._cursor_idle_ticks = 0
            self._last_cursor_pos = current_pos
            if self.cursor().shape() == Qt.BlankCursor:
                self.unsetCursor()
        if self._cursor_idle_ticks * 200 >= 2000 and self.cursor().shape() != Qt.BlankCursor:
            self.setCursor(Qt.BlankCursor)

        pos = self.mapFromGlobal(current_pos)
        threshold = 50
        near_bottom = (
            0 <= pos.x() <= self.width()
            and self.height() - threshold <= pos.y() <= self.height() + 10
        )
        if near_bottom:
            self._show_fullscreen_slider()
        elif self.fullscreen_slider.isVisible() and not self.fullscreen_slider.underMouse():
            self.fullscreen_slider.hide()

    def _show_fullscreen_slider(self):
        w = int(self.width() * 0.6)
        self.fullscreen_slider.setFixedWidth(w)
        x = (self.width() - w) // 2
        y = self.height() - 36
        self.fullscreen_slider.move(x, y)
        self.fullscreen_slider.raise_()
        self.fullscreen_slider.show()

    def _get_thumbnail_for_index(self, index, max_size=180):
        """先読みキャッシュに既にあるページ画像から、プレビュー用の小さな
        サムネイルを作る。まだキャッシュに無いページはNoneを返す
        (先読みが完了するまで待つ; アーカイブ全体を先読みする設計なので
        通常はほぼ全ページ揃っている)。"""
        cached = self._image_cache.get(index)
        if cached is None:
            return None
        _, image = cached
        pixmap = QPixmap.fromImage(image)
        return pixmap.scaled(max_size, max_size, Qt.KeepAspectRatio, Qt.SmoothTransformation)

    def _on_page_slider_moved(self, value):
        thumb = self._get_thumbnail_for_index(value)
        if thumb is None:
            self.thumb_preview_label.hide()
            return

        slider = self.sender()
        if slider is None:
            return

        self.thumb_preview_label.setPixmap(thumb)
        self.thumb_preview_label.resize(thumb.size())

        parent = self.thumb_preview_label.parentWidget()
        span = max(1, slider.maximum() - slider.minimum())
        ratio = (value - slider.minimum()) / span
        slider_top_left_global = slider.mapToGlobal(slider.rect().topLeft())
        slider_local = parent.mapFromGlobal(slider_top_left_global)

        x = slider_local.x() + int(ratio * slider.width()) - thumb.width() // 2
        y = slider_local.y() - thumb.height() - 10
        x = max(0, min(parent.width() - thumb.width(), x))
        y = max(0, y)
        self.thumb_preview_label.move(x, y)
        self.thumb_preview_label.raise_()
        self.thumb_preview_label.show()

    def _on_page_slider_released(self):
        slider = self.sender()
        self.thumb_preview_label.hide()
        if slider is None or self.reader is None:
            return
        value = slider.value()
        if 0 <= value < len(self.reader.image_names):
            self.index = value
            self.show_current_image()

    def _show_osd(self, text):
        """D/Uキーでのモード切替時などに、画面中央下寄りに1秒間だけメッセージを
        表示する。ナビバー/ステータスバーが隠れる全画面時でも見えるよう、
        それらとは独立したフローティングラベルを使う。"""
        self.osd_label.setText(text)
        self.osd_label.adjustSize()
        parent_size = self.osd_label.parentWidget().size()
        x = (parent_size.width() - self.osd_label.width()) // 2
        y = parent_size.height() - self.osd_label.height() - 60
        self.osd_label.move(max(0, x), max(0, y))
        self.osd_label.raise_()
        self.osd_label.show()
        self._osd_timer.start(1000)

    def toggle_fullscreen(self):
        if self.isFullScreen():
            # 枠なし(FramelessWindowHint)を解除してから通常表示に戻す
            self.setWindowFlags(self.windowFlags() & ~Qt.FramelessWindowHint)
            self.showNormal()  # 直前のウィンドウサイズに自然に戻る
            self.show()
            self.nav_bar.show()
            self.statusBar().show()
            self._fullscreen_slider_check_timer.stop()
            self.fullscreen_slider.hide()
            self.unsetCursor()  # 隠していたマウスカーソルを元に戻す
            if self.fullscreen_exit_mode == "100" and self.original_pixmap is not None:
                # 「100%」を選んでいる場合は、画像のネイティブサイズに合わせて
                # ウィンドウサイズを変更し直す(直前のサイズへの復元を上書きする)。
                canvas = self._compute_canvas(self.original_pixmap)
                chrome_height = self._get_chrome_height()
                target_w = canvas.width()
                target_h = canvas.height() + chrome_height
                screen = QApplication.primaryScreen()
                if screen is not None:
                    avail = screen.availableGeometry()
                    target_w = min(target_w, avail.width())
                    target_h = min(target_h, avail.height())
                self.resize(target_w, target_h)
        else:
            # 枠なしにしてから全画面化する(タイトルバー・タスクバーを確実に隠す)。
            # 自前のナビバー・ステータスバーも隠し、画像だけの「本当の全画面」にする。
            self.setWindowFlags(self.windowFlags() | Qt.FramelessWindowHint)
            self.showFullScreen()
            self.nav_bar.hide()
            self.statusBar().hide()
            self._fullscreen_slider_check_timer.start(200)
        # フルスクリーン切替直後はビューポートサイズが変わるので、
        # fitモードなら再レンダリングが必要
        self.render_current_pixmap()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        debug_print(f"[RESIZE DEBUG] resizeEvent -> size={self.width()}x{self.height()} "
              f"zoom_mode={self.zoom_mode} auto_resize_window={self.auto_resize_window} "
              f"ai_pending={self._ai_pending_key} ai_queue_len={len(self._ai_queue)}")
        if self.zoom_mode == "fit":
            self.render_current_pixmap()
            # ドラッグ中は追従表示するだけにし、ドラッグが止まってから(一定時間
            # リサイズが来なくなってから)余白を消す方向にウィンドウサイズを
            # 微調整する(マウスを離した後に「カチッ」と余白なしサイズに収まる)。
            self._fit_resize_debounce_timer.start(250)
        elif self.auto_resize_window:
            # 手動でウィンドウをドラッグしてリサイズした後も、画像に合わせて
            # ウィンドウサイズを調整し直す(黒い余白が残らないようにする)。
            QTimer.singleShot(0, self._resize_window_to_image)

    def _get_chrome_height(self):
        """画像表示エリア以外が占めている、ウィンドウ内の縦方向の合計高さを返す。
        (ナビバー + ステータスバー。全画面時はどちらも隠しているので0)。
        以前はナビバーの高さしか数えておらず、ステータスバーの高さ(通常20-30px程度)が
        毎回計算から漏れていたため、「余白を消す」処理がステータスバーの分だけ
        余白を見誤り、際限なくウィンドウを縮め続けるバグの原因になっていた。"""
        if self.isFullScreen():
            return 0
        nav_bar_height = self.path_label.parentWidget().height() if self.path_label.parentWidget() else 0
        status_bar_height = self.statusBar().height() if self.statusBar().isVisible() else 0
        return nav_bar_height + status_bar_height

    def _snap_fit_window_to_remove_margin(self):
        """「ウィンドウに合わせる」モードで手動リサイズが終わった後、画像の
        アスペクト比に合わせてウィンドウサイズを微調整し、黒い余白(レターボックス)
        を消す。ドラッグでおおよそ合わせたサイズより大きくはせず、余白のある方の
        辺だけを縮める(= 元々余白が無かった辺の長さはそのまま維持する)。"""
        print(f"[RESIZE DEBUG] _snap_fit_window_to_remove_margin called, "
              f"zoom_mode={self.zoom_mode} ai_pending={self._ai_pending_key} "
              f"ai_queue_len={len(self._ai_queue)}")
        if self.zoom_mode != "fit":
            return
        if self.isMaximized() or self.isFullScreen():
            return
        if self._ai_pending_key is not None or self._ai_queue:
            # AI処理が進行中は、ウィンドウサイズをここで変えない。
            # (AI結果が届くたびにビューポートサイズが変わり、それがまた
            # AI処理のやり直しを招く…という無限ループになりかねないため)
            debug_print("[RESIZE DEBUG] -> AI処理中なのでスキップ")
            return
        if self.original_pixmap is None or self.original_pixmap.isNull():
            return

        canvas = self._compute_canvas(self.original_pixmap)
        if canvas.width() <= 0 or canvas.height() <= 0:
            return

        chrome_height = self._get_chrome_height()
        viewport = self.scroll_area.viewport().size()
        cur_w, cur_h = viewport.width(), viewport.height()
        if cur_w <= 0 or cur_h <= 0:
            return

        image_ratio = canvas.width() / canvas.height()
        viewport_ratio = cur_w / cur_h

        if viewport_ratio > image_ratio:
            # 横方向に余白(ピラーボックス)がある -> 幅だけ縮めて合わせる
            new_w = round(cur_h * image_ratio)
            new_h = cur_h
        else:
            # 縦方向に余白(レターボックス)がある -> 高さだけ縮めて合わせる
            new_w = cur_w
            new_h = round(cur_w / image_ratio)

        target_w = new_w
        target_h = new_h + chrome_height

        debug_print(f"[RESIZE DEBUG] snap calc: canvas={canvas.width()}x{canvas.height()} "
              f"viewport={cur_w}x{cur_h} chrome_height={chrome_height} "
              f"-> target={target_w}x{target_h} current_window={self.width()}x{self.height()}")

        if (target_w, target_h) == (self.width(), self.height()):
            return  # 既に余白なしサイズなので何もしない(無限リサイズループ防止)

        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            target_w = min(target_w, avail.width())
            target_h = min(target_h, avail.height())

        self.resize(target_w, target_h)

    def _resize_window_to_image(self):
        """現在表示中の画像のピクセルサイズに合わせて、ウィンドウ全体のサイズを変える。
        （手動でウィンドウをドラッグして大きさを変えても、画像の周りに余白
        （白背景）が出る代わりに、ウィンドウ自体が画像に合わせて変わるようにする）"""
        if self._ai_pending_key is not None or self._ai_queue:
            # AI処理が進行中は、ウィンドウサイズをここで変えない(理由は
            # _snap_fit_window_to_remove_marginのコメントと同じ)。
            debug_print("[RESIZE DEBUG] _resize_window_to_image: AI処理中なのでスキップ")
            return
        pixmap = self.image_label.pixmap()
        if pixmap is None or pixmap.isNull():
            return

        chrome_height = self._get_chrome_height()
        target_w = pixmap.width()
        target_h = pixmap.height() + chrome_height
        debug_print(f"[RESIZE DEBUG] _resize_window_to_image: pixmap={pixmap.width()}x{pixmap.height()} "
              f"chrome_height={chrome_height} -> target={target_w}x{target_h} "
              f"current_window={self.width()}x{self.height()}")

        screen = QApplication.primaryScreen()
        if screen is not None:
            avail = screen.availableGeometry()
            target_w = min(target_w, avail.width())
            target_h = min(target_h, avail.height())

        if not self.isMaximized() and not self.isFullScreen():
            if (target_w, target_h) != (self.width(), self.height()):
                self.resize(target_w, target_h)

    def toggle_auto_resize_window(self, checked):
        self.auto_resize_window = checked
        self.settings["auto_resize_window"] = checked
        save_settings(self.settings)
        self._update_scrollbar_policy()
        if checked and self.zoom_mode != "fit":
            self._resize_window_to_image()

    def toggle_diff_based_upscale(self, checked):
        self.ai_diff_based_enabled = checked
        self.settings["ai_diff_based_enabled"] = checked
        save_settings(self.settings)

    def _update_scrollbar_policy(self):
        """自動ウィンドウリサイズが有効な間は、常にウィンドウが画像サイズに
        追従する設計なのでスクロールバーは不要（表示すると画像の端を覆って
        しまうため、常に隠す）。無効な時は通常通り、必要な時だけ表示する。"""
        if self.auto_resize_window:
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        else:
            self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
            self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

    # ---- フォルダ一覧のタイル右クリックメニュー ----
    def _show_tile_context_menu(self, path_str, kind, global_pos):
        menu = QMenu(self)
        path = Path(path_str)

        action_open = QAction("開く", self)
        if kind == "folder":
            action_open.triggered.connect(lambda: self.navigate_to_folder(path_str))
        else:
            action_open.triggered.connect(lambda: self.navigate_to_archive(path_str))
        menu.addAction(action_open)

        action_properties = QAction("プロパティ", self)
        action_properties.triggered.connect(lambda: self._show_tile_properties(path, kind))
        menu.addAction(action_properties)

        action_show_in_explorer = QAction("ファイルの場所をエクスプローラーで開く", self)
        action_show_in_explorer.triggered.connect(lambda: self._reveal_in_explorer(path))
        menu.addAction(action_show_in_explorer)

        menu.exec(global_pos)

    def _show_tile_properties(self, path: Path, kind: str):
        try:
            st = path.stat()
        except Exception as e:
            QMessageBox.warning(self, "プロパティ", f"情報を取得できませんでした:\n{e}")
            return

        kind_label = {"folder": "フォルダ", "zip": "ZIPアーカイブ", "rar": "RARアーカイブ"}.get(kind, kind)
        text = (
            f"名前: {path.name}\n"
            f"種類: {kind_label}\n"
            f"場所: {path.parent}\n"
        )
        if path.is_file():
            text += f"サイズ: {human_size(st.st_size)}\n"
        text += f"更新日時: {datetime.fromtimestamp(st.st_mtime).strftime('%Y-%m-%d %H:%M:%S')}"

        QMessageBox.information(self, "プロパティ", text)

    def _reveal_in_explorer(self, path: Path):
        """Windowsのエクスプローラーで、指定したファイル/フォルダを選択状態で開く。"""
        try:
            if sys.platform == "win32":
                if path.is_dir():
                    os.startfile(str(path))
                else:
                    subprocess.run(["explorer", "/select,", str(path)])
            elif sys.platform == "darwin":
                subprocess.run(["open", "-R", str(path)])
            else:
                subprocess.run(["xdg-open", str(path.parent)])
        except Exception as e:
            QMessageBox.warning(self, "エラー", f"開けませんでした:\n{e}")

    # ---- 右クリックメニュー ----
    def show_context_menu(self, pos):
        # ビューア表示中とフォルダ一覧表示中で、右クリックメニューの内容を切り替える。
        if self.stacked.currentIndex() == 1:
            self._show_explorer_context_menu(pos)
        else:
            self._show_viewer_context_menu(pos)

    def _show_explorer_context_menu(self, pos):
        """フォルダ一覧（空白部分）を右クリックした時の、簡易版メニュー。
        タイル自体を右クリックした場合は_show_tile_context_menuが別途処理する。"""
        menu = QMenu(self)

        menu.addAction(self.actions_by_id["open_file"])
        menu.addAction(self.actions_by_id["toggle_fullscreen"])
        menu.addSeparator()

        up_action = QAction("上のフォルダへ", self)
        up_action.triggered.connect(self.go_up)
        up_action.setEnabled(self.up_button.isEnabled())
        menu.addAction(up_action)

        icon_menu = menu.addMenu("アイコンサイズ")
        action_zoom_in = QAction("拡大", self)
        action_zoom_in.triggered.connect(lambda: self.folder_browser.zoom_icon_size(1))
        action_zoom_in.setEnabled(self.folder_browser.icon_size_level != self.folder_browser.ICON_SIZE_ORDER[-1])
        icon_menu.addAction(action_zoom_in)

        action_zoom_out = QAction("縮小", self)
        action_zoom_out.triggered.connect(lambda: self.folder_browser.zoom_icon_size(-1))
        action_zoom_out.setEnabled(self.folder_browser.icon_size_level != self.folder_browser.ICON_SIZE_ORDER[0])
        icon_menu.addAction(action_zoom_out)

        icon_menu.addSeparator()
        for level, label in (("small", "小"), ("medium", "中"), ("large", "大"), ("xlarge", "特大(4K向け)")):
            action = QAction(label, self)
            action.setCheckable(True)
            action.setChecked(self.folder_browser.icon_size_level == level)
            action.triggered.connect(lambda checked=False, lv=level: self.folder_browser.set_icon_size(lv))
            icon_menu.addAction(action)

        menu.addSeparator()
        action_options = QAction("オプション...", self)
        action_options.triggered.connect(self.show_options_dialog)
        menu.addAction(action_options)

        action_about = QAction("バージョン情報...", self)
        action_about.triggered.connect(self.show_about_dialog)
        menu.addAction(action_about)

        menu.addAction(self.actions_by_id["quit"])

        menu.exec(self.mapToGlobal(pos))

    def _show_viewer_context_menu(self, pos):
        # 開く / ページ / 拡大縮小 / アスペクト比 / 表示 /
        # オプション / バージョン情報 / 終了
        menu = QMenu(self)
        has_archive = self.reader is not None and bool(self.reader.image_names)

        # ---- 開く ----
        menu.addAction(self.actions_by_id["open_file"])

        # ---- 全画面表示（すぐ使えるようトップレベルにも置く） ----
        menu.addAction(self.actions_by_id["toggle_fullscreen"])

        menu.addSeparator()

        # ---- ページ ----
        nav_menu = menu.addMenu("ページ")
        for action_id in ("next_page", "prev_page", "first_page", "last_page"):
            action = self.actions_by_id[action_id]
            action.setEnabled(has_archive)
            nav_menu.addAction(action)

        # ---- 拡大縮小 / 回転 ----
        zoom_menu = menu.addMenu("拡大縮小")
        zoom_menu.addAction(self.actions_by_id["zoom_100"])
        zoom_menu.addAction(self.actions_by_id["zoom_fit"])
        zoom_menu.addSeparator()
        zoom_menu.addAction(self.actions_by_id["zoom_200"])
        zoom_menu.addAction(self.actions_by_id["zoom_300"])
        zoom_menu.addAction(self.actions_by_id["zoom_400"])
        zoom_menu.addAction(self.actions_by_id["zoom_500"])
        zoom_menu.addSeparator()
        zoom_menu.addAction(self.actions_by_id["rotate_right"])
        zoom_menu.addAction(self.actions_by_id["rotate_left"])
        zoom_menu.addSeparator()
        zoom_menu.addAction(self.actions_by_id["toggle_fullscreen"])

        # ---- アスペクト比 ----
        aspect_menu = menu.addMenu("アスペクト比")
        for mode in ASPECT_MODES:
            aspect_menu.addAction(self.aspect_actions[mode])

        menu.addSeparator()

        # ---- 表示 ----
        view_menu = menu.addMenu("表示")
        view_menu.addAction(self.actions_by_id["toggle_always_on_top"])

        action_auto_resize = QAction("ウィンドウサイズを画像に合わせる", self)
        action_auto_resize.setCheckable(True)
        action_auto_resize.setChecked(self.auto_resize_window)
        action_auto_resize.toggled.connect(self.toggle_auto_resize_window)
        view_menu.addAction(action_auto_resize)

        action_diff_based = QAction("AI差分ベース高速化", self)
        action_diff_based.setCheckable(True)
        action_diff_based.setChecked(self.ai_diff_based_enabled)
        action_diff_based.toggled.connect(self.toggle_diff_based_upscale)
        view_menu.addAction(action_diff_based)

        view_menu.addSeparator()
        action_properties = QAction("プロパティ", self)
        action_properties.triggered.connect(self.show_properties_dialog)
        action_properties.setEnabled(has_archive)
        view_menu.addAction(action_properties)

        menu.addSeparator()

        # ---- オプション ----
        action_options = QAction("オプション...", self)
        action_options.triggered.connect(self.show_options_dialog)
        menu.addAction(action_options)

        menu.addSeparator()

        # ---- バージョン情報 / 終了 ----
        action_about = QAction("バージョン情報...", self)
        action_about.triggered.connect(self.show_about_dialog)
        menu.addAction(action_about)

        action_open_log = QAction("デバッグログを開く", self)
        action_open_log.triggered.connect(self.open_debug_log)
        menu.addAction(action_open_log)

        menu.addAction(self.actions_by_id["quit"])

        menu.exec(self.mapToGlobal(pos))

    def toggle_always_on_top(self, checked):
        self.settings["always_on_top"] = checked
        save_settings(self.settings)
        flags = self.windowFlags()
        if checked:
            flags |= Qt.WindowStaysOnTopHint
        else:
            flags &= ~Qt.WindowStaysOnTopHint
        self.setWindowFlags(flags)
        self.show()  # フラグ変更後はshow()し直さないと反映されない

    def open_debug_log(self):
        log_path = _get_debug_log_path()
        if not log_path.exists():
            QMessageBox.information(self, "デバッグログ", "まだログファイルがありません。")
            return
        try:
            if sys.platform == "win32":
                os.startfile(str(log_path))
            elif sys.platform == "darwin":
                subprocess.run(["open", str(log_path)])
            else:
                subprocess.run(["xdg-open", str(log_path)])
        except Exception as e:
            QMessageBox.warning(self, "エラー", f"開けませんでした:\n{e}\n\nパス: {log_path}")

    def show_about_dialog(self):
        QMessageBox.about(
            self,
            "バージョン情報",
            f"{APP_NAME} {APP_VERSION}\n\n"
            "フォルダやアーカイブ内の画像を先読み表示し、\n"
            "任意の外部AIエンジンでアップスケールするビューアです。\n\n"
            "対応: ZIP / RAR / 7z / TLG5 / TLG6\n"
            "GUI: PySide6\n"
            "License: Apache-2.0"
        )



    def show_properties_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("プロパティ")
        dialog.resize(440, 520)
        outer_layout = QVBoxLayout(dialog)

        if self.reader is None or not self.reader.image_names:
            outer_layout.addWidget(QLabel("画像が開かれていません。"))
        else:
            name = self.reader.image_names[self.index]

            # ---- アーカイブ情報 ----
            archive_group = QGroupBox("アーカイブ")
            archive_layout = QFormLayout(archive_group)
            archive_layout.addRow("パス:", QLabel(str(self.current_archive_path)))
            archive_layout.addRow("形式:", QLabel(self.reader.kind.upper()))
            archive_layout.addRow("内包する画像数:", QLabel(str(len(self.reader.image_names))))
            total_size = self.reader.get_total_uncompressed_size()
            if total_size is not None:
                archive_layout.addRow("展開後の合計サイズ:", QLabel(human_size(total_size)))
            if self.reader._working_password is not None:
                archive_layout.addRow("パスワード:", QLabel("password_list.txt から自動突破済み"))
            else:
                archive_layout.addRow("パスワード:", QLabel("なし（保護されていないアーカイブ）"))
            outer_layout.addWidget(archive_group)

            # ---- ファイル情報（アーカイブ内エントリのメタデータ） ----
            file_group = QGroupBox("ファイル")
            file_layout = QFormLayout(file_group)
            file_layout.addRow("ファイル名:", QLabel(name))
            file_layout.addRow("ページ:", QLabel(f"{self.index + 1} / {len(self.reader.image_names)}"))

            info = self.current_entry_info
            if info is not None:
                uncompressed = getattr(info, "file_size", None)
                compressed = getattr(info, "compress_size", None)
                file_layout.addRow("展開後のサイズ:", QLabel(human_size(uncompressed)))
                file_layout.addRow("圧縮後のサイズ:", QLabel(human_size(compressed)))
                if uncompressed and compressed and uncompressed > 0:
                    ratio = 100 * (1 - compressed / uncompressed)
                    file_layout.addRow("圧縮率:", QLabel(f"{ratio:.1f}%"))

                if self.reader.kind == "zip":
                    ctype = getattr(info, "compress_type", None)
                    file_layout.addRow(
                        "圧縮方式:",
                        QLabel(ZIP_COMPRESSION_NAMES.get(ctype, f"不明 (コード {ctype})"))
                    )
                    crc = getattr(info, "CRC", None)
                    if crc is not None:
                        file_layout.addRow("CRC32:", QLabel(f"{crc:08X}"))
                else:
                    ctype = getattr(info, "compress_type", None)
                    if ctype is not None:
                        file_layout.addRow("圧縮方式コード:", QLabel(f"{ctype} (RAR固有)"))
                    crc = getattr(info, "CRC", None) or getattr(info, "crc", None)
                    if crc is not None:
                        file_layout.addRow("CRC32:", QLabel(f"{crc:08X}" if isinstance(crc, int) else str(crc)))

                date_time = getattr(info, "date_time", None)
                if date_time:
                    file_layout.addRow("更新日時（アーカイブ内）:", QLabel(format_entry_date(date_time)))
            outer_layout.addWidget(file_group)

            # ---- 画像情報 ----
            image_group = QGroupBox("画像")
            image_layout = QFormLayout(image_group)
            if self.original_pixmap is not None and not self.original_pixmap.isNull():
                image_layout.addRow(
                    "解像度（元画像）:",
                    QLabel(f"{self.original_pixmap.width()} x {self.original_pixmap.height()} px")
                )
            displayed = self.image_label.pixmap()
            if displayed is not None and not displayed.isNull():
                image_layout.addRow(
                    "表示中のサイズ:",
                    QLabel(f"{displayed.width()} x {displayed.height()} px")
                )

            if self.current_pil_info is None and self.current_raw_data is not None:
                self.current_pil_info = get_pil_image_info(self.current_raw_data)
            pil_info = self.current_pil_info
            if pil_info is not None:
                image_layout.addRow("フォーマット:", QLabel(str(pil_info.get("format") or "不明")))
                image_layout.addRow("カラーモード:", QLabel(str(pil_info.get("mode") or "不明")))
                dpi = pil_info.get("dpi")
                if dpi:
                    image_layout.addRow("DPI:", QLabel(f"{dpi[0]:.0f} x {dpi[1]:.0f}"))
                image_layout.addRow("ICCプロファイル:", QLabel(pil_info.get("icc_profile", "不明")))
                orientation = pil_info.get("exif_orientation")
                if orientation is not None:
                    image_layout.addRow("EXIF Orientation:", QLabel(str(orientation)))

            if self.current_qimage_depth is not None:
                image_layout.addRow("ビット深度:", QLabel(f"{self.current_qimage_depth} bit"))
            if self.current_qimage_has_alpha is not None:
                image_layout.addRow("アルファチャンネル:", QLabel("あり" if self.current_qimage_has_alpha else "なし"))
            if self.current_data_size is not None:
                image_layout.addRow("デコード前データサイズ:", QLabel(human_size(self.current_data_size)))

            outer_layout.addWidget(image_group)

            # ---- メモリ使用状況(先読みキャッシュ・AIアップスケール結果) ----
            memory_group = QGroupBox("メモリ使用状況")
            memory_layout = QFormLayout(memory_group)

            raw_count = len(self._image_cache)
            raw_bytes = sum(img.sizeInBytes() for _data, img in self._image_cache.values())
            memory_layout.addRow("先読み済み(生画像):", QLabel(f"{raw_count} ページ / {human_size(raw_bytes)}"))

            ai_count = len(self._ai_cache)
            ai_bytes = sum(img.sizeInBytes() for _key, img in self._ai_cache.values())
            memory_layout.addRow("AIアップスケール済み:", QLabel(f"{ai_count} ページ / {human_size(ai_bytes)}"))

            memory_layout.addRow("合計:", QLabel(human_size(raw_bytes + ai_bytes)))
            outer_layout.addWidget(memory_group)

            # ---- 表示設定（現在の状態） ----
            view_group = QGroupBox("表示設定")
            view_layout = QFormLayout(view_group)
            zoom_label = "ウィンドウに合わせる" if self.zoom_mode == "fit" else f"{self.zoom_mode}%"
            view_layout.addRow("拡大縮小:", QLabel(zoom_label))
            view_layout.addRow("アスペクト比:", QLabel(ASPECT_MODE_LABELS.get(self.aspect_mode, self.aspect_mode)))
            view_layout.addRow("回転:", QLabel(f"{self.rotation}度"))
            if self.current_noise_level is not None:
                view_layout.addRow("ノイズレベル推定:", QLabel(f"{self.current_noise_level:.1f}"))
            if self.ai_upscale_enabled:
                engine_label = ENGINES.get(self.ai_upscale_engine, {}).get("label", self.ai_upscale_engine)
                model_info = get_model_info(self.ai_upscale_engine, self.ai_upscale_model)
                model_label = model_info["label"] if model_info else self.ai_upscale_model
                cached_entry = self._ai_cache.get(self.index)
                ai_status = "適用済み" if cached_entry is not None and cached_entry[0] == (self.rotation, self.aspect_mode) else "未適用/処理中"
                view_layout.addRow("AI処理:", QLabel(f"{engine_label} / {model_label} ({ai_status})"))
                if self.last_ai_error:
                    error_label = QLabel(self.last_ai_error[:500])
                    error_label.setWordWrap(True)
                    error_label.setStyleSheet("color: #a00; font-size: 11px;")
                    view_layout.addRow("直近のAI失敗詳細:", error_label)
            outer_layout.addWidget(view_group)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok)
        buttons.accepted.connect(dialog.accept)
        outer_layout.addWidget(buttons)

        dialog.exec()

    def _show_password_manager_dialog(self):
        """暗号化ZIP/RAR/7z用のパスワード一覧を管理するダイアログ。
        OSの資格情報マネージャー(keyring)にのみ保存し、平文ファイルは作らない。
        keyring自体が使えない環境では、このセッション中のみ有効な一時保存になる
        (保存できない旨を明示し、平文ファイルへは絶対にフォールバックしない)。"""
        dialog = QDialog(self)
        dialog.setWindowTitle("パスワードの管理")
        layout = QVBoxLayout(dialog)

        if not HAS_KEYRING:
            note = QLabel(
                "この環境ではOSの資格情報マネージャーが利用できないため、\n"
                "パスワードは保存できません(このセッション中のみ有効です)。"
            )
            note.setStyleSheet("color: #a00;")
            layout.addWidget(note)
        else:
            layout.addWidget(QLabel(
                "1行に1つ、パスワードを入力してください。\n"
                "OSの資格情報マネージャーに保存され、平文ファイルは作成されません。"
            ))

        text_edit = QPlainTextEdit()
        text_edit.setPlainText("\n".join(self.passwords))
        layout.addWidget(text_edit)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        layout.addWidget(buttons)

        def on_accept():
            new_passwords = [line.strip() for line in text_edit.toPlainText().splitlines() if line.strip()]
            self.passwords = new_passwords
            if HAS_KEYRING:
                if save_password_list(new_passwords):
                    self.statusBar().showMessage("パスワードを保存しました", 3000)
                else:
                    self.statusBar().showMessage("パスワードの保存に失敗しました", 4000)
            dialog.accept()

        buttons.accepted.connect(on_accept)
        buttons.rejected.connect(dialog.reject)

        dialog.resize(400, 300)
        dialog.exec()

    def show_options_dialog(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("オプション")
        dialog.resize(460, 560)
        outer_layout = QVBoxLayout(dialog)

        tab_widget = QTabWidget()
        outer_layout.addWidget(tab_widget)

        # ---- 一般タブ ----
        general_tab = QWidget()
        general_tab_layout = QVBoxLayout(general_tab)
        general_group = QGroupBox("一般")
        general_layout = QFormLayout(general_group)
        natural_sort_checkbox = QCheckBox("ファイル名を自然順でソートする（page2 < page10）")
        natural_sort_checkbox.setChecked(self.settings.get("natural_sort", True))
        general_layout.addRow(natural_sort_checkbox)

        debug_checkbox = QCheckBox("デバッグログを出力する（トラブル調査用、通常はオフでよい）")
        debug_checkbox.setChecked(self.settings.get("debug_logging_enabled", False))
        general_layout.addRow(debug_checkbox)

        open_log_button = QPushButton("デバッグログを開く")
        open_log_button.clicked.connect(self.open_debug_log)
        general_layout.addRow(open_log_button)

        fullscreen_exit_combo = QComboBox()
        fullscreen_exit_combo.addItem("直前のウィンドウサイズ", "keep")
        fullscreen_exit_combo.addItem("画像の100%サイズ", "100")
        idx = fullscreen_exit_combo.findData(self.fullscreen_exit_mode)
        if idx >= 0:
            fullscreen_exit_combo.setCurrentIndex(idx)
        general_layout.addRow("全画面終了後:", fullscreen_exit_combo)

        passed_pages_spin = QSpinBox()
        passed_pages_spin.setRange(-1, 500)
        passed_pages_spin.setSpecialValueText("無制限(解放しない)")
        passed_pages_spin.setValue(self.passed_pages_keep_count)
        general_layout.addRow("通り過ぎたページを保持する数:", passed_pages_spin)

        password_button = QPushButton("パスワードを管理...")
        password_button.clicked.connect(self._show_password_manager_dialog)
        general_layout.addRow("暗号化ZIP/RAR/7z用パスワード:", password_button)

        general_tab_layout.addWidget(general_group)
        general_tab_layout.addStretch()
        tab_widget.addTab(general_tab, "一般")

        # ---- AIアップスケールタブ（縦に長いのでスクロール可能にする） ----
        ai_tab_scroll = QScrollArea()
        ai_tab_scroll.setWidgetResizable(True)
        ai_tab_content = QWidget()
        ai_tab_layout = QVBoxLayout(ai_tab_content)
        ai_group = QGroupBox("AIアップスケール / ノイズ除去")
        ai_layout = QFormLayout(ai_group)
        available_engines = get_available_engines()
        if available_engines:
            ai_enabled_checkbox = QCheckBox("拡大表示時・ノイズが多い時にAI処理を使う")
            ai_enabled_checkbox.setChecked(self.ai_upscale_enabled)
            ai_layout.addRow(ai_enabled_checkbox)

            ai_engine_combo = QComboBox()
            for key, label in available_engines.items():
                ai_engine_combo.addItem(label, key)

            ai_model_combo = QComboBox()

            def refresh_model_combo():
                ai_model_combo.clear()
                engine_key = ai_engine_combo.currentData()
                for name, label in get_available_models(engine_key).items():
                    ai_model_combo.addItem(label, name)

            ai_engine_combo.currentIndexChanged.connect(refresh_model_combo)

            idx = ai_engine_combo.findData(self.ai_upscale_engine)
            if idx >= 0:
                ai_engine_combo.setCurrentIndex(idx)
            # setCurrentIndex()は、既にその位置が選択済みの場合(よくある初期表示のケース)
            # currentIndexChangedを発火させない。そのため明示的に一度呼んでおく必要がある
            # (呼ばないと、初回表示時にモデル一覧が空のままになるバグがあった)。
            refresh_model_combo()

            model_idx = ai_model_combo.findData(self.ai_upscale_model)
            if model_idx >= 0:
                ai_model_combo.setCurrentIndex(model_idx)

            ai_denoise_mode_combo = QComboBox()
            ai_denoise_mode_combo.addItem("自動判定", "auto")
            ai_denoise_mode_combo.addItem("常にオン", "on")
            ai_denoise_mode_combo.addItem("常にオフ", "off")
            idx = ai_denoise_mode_combo.findData(self.ai_denoise_mode)
            if idx >= 0:
                ai_denoise_mode_combo.setCurrentIndex(idx)
            ai_layout.addRow("デノイズ:", ai_denoise_mode_combo)

            ai_upscale_mode_combo = QComboBox()
            ai_upscale_mode_combo.addItem("目標解像度まで(既定)", "target")
            ai_upscale_mode_combo.addItem("なし", "off")
            ai_upscale_mode_combo.addItem("あり(1回のみ)", "on")
            ai_upscale_mode_combo.addItem("固定回数", "count")
            ai_upscale_mode_combo.addItem("手前で止めて拡大", "undershoot")
            ai_upscale_mode_combo.addItem("超えて縮小", "overshoot")
            idx = ai_upscale_mode_combo.findData(self.ai_upscale_mode)
            if idx >= 0:
                ai_upscale_mode_combo.setCurrentIndex(idx)
            ai_layout.addRow("アップスケール:", ai_upscale_mode_combo)

            ai_fixed_count_spin = QSpinBox()
            ai_fixed_count_spin.setRange(1, 4)
            ai_fixed_count_spin.setValue(self.ai_upscale_fixed_count)
            ai_fixed_count_spin.setEnabled(self.ai_upscale_mode == "count")
            ai_upscale_mode_combo.currentIndexChanged.connect(
                lambda: ai_fixed_count_spin.setEnabled(ai_upscale_mode_combo.currentData() == "count")
            )
            ai_layout.addRow("固定回数:", ai_fixed_count_spin)

            ai_target_mode_combo = QComboBox()
            ai_target_mode_combo.addItem("自動（現在のズーム/画面解像度に合わせる）", "auto")
            ai_target_mode_combo.addItem("手動（下の解像度に収まるまで拡大）", "manual")
            idx = ai_target_mode_combo.findData(self.ai_target_mode)
            if idx >= 0:
                ai_target_mode_combo.setCurrentIndex(idx)
            ai_layout.addRow("目標解像度:", ai_target_mode_combo)

            ai_target_width_spin = QSpinBox()
            ai_target_width_spin.setRange(100, 8000)
            ai_target_width_spin.setSingleStep(100)
            ai_target_width_spin.setSuffix(" px（幅）")
            ai_target_width_spin.setValue(self.ai_target_width)
            ai_target_width_spin.setEnabled(self.ai_target_mode == "manual")
            ai_target_mode_combo.currentIndexChanged.connect(
                lambda: ai_target_width_spin.setEnabled(ai_target_mode_combo.currentData() == "manual")
            )
            ai_layout.addRow("目標幅:", ai_target_width_spin)

            ai_target_height_spin = QSpinBox()
            ai_target_height_spin.setRange(100, 8000)
            ai_target_height_spin.setSingleStep(100)
            ai_target_height_spin.setSuffix(" px（高さ）")
            ai_target_height_spin.setValue(self.ai_target_height)
            ai_target_height_spin.setEnabled(self.ai_target_mode == "manual")
            ai_target_mode_combo.currentIndexChanged.connect(
                lambda: ai_target_height_spin.setEnabled(ai_target_mode_combo.currentData() == "manual")
            )
            ai_layout.addRow("目標高さ:", ai_target_height_spin)

            target_note = QLabel(
                "幅・高さ両方を指定します(例: 2560x1600)。アスペクト比を保ったまま、\n"
                "幅または高さのどちらか先に指定値に収まる倍率まで拡大します。\n"
                "「アップスケール」を「目標解像度まで」「手前で止めて拡大」「超えて縮小」\n"
                "にしている時だけ使われる設定です(それ以外のモードでは無視されます)。"
            )
            target_note.setStyleSheet("color: #888; font-size: 11px;")
            ai_layout.addRow(target_note)

            ai_prefetch_all_checkbox = QCheckBox("アーカイブ全体を先読み対象にする")
            ai_prefetch_all_checkbox.setChecked(self.ai_prefetch_depth < 0)
            ai_layout.addRow(ai_prefetch_all_checkbox)

            ai_prefetch_depth_spin = QSpinBox()
            ai_prefetch_depth_spin.setRange(0, 50)
            ai_prefetch_depth_spin.setValue(max(0, self.ai_prefetch_depth))
            ai_prefetch_depth_spin.setEnabled(self.ai_prefetch_depth >= 0)
            ai_prefetch_all_checkbox.toggled.connect(
                lambda checked: ai_prefetch_depth_spin.setEnabled(not checked)
            )
            ai_layout.addRow("AI先読み範囲(前後ページ数):", ai_prefetch_depth_spin)

            prefetch_note = QLabel(
                "先読みは順番に処理されるため、範囲を広げるほど手元のページの\n"
                "処理が後回しになることがあります。"
            )
            prefetch_note.setStyleSheet("color: #888; font-size: 11px;")
            ai_layout.addRow(prefetch_note)

            mode_key_note = QLabel("表示中に D キーでデノイズ、U キーでアップスケールの方式を切り替えられます。")
            mode_key_note.setStyleSheet("color: #888; font-size: 11px;")
            ai_layout.addRow(mode_key_note)

            ai_diff_checkbox = QCheckBox("差分ベース高速化(前ページと似ている場合、変化部分だけAI処理する)")
            ai_diff_checkbox.setChecked(self.ai_diff_based_enabled)
            ai_layout.addRow(ai_diff_checkbox)

            diff_note = QLabel(
                "差分CG(立ち絵の表情差分等)のような、前ページとほぼ同じ画像が\n"
                "続く場合に有効です。前後で全く違う画像(通常のページ送り)では\n"
                "自動的に通常処理にフォールバックします。"
            )
            diff_note.setStyleSheet("color: #888; font-size: 11px;")
            ai_layout.addRow(diff_note)

            skip_low_res_checkbox = QCheckBox("低解像度の画像はAI処理を飛ばす")
            skip_low_res_checkbox.setChecked(self.skip_low_res_enabled)
            ai_layout.addRow(skip_low_res_checkbox)

            skip_low_res_spin = QSpinBox()
            skip_low_res_spin.setRange(1, 2000)
            skip_low_res_spin.setSuffix(" px 未満")
            skip_low_res_spin.setValue(self.skip_low_res_threshold)
            skip_low_res_spin.setEnabled(self.skip_low_res_enabled)
            skip_low_res_checkbox.toggled.connect(skip_low_res_spin.setEnabled)
            ai_layout.addRow("しきい値(幅または高さ):", skip_low_res_spin)

            batch_checkbox = QCheckBox("背景の先読み分をまとめて処理する(バッチ処理)")
            batch_checkbox.setChecked(self.ai_batch_processing_enabled)
            ai_layout.addRow(batch_checkbox)

            batch_note = QLabel(
                "現在ページ・次ページは常に単体で即座に処理されます(レスポンスに影響しません)。\n"
                "それ以外の背景の先読み分だけを、条件が揃えばまとめて1回のプロセス起動で\n"
                "処理することで、起動オーバーヘッドを減らします。"
            )
            batch_note.setStyleSheet("color: #888; font-size: 11px;")
            ai_layout.addRow(batch_note)

            benchmark_label = QLabel(self._format_backend_rankings())
            benchmark_label.setWordWrap(True)
            self._benchmark_rank_label = benchmark_label
            self._benchmark_engine_combo = ai_engine_combo
            self._benchmark_model_combo = ai_model_combo
            benchmark_button = QPushButton("ベンチマークを再実行")
            benchmark_button.clicked.connect(
                lambda: self._start_backend_benchmark(force=True)
            )
            ai_layout.addRow("自動選択順位:", benchmark_label)
            ai_layout.addRow("", benchmark_button)

            ai_layout.addRow("エンジン:", ai_engine_combo)
            ai_layout.addRow("モデル:", ai_model_combo)

            ai_gpu_combo = QComboBox()
            ai_gpu_combo.addItem("自動選択", "auto")
            for i in range(4):
                ai_gpu_combo.addItem(f"GPU {i}", str(i))
            idx = ai_gpu_combo.findData(self.ai_gpu_id)
            if idx >= 0:
                ai_gpu_combo.setCurrentIndex(idx)
            gpu_row_label = QLabel("使うGPU:")
            ai_layout.addRow(gpu_row_label, ai_gpu_combo)

            gpu_note = QLabel(
                "内蔵GPUと外付けGPUの両方がある場合、「自動選択」だと\n"
                "意図した方が使われないことがあります。番号が分からない場合は\n"
                "0から順に試して、処理時間が短い方を選んでください。"
            )
            gpu_note.setStyleSheet("color: #888; font-size: 11px;")
            ai_layout.addRow(gpu_note)

            # ---- OpenVINOエンジン専用: デバイス選択(CPU/GPU/NPU等) ----
            ai_openvino_device_combo = QComboBox()
            self._benchmark_openvino_device_combo = ai_openvino_device_combo
            ai_openvino_device_combo.addItem("自動選択(AUTO)", "AUTO")
            openvino_devices = get_openvino_devices()
            if "GPU" in openvino_devices and "CPU" in openvino_devices:
                ai_openvino_device_combo.addItem(
                    "Intel GPU優先、CPUフォールバック", "AUTO:GPU,CPU"
                )
            for dev in openvino_devices:
                ai_openvino_device_combo.addItem(dev, dev)
            idx = ai_openvino_device_combo.findData(self.ai_openvino_device)
            if idx >= 0:
                ai_openvino_device_combo.setCurrentIndex(idx)
            openvino_row_label = QLabel("使うデバイス:")
            ai_layout.addRow(openvino_row_label, ai_openvino_device_combo)

            openvino_note = QLabel(
                "AUTOはOpenVINOが利用可能なIntel CPU/GPUから選択します。\n"
                "NPUは既定のAUTO候補外なので、表示されたNPUを明示選択してください。"
            )
            openvino_note.setStyleSheet("color: #888; font-size: 11px;")
            ai_layout.addRow(openvino_note)

            def update_engine_specific_rows():
                selected_engine = ai_engine_combo.currentData()
                is_openvino = selected_engine == "openvino"
                is_ncnn = not ENGINES.get(selected_engine, {}).get("in_process")
                for w in (gpu_row_label, ai_gpu_combo, gpu_note):
                    w.setVisible(is_ncnn)
                for w in (openvino_row_label, ai_openvino_device_combo, openvino_note):
                    w.setVisible(is_openvino)

            ai_engine_combo.currentIndexChanged.connect(update_engine_specific_rows)
            update_engine_specific_rows()

            note = QLabel(
                "モデルは「拡大の基準(倍率/スタイル)」のみを選びます。\n"
                "ノイズ除去はデノイズ設定(上のタブ内)で別軸として指定します。\n"
                "初回のページ表示時は通常のスムーズ拡大が先に出て、\n"
                "AI処理が終わると自動的に高精細な結果に切り替わります。\n"
                "手動モードにすると、ズーム操作のたびにAI処理をやり直さず、\n"
                "指定した幅まで一度だけアップスケールします。\n"
                "Vulkan、DirectML、CUDA、OpenVINOから利用可能なものを選べます。"
            )
            note.setStyleSheet("color: #888; font-size: 11px;")
            ai_layout.addRow(note)
        else:
            ai_enabled_checkbox = None
            ai_engine_combo = None
            ai_model_combo = None
            ai_target_mode_combo = None
            ai_target_width_spin = None
            ai_target_height_spin = None
            ai_denoise_mode_combo = None
            ai_upscale_mode_combo = None
            ai_fixed_count_spin = None
            ai_prefetch_all_checkbox = None
            ai_prefetch_depth_spin = None
            ai_diff_checkbox = None
            skip_low_res_checkbox = None
            skip_low_res_spin = None
            batch_checkbox = None
            ai_gpu_combo = None
            ai_openvino_device_combo = None
            note = QLabel(
                "このPCではAI処理を利用できません\n"
                "(Windows専用の機能です。ai_upscaleフォルダが\n"
                "exeと同じ場所にあるか確認してください)"
            )
            note.setStyleSheet("color: #a00;")
            ai_layout.addRow(note)
        ai_tab_layout.addWidget(ai_group)
        ai_tab_layout.addStretch()
        ai_tab_scroll.setWidget(ai_tab_content)
        tab_widget.addTab(ai_tab_scroll, "AI処理")

        # ---- キーバインドタブ（縦に長いのでスクロール可能にする） ----
        keybind_tab_scroll = QScrollArea()
        keybind_tab_scroll.setWidgetResizable(True)
        keybind_tab_content = QWidget()
        keybind_tab_layout = QVBoxLayout(keybind_tab_content)
        keybind_group = QGroupBox("キーバインド")
        keybind_layout = QFormLayout(keybind_group)
        keybind_edits = {}  # action_id -> QKeySequenceEdit
        for action_id, label, default_key, _handler in KEYBINDABLE_ACTIONS:
            edit = QKeySequenceEdit()
            current = get_keybind(self.settings, action_id, default_key)
            edit.setKeySequence(QKeySequence(current))
            keybind_layout.addRow(label, edit)
            keybind_edits[action_id] = edit

        reset_button = QPushButton("すべてデフォルトに戻す")

        def reset_all_keybinds():
            for action_id, _label, default_key, _handler in KEYBINDABLE_ACTIONS:
                keybind_edits[action_id].setKeySequence(QKeySequence(default_key))

        reset_button.clicked.connect(reset_all_keybinds)
        keybind_layout.addRow(reset_button)

        keybind_tab_layout.addWidget(keybind_group)
        keybind_tab_layout.addStretch()
        keybind_tab_scroll.setWidget(keybind_tab_content)
        tab_widget.addTab(keybind_tab_scroll, "キーバインド")

        # ---- ショートカット一覧タブ(読み取り専用の早見表) ----
        shortcuts_tab_scroll = QScrollArea()
        shortcuts_tab_scroll.setWidgetResizable(True)
        shortcuts_tab_content = QWidget()
        shortcuts_tab_layout = QVBoxLayout(shortcuts_tab_content)

        settable_group = QGroupBox("設定変更可能なショートカット(「キーバインド」タブで変更可)")
        settable_layout = QFormLayout(settable_group)
        for action_id, label, default_key, _handler in KEYBINDABLE_ACTIONS:
            current = get_keybind(self.settings, action_id, default_key)
            settable_layout.addRow(label, QLabel(current))
        shortcuts_tab_layout.addWidget(settable_group)

        fixed_group = QGroupBox("固定のショートカット/マウス操作(変更不可)")
        fixed_layout = QFormLayout(fixed_group)
        for label, key in FIXED_SHORTCUTS:
            fixed_layout.addRow(label, QLabel(key))
        shortcuts_tab_layout.addWidget(fixed_group)

        shortcuts_tab_layout.addStretch()
        shortcuts_tab_scroll.setWidget(shortcuts_tab_content)
        tab_widget.addTab(shortcuts_tab_scroll, "ショートカット一覧")

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        outer_layout.addWidget(buttons)

        dialog_result = dialog.exec()
        self._benchmark_rank_label = None
        self._benchmark_engine_combo = None
        self._benchmark_model_combo = None
        self._benchmark_openvino_device_combo = None
        if dialog_result == QDialog.Accepted:
            self.settings["natural_sort"] = natural_sort_checkbox.isChecked()

            self.fullscreen_exit_mode = fullscreen_exit_combo.currentData()
            self.settings["fullscreen_exit_mode"] = self.fullscreen_exit_mode

            self.passed_pages_keep_count = passed_pages_spin.value()
            self.settings["passed_pages_keep_count"] = self.passed_pages_keep_count

            debug_enabled = debug_checkbox.isChecked()
            self.settings["debug_logging_enabled"] = debug_enabled
            _debug_state["enabled"] = debug_enabled
            ai_upscale._debug_state["enabled"] = debug_enabled

            if ai_enabled_checkbox is not None:
                new_ai_enabled = ai_enabled_checkbox.isChecked()
                new_ai_engine = ai_engine_combo.currentData()
                new_ai_model = ai_model_combo.currentData()
                changed = (new_ai_engine != self.ai_upscale_engine) or (new_ai_model != self.ai_upscale_model) or (new_ai_enabled != self.ai_upscale_enabled)
                self.ai_upscale_enabled = new_ai_enabled
                self.ai_upscale_engine = new_ai_engine
                self.ai_upscale_model = new_ai_model
                self.settings["ai_upscale_enabled"] = new_ai_enabled
                self.settings["ai_upscale_engine"] = new_ai_engine
                self.settings["ai_upscale_model"] = new_ai_model
                if changed:
                    self._invalidate_ai_work()

                new_target_mode = ai_target_mode_combo.currentData()
                new_target_width = ai_target_width_spin.value()
                new_target_height = ai_target_height_spin.value()
                if (new_target_mode != self.ai_target_mode or new_target_width != self.ai_target_width
                        or new_target_height != self.ai_target_height):
                    self._invalidate_ai_work()  # 目標が変わったので古いAI結果は使えない
                self.ai_target_mode = new_target_mode
                self.ai_target_width = new_target_width
                self.ai_target_height = new_target_height
                self.settings["ai_target_mode"] = new_target_mode
                self.settings["ai_target_width"] = new_target_width
                self.settings["ai_target_height"] = new_target_height

                new_gpu_id = ai_gpu_combo.currentData()
                if new_gpu_id != self.ai_gpu_id:
                    self._invalidate_ai_work()  # GPUを変えたら再計算した方が安全
                self.ai_gpu_id = new_gpu_id
                self.settings["ai_gpu_id"] = new_gpu_id

                new_openvino_device = ai_openvino_device_combo.currentData()
                if new_openvino_device != self.ai_openvino_device:
                    self._invalidate_ai_work()
                self.ai_openvino_device = new_openvino_device
                self.settings["ai_openvino_device"] = new_openvino_device

                new_denoise_mode = ai_denoise_mode_combo.currentData()
                new_upscale_mode = ai_upscale_mode_combo.currentData()
                new_fixed_count = ai_fixed_count_spin.value()
                if (new_denoise_mode != self.ai_denoise_mode or new_upscale_mode != self.ai_upscale_mode
                        or new_fixed_count != self.ai_upscale_fixed_count):
                    self._invalidate_ai_work()
                self.ai_denoise_mode = new_denoise_mode
                self.ai_upscale_mode = new_upscale_mode
                self.ai_upscale_fixed_count = new_fixed_count
                self.settings["ai_denoise_mode"] = new_denoise_mode
                self.settings["ai_upscale_mode"] = new_upscale_mode
                self.settings["ai_upscale_fixed_count"] = new_fixed_count

                new_prefetch_depth = -1 if ai_prefetch_all_checkbox.isChecked() else ai_prefetch_depth_spin.value()
                self.ai_prefetch_depth = new_prefetch_depth
                self.settings["ai_prefetch_depth"] = new_prefetch_depth

                self.ai_diff_based_enabled = ai_diff_checkbox.isChecked()
                self.settings["ai_diff_based_enabled"] = self.ai_diff_based_enabled

                self.skip_low_res_enabled = skip_low_res_checkbox.isChecked()
                self.settings["skip_low_res_enabled"] = self.skip_low_res_enabled
                self.skip_low_res_threshold = skip_low_res_spin.value()
                self.settings["skip_low_res_threshold"] = self.skip_low_res_threshold

                self.ai_batch_processing_enabled = batch_checkbox.isChecked()
                self.settings["ai_batch_processing_enabled"] = self.ai_batch_processing_enabled

            # キーバインドを反映（デフォルトと同じ値は保存しない -> settings.jsonを簡潔に保つ）
            new_keybinds = {}
            for action_id, _label, default_key, _handler in KEYBINDABLE_ACTIONS:
                seq = keybind_edits[action_id].keySequence().toString()
                if seq and seq != default_key:
                    new_keybinds[action_id] = seq
                action = self.actions_by_id.get(action_id)
                if action is not None:
                    action.setShortcut(QKeySequence(seq) if seq else QKeySequence())
            self.settings["keybinds"] = new_keybinds

            save_settings(self.settings)

            # 既にアーカイブを開いていれば、新しいソート順で開き直す
            if self.current_archive_path:
                current_name = None
                if self.reader and self.reader.image_names:
                    current_name = self.reader.image_names[self.index]
                self.open_archive(self.current_archive_path)
                # 可能なら、開き直した後も同じ画像を表示し続ける
                if current_name and self.reader and current_name in self.reader.image_names:
                    self.index = self.reader.image_names.index(current_name)
                    self.show_current_image()


def main():
    app = QApplication(sys.argv)
    window = ImageViewer()
    window.show()

    # コマンドライン引数でファイルを渡された場合はそれを開く（エクスプローラの右クリック等用）
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        window.navigate_to_archive(sys.argv[1])

    sys.exit(app.exec())


if __name__ == "__main__":
    main()


