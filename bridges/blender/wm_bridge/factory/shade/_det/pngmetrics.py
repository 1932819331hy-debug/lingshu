# -*- coding: utf-8 -*-
"""_det/pngmetrics · PNG 解码 / **确定性重编码** / 量化判据（纯标准库 · 零 bpy · 零第三方）

为什么需要它（不是重造 PIL）：
 1. **确定性**：Blender 写出的 PNG 里有一个 `tEXt/RenderTime` chunk（值 = 该次渲染的耗时
    字符串）。实测 chunk 级对拍证明：两次渲染**唯一的字节差异**就是这个 chunk，解开 IDAT
    后像素 0 通道有差。所以"PNG 的 sha256"根本不是像素的哈希 —— 本模块提供
    `encode_canonical()`：只保留 IHDR + IDAT（固定 filter=0、固定 zlib level）+ IEND，
    于是 sha256 变成**可复现的像素指纹**。
 2. **判据**：验收要的是"描边像素占前景比例""前景/背景对比度""材质区分的像素分布差异"，
    这些都得自己解像素算。用第三方库会让"这结论怎么来的"不可复核；纯标准库则
    在无 Blender、无 numpy 的机器上也能重跑。

口径与坑：
  · 只支持 8bit、非隔行（IHDR 里 bit_depth=8 / interlace=0）；其它一律 raise，**不猜**。
  · 支持 color_type 0/2/4/6（灰度 / RGB / 灰+Alpha / RGBA），ch = 1/2/3/4。
  · 像素索引一律**按行主序的通道序列**；取某通道用 `px[c::ch]`（C 速度切片，别写 Python 循环）。
"""
from __future__ import annotations

import hashlib
import struct
import zlib

__all__ = [
    "PngError", "decode", "decode_file", "png_chunks", "encode_canonical",
    "canonical_file", "canonical_sha256", "diff_stats", "alpha_mask",
    "coverage_from_alpha", "luma_histogram", "top_bins_coverage", "band_stats",
    "quantize_colors", "dominant_colors", "fg_bg_stats", "subsample",
    "image_stats", "contrast_ratio", "luminance",
]


class PngError(Exception):
    """PNG 不合法或不在支持范围内（fail-closed：不猜格式）。"""


# ────────────────────────────────────────────────────────────── 读
def png_chunks(data):
    """按序返回 [(type, data), ...]（含 IEND）。"""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise PngError("不是 PNG（签名不符）")
    out = []
    i = 8
    n = len(data)
    while i < n:
        if i + 8 > n:
            raise PngError("PNG 截断（chunk 头）")
        ln = struct.unpack(">I", data[i:i + 4])[0]
        typ = data[i + 4:i + 8].decode("latin1")
        body = data[i + 8:i + 8 + ln]
        if len(body) != ln:
            raise PngError("PNG 截断（chunk %s 体）" % typ)
        out.append((typ, body))
        i += 12 + ln
        if typ == "IEND":
            break
    return out


def _unfilter(raw, w, h, ch):
    stride = w * ch
    if len(raw) != (stride + 1) * h:
        raise PngError("IDAT 解压长度 %d ≠ 期望 %d" % (len(raw), (stride + 1) * h))
    out = bytearray()
    prev = bytes(stride)
    pos = 0
    for _y in range(h):
        f = raw[pos]
        pos += 1
        line = bytearray(raw[pos:pos + stride])
        pos += stride
        if f == 0:
            pass
        elif f == 1:
            for x in range(ch, stride):
                line[x] = (line[x] + line[x - ch]) & 0xFF
        elif f == 2:
            for x in range(stride):
                line[x] = (line[x] + prev[x]) & 0xFF
        elif f == 3:
            for x in range(stride):
                a = line[x - ch] if x >= ch else 0
                line[x] = (line[x] + ((a + prev[x]) >> 1)) & 0xFF
        elif f == 4:
            for x in range(stride):
                a = line[x - ch] if x >= ch else 0
                b = prev[x]
                c = prev[x - ch] if x >= ch else 0
                p = a + b - c
                pa, pb, pc = abs(p - a), abs(p - b), abs(p - c)
                pr = a if (pa <= pb and pa <= pc) else (b if pb <= pc else c)
                line[x] = (line[x] + pr) & 0xFF
        else:
            raise PngError("未知 PNG 行滤波器 %d" % f)
        out += line
        prev = bytes(line)
    return bytes(out)


def decode(data):
    """PNG bytes → dict(w, h, ch, color_type, bit_depth, pixels, interlace)。

    pixels 是**未做色彩管理**的原始通道字节（8bit/通道）。
    """
    w = h = ch = ct = bd = il = None
    idat = bytearray()
    for typ, body in png_chunks(data):
        if typ == "IHDR":
            w, h, bd, ct, _comp, _filt, il = struct.unpack(">IIBBBBB", body)
        elif typ == "IDAT":
            idat += body
        elif typ == "IEND":
            break
    if w is None:
        raise PngError("缺 IHDR")
    if bd != 8:
        raise PngError("只支持 8bit/通道，实为 %r" % (bd,))
    if il != 0:
        raise PngError("只支持非隔行，实为 interlace=%r" % (il,))
    if ct not in (0, 2, 4, 6):
        raise PngError("只支持 color_type 0/2/4/6，实为 %r" % (ct,))
    ch = {0: 1, 2: 3, 4: 2, 6: 4}[ct]
    raw = zlib.decompress(bytes(idat))
    return {"w": w, "h": h, "ch": ch, "color_type": ct, "bit_depth": bd,
            "interlace": il, "pixels": _unfilter(raw, w, h, ch)}


def decode_file(path):
    with open(path, "rb") as f:
        return decode(f.read())


# ────────────────────────────────────────────────────────────── 写（确定性）
def _chunk(typ, body):
    return (struct.pack(">I", len(body)) + typ.encode("latin1") + body
            + struct.pack(">I", zlib.crc32(typ.encode("latin1") + body) & 0xFFFFFFFF))


def encode_canonical(w, h, ch, pixels, *, level=9, filter_type=0):
    """确定性 PNG 编码：只写 IHDR + IDAT + IEND；行滤波固定；zlib 级别固定。

    同一份像素 ⇒ **同字节**（不含时间戳/软件名/exif/physical 等一切随环境变的 chunk）。
    """
    if ch not in (1, 2, 3, 4):
        raise PngError("ch 须为 1/2/3/4，实为 %r" % (ch,))
    stride = w * ch
    if len(pixels) != stride * h:
        raise PngError("像素长度 %d ≠ w*h*ch = %d" % (len(pixels), stride * h))
    ct = {1: 0, 2: 4, 3: 2, 4: 6}[ch]
    raw = bytearray()
    for y in range(h):
        raw.append(filter_type)
        raw += pixels[y * stride:(y + 1) * stride]
    out = bytearray(b"\x89PNG\r\n\x1a\n")
    out += _chunk("IHDR", struct.pack(">IIBBBBB", w, h, 8, ct, 0, 0, 0))
    out += _chunk("IDAT", zlib.compress(bytes(raw), level))
    out += _chunk("IEND", b"")
    return bytes(out)


def canonical_file(src, dst, *, level=9, keep_if_exists=False):
    """把 src 重编码成确定性 PNG 写到 dst。返回 {src, dst, sha256, bytes, from_cache}。

    **来源可判读**（#699 F8）：`keep_if_exists=True` 且 dst 已存在时**不重编码、不覆写**，
    直接取 dst 既有内容的 sha。旧版对此**不作任何标记** ⇒ 陈旧缓存的 sha 可冒充"本次产物
    的指纹"。现在返回值带 `from_cache`：
      · `from_cache=False` ⇒ `sha256` 是**本次由 src 重编码**所得字节的哈希（src 的可信指纹）
      · `from_cache=True`  ⇒ `sha256` 是 **dst 既有文件**的哈希，**不保证**对应本次的 src；
                             需要 src 的指纹时请传 `keep_if_exists=False`。
    """
    import os
    from_cache = bool(keep_if_exists and os.path.exists(dst))
    if from_cache:
        b = open(dst, "rb").read()
    else:
        im = decode_file(src)
        b = encode_canonical(im["w"], im["h"], im["ch"], im["pixels"], level=level)
        with open(dst, "wb") as f:
            f.write(b)
    return {"src": src, "dst": dst, "bytes": len(b), "from_cache": from_cache,
            "sha256": hashlib.sha256(b).hexdigest()}


def canonical_sha256(path):
    im = decode_file(path)
    b = encode_canonical(im["w"], im["h"], im["ch"], im["pixels"])
    return hashlib.sha256(b).hexdigest()


# ────────────────────────────────────────────────────────────── 差异 / 掩膜
def diff_stats(pa, pb, ch):
    """两个**同几何**像素串的逐通道差（不建大中间列表，用 map 走 C 速度）。"""
    if len(pa) != len(pb):
        raise PngError("像素长度不一致：%d vs %d" % (len(pa), len(pb)))
    import operator
    n = len(pa)
    if pa == pb:
        return {"channels_total": n, "nonzero_chan": 0, "nz_frac": 0.0, "max_abs_diff": 0,
                "chans_gt1": 0, "mean_abs_diff": 0.0, "identical": True}
    nz = 0
    mx = 0
    gt1 = 0
    tot = 0
    for a, b in zip(pa, pb):
        d = a - b
        if d:
            if d < 0:
                d = -d
            nz += 1
            tot += d
            if d > mx:
                mx = d
            if d > 1:
                gt1 += 1
    return {"channels_total": n, "nonzero_chan": nz, "nz_frac": round(nz / float(n), 8),
            "max_abs_diff": mx, "chans_gt1": gt1,
            "mean_abs_diff": round(tot / float(n), 6), "identical": False}


def diff_files(a, b):
    ia, ib = decode_file(a), decode_file(b)
    if (ia["w"], ia["h"], ia["ch"]) != (ib["w"], ib["h"], ib["ch"]):
        return {"geom_mismatch": [ia["w"], ia["h"], ia["ch"], ib["w"], ib["h"], ib["ch"]]}
    return dict(diff_stats(ia["pixels"], ib["pixels"], ia["ch"]),
                w=ia["w"], h=ia["h"], ch=ia["ch"])


def alpha_mask(im, thr=128):
    """按 alpha 通道出掩膜（color_type 4/6 才有）。thr 是 0..255。"""
    ch = im["ch"]
    if ch not in (2, 4):
        raise PngError("没有 alpha 通道（ch=%d）" % ch)
    a = im["pixels"][ch - 1::ch]
    return bytes(1 if v >= thr else 0 for v in a)


def coverage_from_alpha(im, thr=128):
    """前景像素占比（alpha ≥ thr）。这是**精确**覆盖率 —— 不受背景明暗干扰。"""
    ch = im["ch"]
    if ch not in (2, 4):
        raise PngError("没有 alpha 通道（ch=%d）" % ch)
    a = im["pixels"][ch - 1::ch]
    n = len(a)
    hit = sum(1 for v in a if v >= thr)
    return {"total_px": n, "fg_px": hit, "coverage": round(hit / float(n), 6),
            "alpha_threshold": thr}


# ────────────────────────────────────────────────────────────── 亮度 / 直方图
def luminance(pixels, ch):
    """按通道序返回**每像素**亮度（0..255，整数；Rec.709 权重）。"""
    if ch < 3:
        raise PngError("亮度需要 RGB（ch≥3），实为 %d" % ch)
    R = pixels[0::ch]
    G = pixels[1::ch]
    B = pixels[2::ch]
    return bytes(min(255, (54 * r + 183 * g + 19 * b) >> 8) for r, g, b in zip(R, G, B))


def luma_histogram(im, bins=16, mask=None):
    """亮度直方图（bins 档）。mask（bytes 0/1）给了就只统计掩膜内像素。"""
    lum = luminance(im["pixels"], im["ch"])
    if mask is not None:
        lum = bytes(v for v, m in zip(lum, mask) if m)
    hist = [0] * bins
    step = 256.0 / bins
    for v in lum:
        hist[min(bins - 1, int(v / step))] += 1
    n = sum(hist)
    return {"bins": bins, "hist": hist, "total": n,
            "frac": ([round(c / float(n), 6) for c in hist] if n else [0.0] * bins)}


def top_bins_coverage(im, bins=16, mask=None, k=3):
    """前 k 个亮度档的像素占比（"色阶量化"的判据：NPR 下应显著高于写实）。"""
    h = luma_histogram(im, bins=bins, mask=mask)
    if not h["total"]:
        return {"top_k": k, "coverage": 0.0, "hist": h}
    top = sorted(h["hist"], reverse=True)[:k]
    return {"top_k": k, "bins": bins, "coverage": round(sum(top) / float(h["total"]), 6),
            "hist": h}


def band_stats(im, mask=None, tol=3, bins=256):
    """亮度**色阶数**：把亮度按 tol 归并后，出现过的"亮带"个数 + 最大带的占比。

    NPR（CONSTANT 插值的 ColorRamp）应给出 2~5 个带；写实渲染会几十上百个带。
    """
    lum = luminance(im["pixels"], im["ch"])
    if mask is not None:
        lum = bytes(v for v, m in zip(lum, mask) if m)
    if not lum:
        return {"bands": 0, "total": 0, "top_band_frac": 0.0, "top3_frac": 0.0}
    counts = {}
    for v in lum:
        k = v // max(1, tol)
        counts[k] = counts.get(k, 0) + 1
    # 相邻 bin 合并成"带"（带宽 = 256/tol 个 bin，这里按 1 个 bin 的容差已经足够）
    vals = sorted(counts.items())
    bands = 0
    prev = None
    for k, _c in vals:
        if prev is None or k > prev + 1:
            bands += 1
        prev = k
    n = len(lum)
    top = sorted(counts.values(), reverse=True)
    return {"bands": bands, "tolerance": tol, "total": n,
            "top_band_frac": round(top[0] / float(n), 6),
            "top3_frac": round(sum(top[:3]) / float(n), 6),
            "distinct_levels": len(counts)}


# ────────────────────────────────────────────────────────────── 颜色分布
def quantize_colors(im, bits=4, mask=None):
    """把 RGB 各通道压到 `bits` 位 → 返回按像素的紧凑键序列（int）。"""
    ch = im["ch"]
    if ch < 3:
        raise PngError("颜色量化需要 RGB（ch≥3）")
    shift = 8 - int(bits)
    keep = ((1 << bits) - 1)
    R = im["pixels"][0::ch]
    G = im["pixels"][1::ch]
    B = im["pixels"][2::ch]
    rk = bytes((v >> shift) & keep for v in R)
    gk = bytes((v >> shift) & keep for v in G)
    bk = bytes((v >> shift) & keep for v in B)
    keys = ((r << (2 * bits)) | (g << bits) | b for r, g, b in zip(rk, gk, bk))
    if mask is not None:
        keys = (k for k, m in zip(keys, mask) if m)
    return keys


def dominant_colors(im, bits=4, mask=None, top=12):
    """主导颜色（量化后计数）。返回 [{key, rgb255, frac}]，按占比降序。"""
    from collections import Counter
    n_bits = int(bits)
    keep = (1 << n_bits) - 1
    shift = 8 - n_bits
    cnt = Counter(quantize_colors(im, bits=n_bits, mask=mask))
    total = sum(cnt.values())
    out = []
    for key, c in cnt.most_common(top):
        r = (key >> (2 * n_bits)) & keep
        g = (key >> n_bits) & keep
        b = key & keep
        rgb = [(r << shift) | (r >> (8 - n_bits - 1) if n_bits < 8 else 0),
               (g << shift), (b << shift)]
        out.append({"rgb255": [min(255, x) for x in rgb], "count": c,
                    "frac": round(c / float(total), 6) if total else 0.0})
    return {"total": total, "bits": n_bits, "n_distinct": len(cnt), "top": out}


def fg_bg_stats(im, fg_mask, *, border=12):
    """前景（掩膜内）与背景（四边 border 宽的框）的亮度/色度统计 + 对比度。

    "前景/背景对比度"用 WCAG 相对亮度比（这也是可复核、有公认口径的数）。
    """
    ch = im["ch"]
    w, h = im["w"], im["h"]
    lum = luminance(im["pixels"], ch)
    bg_idx = []
    for y in range(h):
        for x in range(w):
            if x < border or y < border or x >= w - border or y >= h - border:
                bg_idx.append(y * w + x)
    fg_vals = [lum[i] for i in range(len(lum)) if fg_mask[i]]
    bg_vals = [lum[i] for i in bg_idx]

    def mean(v):
        return round(sum(v) / float(len(v)), 4) if v else 0.0

    def rel_lum(v255):
        # WCAG: 先线性化 sRGB，再用 0.2126/0.7152/0.0722
        c = v255 / 255.0
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    lf = mean([rel_lum(x) for x in fg_vals[:20000]]) if fg_vals else 0.0
    lb = mean([rel_lum(x) for x in bg_vals[:20000]]) if bg_vals else 0.0
    lo, hi = (lf, lb) if lf <= lb else (lb, lf)
    ratio = round((hi + 0.05) / (lo + 0.05), 4) if (lo + 0.05) > 0 else None
    return {"fg_mean_luma255": mean(fg_vals), "bg_mean_luma255": mean(bg_vals),
            "fg_px": len(fg_vals), "bg_px": len(bg_vals),
            "wcag_contrast": ratio,
            "luma_diff": round(abs(mean(fg_vals) - mean(bg_vals)), 4)}


def subsample(im, step=2):
    """按 step 抽稀（仅用于很慢的统计；判据优先用全分辨率）。"""
    if step <= 1:
        return im
    w, h, ch = im["w"], im["h"], im["ch"]
    px = im["pixels"]
    nw, nh = w // step, h // step
    out = bytearray()
    stride = w * ch
    for y in range(nh):
        row = y * step * stride
        for x in range(nw):
            i = row + x * step * ch
            out += px[i:i + ch]
    return {"w": nw, "h": nh, "ch": ch, "color_type": im["color_type"],
            "bit_depth": 8, "interlace": 0, "pixels": bytes(out)}


def contrast_ratio(l1, l2):
    lo, hi = (l1, l2) if l1 <= l2 else (l2, l1)
    return round((hi + 0.05) / (lo + 0.05), 4)


def image_stats(im, mask=None, bins=16):
    """一次拿到直方图 + 色阶带 + 主导色（给报告用的小结）。"""
    return {"size": [im["w"], im["h"]], "channels": im["ch"],
            "hist": luma_histogram(im, bins=bins, mask=mask),
            "bands": band_stats(im, mask=mask),
            "dominant": dominant_colors(im, mask=mask, top=8)}
