# SPDX-License-Identifier: Apache-2.0
"""
TLG5 / TLG6 デコーダ（吉里吉里/KiriKiriの画像形式）

このモジュールは、GARbro (https://github.com/morkt/GARbro) の
ArcFormats/KiriKiri/ImageTLG.cs (MIT License, Copyright W.Dee and contributors,
C# port by morkt) を元に、Pythonへ忠実に移植したものです。

【重要な注意】
- TLG5は仕組みが単純（LZSS+差分符号化）なので、移植の確信度は高いです。
- TLG6はGolomb-Rice符号＋32種の色相関フィルタを使う、かなり複雑なコーデックです。
  実際のTLG6ファイル(800x600)で実測したところ、純粋なPython実装では
  1枚あたり約2秒かかっていました。これに対応するため、同じアルゴリズムを
  Rustに移植したネイティブライブラリ(tlg6_native.dll)を用意しており、
  存在する場合は自動的にそちらを使う(約20倍高速、実データでバイト単位の
  出力一致を確認済み)。無い場合は今まで通りPython実装にフォールバックする。
- タグ付き画像（差分画像の合成、TLG0.0のタグ形式）には対応していません。
  単純な単体のTLG5/TLG6画像のみが対象です。
"""

import ctypes
import os
import struct
import sys
from pathlib import Path

from PySide6.QtGui import QImage


# ============================================================
# ネイティブ(Rust製)TLG6デコーダの読み込み(あれば使う、無ければPythonにフォールバック)
# ============================================================

def _find_native_tlg6_library():
    """tlg6_native.dll(Windows)/.so(Linux、開発時の検証用)を探して読み込む。
    見つからない/読み込めない場合はNoneを返す(呼び出し側はPython実装にフォールバックする)。"""
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).parent
    else:
        base = Path(__file__).parent

    if sys.platform == "win32":
        candidates = [base / "tlg6_native.dll", base / "ai_upscale" / "tlg6_native.dll"]
    else:
        candidates = [base / "tlg6_native.so", base / "libtlg6_native.so"]

    for path in candidates:
        if path.exists():
            try:
                lib = ctypes.CDLL(str(path))
                lib.tlg6_decode.restype = ctypes.c_int
                lib.tlg6_decode.argtypes = [
                    ctypes.c_char_p, ctypes.c_size_t, ctypes.c_size_t,
                    ctypes.c_uint32, ctypes.c_uint32, ctypes.c_uint32,
                    ctypes.c_char_p, ctypes.c_size_t,
                ]
                return lib
            except Exception:
                continue
    return None


_NATIVE_TLG6 = _find_native_tlg6_library()


def _decode_tlg6_native(data, offset, width, height, colors):
    """ネイティブライブラリでTLG6をデコードする。失敗したらNoneを返す
    (呼び出し側がPython実装にフォールバックできるように、例外は投げない)。"""
    if _NATIVE_TLG6 is None:
        return None
    out_buf = ctypes.create_string_buffer(width * height * 4)
    try:
        ret = _NATIVE_TLG6.tlg6_decode(
            bytes(data), len(data), offset, width, height, colors, out_buf, len(out_buf)
        )
    except Exception:
        return None
    if ret != 0:
        return None
    return bytearray(out_buf.raw)


# ============================================================
# 共通: ヘッダ判定
# ============================================================

def _read_header(data: bytes):
    """TLGファイルのヘッダを解析し、(version, width, height, colors, data_offset) を返す。
    非対応の場合はNoneを返す。"""
    if len(data) < 0x26:
        return None

    offset = 0
    if data[:11] == b"TLG0.0\x00sds\x1a":
        # TLG0.0コンテナ(タグ付き画像)。構造は
        #   "TLG0.0\0"(7) + "sds\x1a"(4) + チャンクサイズ(4, リトルエンディアン) + 実データ
        # サイズの値そのものは使わず、単に15バイト分オフセットして実データ(TLG5/6)に進む。
        offset = 15

    if data[offset:offset + 6] == b"TLG5.0" and data[offset + 6:offset + 11] == b"\x00raw\x1a":
        version = 5
    elif data[offset:offset + 6] == b"TLG6.0" and data[offset + 6:offset + 11] == b"\x00raw\x1a":
        version = 6
    else:
        return None

    colors = data[offset + 11]

    if version == 6:
        if colors not in (1, 3, 4):
            return None
        if data[offset + 12] != 0 or data[offset + 13] != 0 or data[offset + 14] != 0:
            return None
        pos = offset + 15
    else:
        if colors not in (3, 4):
            return None
        pos = offset + 12

    width, height = struct.unpack_from("<II", data, pos)
    data_offset = pos + 8

    return version, width, height, colors, data_offset


def decode_tlg(data: bytes, debug_errors=None):
    """TLGファイルのバイト列をデコードし、QImage(Format_ARGB32)を返す。
    失敗した場合はNoneを返す。debug_errorsにリストを渡すと、失敗時の例外内容
    (種類・メッセージ・トレースバック)を追記する(呼び出し側での原因調査用)。"""
    header = _read_header(data)
    if header is None:
        if debug_errors is not None:
            debug_errors.append("ヘッダー解析に失敗（TLG5.0/TLG6.0の署名が見つからない）")
        return None
    version, width, height, colors, data_offset = header

    if width <= 0 or height <= 0 or width * height > 64_000_000:
        if debug_errors is not None:
            debug_errors.append(f"サイズが異常: {width}x{height}")
        return None  # 異常なサイズはガードする

    try:
        if version == 5:
            pixels = _decode_tlg5(data, data_offset, width, height, colors)
        else:
            pixels = None
            if _NATIVE_TLG6 is not None:
                pixels = _decode_tlg6_native(data, data_offset, width, height, colors)
            if pixels is None:
                pixels = _decode_tlg6(data, data_offset, width, height, colors)
    except Exception as e:
        if debug_errors is not None:
            import traceback
            debug_errors.append(f"デコード中に例外(TLG{version}): {e}\n{traceback.format_exc()}")
        return None

    if pixels is None:
        return None

    # pixelsはBGRA順、4バイト/pixelのバイト列（stride = width*4）
    image = QImage(bytes(pixels), width, height, width * 4, QImage.Format_ARGB32)
    return image.copy()  # bytesのバッファ寿命に依存しないようコピーする


# ============================================================
# LZSS展開（TLG5本体、およびTLG6のフィルタ種別データで共用）
# ============================================================

def _tlg5_lzss_decompress(outbuf, out_pos, inbuf, in_size, text, r):
    """TLG5で使われる、辞書サイズ4096の簡易LZSS展開。
    text: 4096バイトのリングバッファ（呼び出しをまたいで状態を保持する）
    r: リングバッファ内の現在位置
    戻り値: 更新後の r"""
    flags = 0
    i = 0
    o = out_pos
    while i < in_size:
        flags >>= 1
        if (flags & 256) == 0:
            flags = inbuf[i] | 0xff00
            i += 1
        if flags & 1:
            mpos = inbuf[i] | ((inbuf[i + 1] & 0x0f) << 8)
            mlen = (inbuf[i + 1] & 0xf0) >> 4
            i += 2
            mlen += 3
            if mlen == 18:
                mlen += inbuf[i]
                i += 1
            while mlen:
                c = text[mpos]
                outbuf[o] = c
                text[r] = c
                o += 1
                r = (r + 1) & 0xfff
                mpos = (mpos + 1) & 0xfff
                mlen -= 1
        else:
            c = inbuf[i]
            i += 1
            outbuf[o] = c
            text[r] = c
            o += 1
            r = (r + 1) & 0xfff
    return r


# ============================================================
# TLG5 デコード
# ============================================================

def _decode_tlg5(data, offset, width, height, colors):
    pos = offset
    blockheight = struct.unpack_from("<I", data, pos)[0]
    pos += 4
    blockcount = (height - 1) // blockheight + 1
    pos += blockcount * 4  # ブロックサイズ一覧はスキップ(使わない)

    stride = width * 4
    image_bits = bytearray(height * stride)
    text = bytearray(4096)

    outbuf = [bytearray(blockheight * width + 10) for _ in range(4)]

    r = 0
    prevline = -1  # image_bits内のバイトオフセット、-1なら「まだ無い」

    for y_blk in range(0, height, blockheight):
        for c in range(colors):
            mark = data[pos]
            pos += 1
            size = struct.unpack_from("<I", data, pos)[0]
            pos += 4
            if mark == 0:
                # LZSS圧縮されたデータ
                inbuf = data[pos:pos + size]
                pos += size
                r = _tlg5_lzss_decompress(outbuf[c], 0, inbuf, size, text, r)
            else:
                # 生データ
                outbuf[c][0:size] = data[pos:pos + size]
                pos += size

        y_lim = min(y_blk + blockheight, height)
        outbuf_pos = 0
        for y in range(y_blk, y_lim):
            current = y * stride
            current_org = current

            if prevline >= 0:
                upper = prevline
                if colors == 3:
                    pc0 = pc1 = pc2 = 0
                    for x in range(width):
                        c0 = outbuf[0][outbuf_pos + x]
                        c1 = outbuf[1][outbuf_pos + x]
                        c2 = outbuf[2][outbuf_pos + x]
                        c0 = (c0 + c1) & 0xff
                        c2 = (c2 + c1) & 0xff
                        pc0 = (pc0 + c0) & 0xff
                        pc1 = (pc1 + c1) & 0xff
                        pc2 = (pc2 + c2) & 0xff
                        image_bits[current] = (pc0 + image_bits[upper]) & 0xff
                        image_bits[current + 1] = (pc1 + image_bits[upper + 1]) & 0xff
                        image_bits[current + 2] = (pc2 + image_bits[upper + 2]) & 0xff
                        image_bits[current + 3] = 0xff
                        current += 4
                        upper += 4
                else:  # colors == 4
                    pc0 = pc1 = pc2 = pc3 = 0
                    for x in range(width):
                        c0 = outbuf[0][outbuf_pos + x]
                        c1 = outbuf[1][outbuf_pos + x]
                        c2 = outbuf[2][outbuf_pos + x]
                        c3 = outbuf[3][outbuf_pos + x]
                        c0 = (c0 + c1) & 0xff
                        c2 = (c2 + c1) & 0xff
                        pc0 = (pc0 + c0) & 0xff
                        pc1 = (pc1 + c1) & 0xff
                        pc2 = (pc2 + c2) & 0xff
                        pc3 = (pc3 + c3) & 0xff
                        image_bits[current] = (pc0 + image_bits[upper]) & 0xff
                        image_bits[current + 1] = (pc1 + image_bits[upper + 1]) & 0xff
                        image_bits[current + 2] = (pc2 + image_bits[upper + 2]) & 0xff
                        image_bits[current + 3] = (pc3 + image_bits[upper + 3]) & 0xff
                        current += 4
                        upper += 4
            else:
                # 先頭行（上の行が無い）
                if colors == 3:
                    pr = pg = pb = 0
                    for x in range(width):
                        b = outbuf[0][outbuf_pos + x]
                        g = outbuf[1][outbuf_pos + x]
                        r_ = outbuf[2][outbuf_pos + x]
                        b = (b + g) & 0xff
                        r_ = (r_ + g) & 0xff
                        pb = (pb + b) & 0xff
                        pg = (pg + g) & 0xff
                        pr = (pr + r_) & 0xff
                        image_bits[current] = pb
                        image_bits[current + 1] = pg
                        image_bits[current + 2] = pr
                        image_bits[current + 3] = 0xff
                        current += 4
                else:  # colors == 4
                    pr = pg = pb = pa = 0
                    for x in range(width):
                        b = outbuf[0][outbuf_pos + x]
                        g = outbuf[1][outbuf_pos + x]
                        r_ = outbuf[2][outbuf_pos + x]
                        a = outbuf[3][outbuf_pos + x]
                        b = (b + g) & 0xff
                        r_ = (r_ + g) & 0xff
                        pb = (pb + b) & 0xff
                        pg = (pg + g) & 0xff
                        pr = (pr + r_) & 0xff
                        pa = (pa + a) & 0xff
                        image_bits[current] = pb
                        image_bits[current + 1] = pg
                        image_bits[current + 2] = pr
                        image_bits[current + 3] = pa
                        current += 4

            outbuf_pos += width
            prevline = current_org

    return image_bits


# ============================================================
# TLG6 デコード
# ============================================================

_TLG6_H_BLOCK_SIZE = 8
_TLG6_W_BLOCK_SIZE = 8
_TLG6_GOLOMB_N_COUNT = 4
_TLG6_LZT_BITS = 12
_TLG6_LZT_SIZE = 1 << _TLG6_LZT_BITS

_TLG6_GOLOMB_COMPRESSED = [
    [3, 7, 15, 27, 63, 108, 223, 448, 130],
    [3, 5, 13, 24, 51, 95, 192, 384, 257],
    [2, 5, 12, 21, 39, 86, 155, 320, 384],
    [2, 3, 9, 18, 33, 61, 129, 258, 511],
]


def _build_leading_zero_table():
    table = bytearray(_TLG6_LZT_SIZE)
    for i in range(_TLG6_LZT_SIZE):
        cnt = 0
        j = 1
        while j != _TLG6_LZT_SIZE and (i & j) == 0:
            j <<= 1
            cnt += 1
        cnt += 1
        if j == _TLG6_LZT_SIZE:
            cnt = 0
        table[i] = cnt
    return table


def _build_golomb_bit_length_table():
    # [a][n] -> ビット長 (0..8)
    table = [[0] * _TLG6_GOLOMB_N_COUNT for _ in range(_TLG6_GOLOMB_N_COUNT * 2 * 128)]
    for n in range(_TLG6_GOLOMB_N_COUNT):
        a = 0
        for i in range(9):
            for _ in range(_TLG6_GOLOMB_COMPRESSED[n][i]):
                table[a][n] = i
                a += 1
    return table


_LEADING_ZERO_TABLE = _build_leading_zero_table()
_GOLOMB_BIT_LENGTH_TABLE = _build_golomb_bit_length_table()


def _make_lzss_text_for_tlg6():
    text = bytearray(4096)
    p = 0
    for i in range(0, 32 * 0x01010101, 0x01010101):
        for j in range(0, 16 * 0x01010101, 0x01010101):
            text[p] = i & 0xff
            text[p + 1] = (i >> 8) & 0xff
            text[p + 2] = (i >> 16) & 0xff
            text[p + 3] = (i >> 24) & 0xff
            text[p + 4] = j & 0xff
            text[p + 5] = (j >> 8) & 0xff
            text[p + 6] = (j >> 16) & 0xff
            text[p + 7] = (j >> 24) & 0xff
            p += 8
    return text


def _u32(v):
    return v & 0xFFFFFFFF


def _read_u32le(buf, index):
    """buf(bytearray)のindexバイト目から32bit(little-endian)を読む。
    範囲外はTLG6の実装同様0埋め扱いにする。"""
    n = len(buf)
    b0 = buf[index] if index < n else 0
    b1 = buf[index + 1] if index + 1 < n else 0
    b2 = buf[index + 2] if index + 2 < n else 0
    b3 = buf[index + 3] if index + 3 < n else 0
    return b0 | (b1 << 8) | (b2 << 16) | (b3 << 24)


def _decode_golomb_values(pixelbuf, offset, pixel_count, bit_pool, for_first):
    """bit_poolからGolomb符号でpixel_count個の値を復号し、pixelbuf(uint32のlist)に書き込む。
    for_first=Trueならdword全体に書き込み(最初の色チャンネル)、
    for_first=Falseならoffsetで指定したバイトレーンだけに書き込む(2番目以降の色チャンネル)。"""
    mask = _u32(~(0xff << offset)) if not for_first else 0

    bit_pool_index = 0
    n = _TLG6_GOLOMB_N_COUNT - 1
    a = 0
    bit_pos = 1
    zero = (bit_pool[0] & 1) == 0

    pixel = 0
    while pixel < pixel_count:
        # ランレングス(count)を読む
        t = _read_u32le(bit_pool, bit_pool_index) >> bit_pos
        b = _LEADING_ZERO_TABLE[t & (_TLG6_LZT_SIZE - 1)]
        bit_count = b
        while b == 0:
            bit_count += _TLG6_LZT_BITS
            bit_pos += _TLG6_LZT_BITS
            bit_pool_index += bit_pos >> 3
            bit_pos &= 7
            t = _read_u32le(bit_pool, bit_pool_index) >> bit_pos
            b = _LEADING_ZERO_TABLE[t & (_TLG6_LZT_SIZE - 1)]
            bit_count += b
        bit_pos += b
        bit_pool_index += bit_pos >> 3
        bit_pos &= 7
        bit_count -= 1
        count = 1 << bit_count
        count += (_read_u32le(bit_pool, bit_pool_index) >> bit_pos) & (count - 1)
        bit_pos += bit_count
        bit_pool_index += bit_pos >> 3
        bit_pos &= 7

        if zero:
            for _ in range(count):
                if for_first:
                    pixelbuf[pixel] = 0
                else:
                    pixelbuf[pixel] = pixelbuf[pixel] & mask
                pixel += 1
            zero = not zero
        else:
            for _ in range(count):
                k = _GOLOMB_BIT_LENGTH_TABLE[a][n]
                t = _read_u32le(bit_pool, bit_pool_index) >> bit_pos
                if t != 0:
                    b = _LEADING_ZERO_TABLE[t & (_TLG6_LZT_SIZE - 1)]
                    bit_count = b
                    while b == 0:
                        bit_count += _TLG6_LZT_BITS
                        bit_pos += _TLG6_LZT_BITS
                        bit_pool_index += bit_pos >> 3
                        bit_pos &= 7
                        t = _read_u32le(bit_pool, bit_pool_index) >> bit_pos
                        b = _LEADING_ZERO_TABLE[t & (_TLG6_LZT_SIZE - 1)]
                        bit_count += b
                    bit_count -= 1
                else:
                    bit_pool_index += 5
                    bit_count = bit_pool[bit_pool_index - 1] if bit_pool_index - 1 < len(bit_pool) else 0
                    bit_pos = 0
                    t = _read_u32le(bit_pool, bit_pool_index)
                    b = 0

                v = (bit_count << k) + ((t >> b) & ((1 << k) - 1))
                sign = (v & 1) - 1
                v >>= 1
                a += v
                value = ((v ^ sign) + sign + 1) & 0xff

                if for_first:
                    pixelbuf[pixel] = value
                else:
                    pixelbuf[pixel] = (pixelbuf[pixel] & mask) | (value << offset)

                bit_pos += b
                bit_pos += k
                bit_pool_index += bit_pos >> 3
                bit_pos &= 7

                pixel += 1
                n -= 1
                if n < 0:
                    a >>= 1
                    n = _TLG6_GOLOMB_N_COUNT - 1
            zero = not zero


# ---- 色相関フィルタ (MED/AVG, 32種) ----

def _make_gt_mask(a, b):
    tmp2 = _u32(~b)
    tmp = _u32((a & tmp2) + (((a ^ tmp2) >> 1) & 0x7f7f7f7f)) & 0x80808080
    tmp = (((tmp >> 7) + 0x7f7f7f7f) & 0xFFFFFFFF) ^ 0x7f7f7f7f
    return tmp


def _packed_bytes_add(a, b):
    tmp = _u32((((a & b) << 1) + ((a ^ b) & 0xfefefefe)) & 0x01010100)
    return _u32(a + b - tmp)


def _med2(a, b, c):
    aa_gt_bb = _make_gt_mask(a, b)
    a_xor_b_and_aa_gt_bb = (a ^ b) & aa_gt_bb
    aa = a_xor_b_and_aa_gt_bb ^ a
    bb = a_xor_b_and_aa_gt_bb ^ b
    n = _make_gt_mask(c, bb)
    nn = _make_gt_mask(aa, c)
    m = _u32(~(n | nn))
    return _u32((n & aa) | (nn & bb) | _u32((bb & m) - (c & m) + (aa & m)))


def _med(a, b, c, v):
    return _packed_bytes_add(_med2(a, b, c), v)


def _avg(a, b, c, v):
    return _packed_bytes_add(
        _u32((((a & b) + (((a ^ b) & 0xfefefefe) >> 1)) + ((a ^ b) & 0x01010101))), v
    )


def _filter_value(ftype, v):
    """32種の色相関フィルタの、"v"(元の値)に対する事前変換部分だけを計算する。
    フィルタ0/1は変換なし(vのまま)。それ以外はビット演算による色チャンネルの混合。"""
    b_ = v & 0xff
    g_ = (v >> 8) & 0xff
    r_ = (v >> 16) & 0xff
    top = v & 0xff000000

    if ftype in (0, 1):
        return v
    elif ftype in (2, 3):
        return _u32((0xff0000 & (((r_ + g_) & 0xff) << 16)) + (g_ << 8) + (0xff & ((b_ + g_) & 0xff)) + top)
    elif ftype in (4, 5):
        return _u32((0xff0000 & (((r_ + b_ + g_) & 0xff) << 16)) + (0xff00 & (((g_ + b_) & 0xff) << 8)) + (0xff & b_) + top)
    elif ftype in (6, 7):
        return _u32((0xff0000 & (r_ << 16)) + (0xff00 & (((g_ + r_) & 0xff) << 8)) + (0xff & ((b_ + r_ + g_) & 0xff)) + top)
    elif ftype in (8, 9):
        return _u32((0xff0000 & (((r_ + b_ + r_ + g_) & 0xff) << 16)) + (0xff00 & (((g_ + b_ + r_) & 0xff) << 8)) + (0xff & ((b_ + r_) & 0xff)) + top)
    elif ftype in (10, 11):
        return _u32((0xff0000 & (r_ << 16)) + (0xff00 & (((g_ + b_ + r_) & 0xff) << 8)) + (0xff & ((b_ + r_) & 0xff)) + top)
    elif ftype in (12, 13):
        return _u32((0xff0000 & (r_ << 16)) + (0xff00 & (g_ << 8)) + (0xff & ((b_ + g_) & 0xff)) + top)
    elif ftype in (14, 15):
        return _u32((0xff0000 & (r_ << 16)) + (0xff00 & (((g_ + b_) & 0xff) << 8)) + (0xff & b_) + top)
    elif ftype in (16, 17):
        return _u32((0xff0000 & (((r_ + g_) & 0xff) << 16)) + (0xff00 & (g_ << 8)) + (0xff & b_) + top)
    elif ftype in (18, 19):
        return _u32((0xff0000 & (((r_ + b_) & 0xff) << 16)) + (0xff00 & (((g_ + r_ + b_) & 0xff) << 8)) + (0xff & ((b_ + g_ + r_ + b_) & 0xff)) + top)
    elif ftype in (20, 21):
        return _u32((0xff0000 & (r_ << 16)) + (0xff00 & (((g_ + r_) & 0xff) << 8)) + (0xff & ((b_ + r_) & 0xff)) + top)
    elif ftype in (22, 23):
        return _u32((0xff0000 & (((r_ + b_) & 0xff) << 16)) + (0xff00 & (((g_ + b_) & 0xff) << 8)) + (0xff & b_) + top)
    elif ftype in (24, 25):
        return _u32((0xff0000 & (((r_ + b_) & 0xff) << 16)) + (0xff00 & (((g_ + r_ + b_) & 0xff) << 8)) + (0xff & b_) + top)
    elif ftype in (26, 27):
        return _u32((0xff0000 & (((r_ + b_ + g_) & 0xff) << 16)) + (0xff00 & (((g_ + r_ + b_ + g_) & 0xff) << 8)) + (0xff & ((b_ + g_) & 0xff)) + top)
    elif ftype in (28, 29):
        return _u32((0xff0000 & (((r_ + b_ + g_ + r_) & 0xff) << 16)) + (0xff00 & (((g_ + r_) & 0xff) << 8)) + (0xff & ((b_ + g_ + r_) & 0xff)) + top)
    elif ftype in (30, 31):
        return _u32((0xff0000 & (((r_ + ((b_ << 1) & 0xff)) & 0xff) << 16)) + (0xff00 & (((g_ + ((b_ << 1) & 0xff)) & 0xff) << 8)) + (0xff & b_) + top)
    else:
        return None


def _decode_line_generic(prevline, prevline_idx, curline, curline_idx, width,
                          start_block, block_limit, filtertypes, filtertypes_idx,
                          skipblockbytes, inbuf, inbuf_idx, initialp, oddskip, dir_):
    if start_block != 0:
        prevline_idx += start_block * _TLG6_W_BLOCK_SIZE
        curline_idx += start_block * _TLG6_W_BLOCK_SIZE
        p = curline[curline_idx - 1]
        up = prevline[prevline_idx - 1]
    else:
        p = up = initialp

    inbuf_idx += skipblockbytes * start_block
    step = 1 if (dir_ & 1) != 0 else -1

    for i in range(start_block, block_limit):
        w = width - i * _TLG6_W_BLOCK_SIZE
        if w > _TLG6_W_BLOCK_SIZE:
            w = _TLG6_W_BLOCK_SIZE
        ww = w

        if step == -1:
            inbuf_idx += ww - 1
        if (i & 1) != 0:
            inbuf_idx += oddskip * ww

        ftype = filtertypes[filtertypes_idx + i]
        use_avg = (ftype % 2) == 1

        while w:
            u = prevline[prevline_idx]
            v_raw = inbuf[inbuf_idx]
            v = _filter_value(ftype, v_raw)
            if v is None:
                return  # 未対応フィルタ種別(仕様上ここには来ないはず)
            p = _avg(p, u, up, v) if use_avg else _med(p, u, up, v)
            up = u
            curline[curline_idx] = p
            curline_idx += 1
            prevline_idx += 1
            inbuf_idx += step
            w -= 1

        if step == 1:
            inbuf_idx += skipblockbytes - ww
        else:
            inbuf_idx += skipblockbytes + 1
        if (i & 1) != 0:
            inbuf_idx -= oddskip * ww


def _decode_tlg6(data, offset, width, height, colors):
    pos = offset
    max_bit_length = struct.unpack_from("<i", data, pos)[0]
    pos += 4

    x_block_count = ((width - 1) // _TLG6_W_BLOCK_SIZE) + 1
    y_block_count = ((height - 1) // _TLG6_H_BLOCK_SIZE) + 1
    main_count = width // _TLG6_W_BLOCK_SIZE
    fraction = width - main_count * _TLG6_W_BLOCK_SIZE

    image_bits = [0] * (height * width)
    bit_pool = bytearray(max_bit_length // 8 + 5)
    pixelbuf = [0] * (width * _TLG6_H_BLOCK_SIZE + 1)
    filter_types = bytearray(x_block_count * y_block_count)

    zerocolor = 0xff000000 if colors == 3 else 0x00000000
    zeroline = [zerocolor] * width

    lzss_text = _make_lzss_text_for_tlg6()

    # 色フィルタ種別データ(LZSS圧縮)を読む
    inbuf_size = struct.unpack_from("<i", data, pos)[0]
    pos += 4
    inbuf = data[pos:pos + inbuf_size]
    pos += inbuf_size
    _tlg5_lzss_decompress(filter_types, 0, inbuf, inbuf_size, lzss_text, 0)

    prevline = zeroline
    prevline_index = 0

    for y in range(0, height, _TLG6_H_BLOCK_SIZE):
        ylim = min(y + _TLG6_H_BLOCK_SIZE, height)
        pixel_count = (ylim - y) * width

        for c in range(colors):
            bit_length = struct.unpack_from("<i", data, pos)[0]
            pos += 4
            method = (bit_length >> 30) & 3
            bit_length &= 0x3fffffff
            byte_length = bit_length // 8
            if bit_length % 8:
                byte_length += 1

            if method != 0:
                raise ValueError(f"Unsupported TLG6 entropy coding method: {method}")

            needed = byte_length
            if len(bit_pool) < needed:
                bit_pool = bytearray(needed)
            bit_pool[:needed] = data[pos:pos + needed]
            if len(bit_pool) > needed:
                bit_pool[needed:] = b"\x00" * (len(bit_pool) - needed)
            pos += byte_length

            if c == 0 and colors != 1:
                _decode_golomb_values(pixelbuf, 0, pixel_count, bit_pool, for_first=True)
            else:
                _decode_golomb_values(pixelbuf, c * 8, pixel_count, bit_pool, for_first=False)

        ft = (y // _TLG6_H_BLOCK_SIZE) * x_block_count
        skipbytes = (ylim - y) * _TLG6_W_BLOCK_SIZE

        for yy in range(y, ylim):
            curline = yy * width
            dir_ = (yy & 1) ^ 1
            oddskip = (ylim - yy - 1) - (yy - y)

            if main_count != 0:
                start = (min(width, _TLG6_W_BLOCK_SIZE)) * (yy - y)
                _decode_line_generic(
                    prevline, prevline_index, image_bits, curline,
                    width, 0, main_count, filter_types, ft, skipbytes,
                    pixelbuf, start, zerocolor, oddskip, dir_
                )

            if main_count != x_block_count:
                ww = min(fraction, _TLG6_W_BLOCK_SIZE)
                start = ww * (yy - y)
                _decode_line_generic(
                    prevline, prevline_index, image_bits, curline,
                    width, main_count, x_block_count, filter_types, ft, skipbytes,
                    pixelbuf, start, zerocolor, oddskip, dir_
                )

            prevline = image_bits
            prevline_index = curline

    # image_bits(uint32のリスト、各要素が0xAARRGGBB風のパック済み値)を
    # BGRA順のバイト列に変換する（QImage.Format_ARGB32はメモリ上でBGRA順）
    out = bytearray(height * width * 4)
    idx = 0
    for v in image_bits:
        out[idx] = v & 0xff
        out[idx + 1] = (v >> 8) & 0xff
        out[idx + 2] = (v >> 16) & 0xff
        out[idx + 3] = (v >> 24) & 0xff
        idx += 4

    return out

