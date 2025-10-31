"""
bg_image.py – トピックから背景画像を取得して横向きへ最適化。
- Unsplash 検索優先（決定的選択）→ 1920x1080 など任意サイズに "cover"/"contain" で整形
- 失敗時は見やすいフォールバック（単色 or グラデーション）
"""

from pathlib import Path
import logging
import io
import os
import time
import hashlib
from typing import Tuple, Optional

import requests
from PIL import Image, ImageOps, ImageDraw

from config import UNSPLASH_ACCESS_KEY

DEFAULT_W, DEFAULT_H = 1920, 1080  # 横動画 Full-HD 既定
UA = "AutoShortVideo/1.0 (+https://example.local)"  # 適当なUAを明示

# ─────────────────────────────────────────────
def fetch(topic: str,
          out_png: Path,
          size: Tuple[int, int] = (DEFAULT_W, DEFAULT_H),
          fit: str = "cover",
          prefer_search: bool = True,
          fallback_style: str = "gradient") -> bool:
    """
    トピックから横向き背景PNGを生成。
    - size: (W,H) 例 (1920,1080)
    - fit : "cover"（黒帯なし中央トリム） / "contain"（余白パッド）
    - prefer_search: True なら /search/photos を優先
    - fallback_style: "gradient" or "solid"
    """
    w, h = size

    if not UNSPLASH_ACCESS_KEY:
        logging.warning("[Unsplash] KEY 未設定 → フォールバック背景を使用")
        _fallback(out_png, size=size, style=fallback_style)
        return False

    try:
        img_bytes = None
        if prefer_search:
            img_bytes = _fetch_by_search(topic, orientation="landscape")
        if img_bytes is None:
            img_bytes = _fetch_random(topic, orientation="landscape")

        if img_bytes is None:
            raise RuntimeError("no image bytes")

        _resize_and_save(img_bytes, out_png, size=size, fit=fit)
        return True

    except Exception as e:
        logging.exception("[Unsplash] fetch failed: %s", e)
        _fallback(out_png, size=size, style=fallback_style)
        return False


# ─────────────────────────────────────────────
# 内部: Unsplash 取得（検索優先 → ランダム）
# ─────────────────────────────────────────────
def _fetch_by_search(query: str, orientation: str = "landscape", retries: int = 2) -> Optional[bytes]:
    """
    /search/photos を使い、per_page=20 の中からトピックのハッシュで決定的に1枚選ぶ。
    """
    url = "https://api.unsplash.com/search/photos"
    params = {
        "query": query,
        "orientation": orientation,
        "per_page": 20,
        "content_filter": "high",
        "client_id": UNSPLASH_ACCESS_KEY,
    }
    headers = {"User-Agent": UA, "Accept-Version": "v1"}
    for i in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
            if r.status_code == 403:
                # レート制限など → 少し待ってリトライ
                time.sleep(1.2)
            r.raise_for_status()
            data = r.json()
            results = data.get("results") or []
            if not results:
                return None
            # 決定的選択（トピック×件数で安定）
            idx = _stable_index(query, len(results))
            chosen = results[idx]
            img_url = (chosen.get("urls") or {}).get("regular") or (chosen.get("urls") or {}).get("full")
            if not img_url:
                return None
            return _download_bytes(img_url)
        except Exception:
            if i == retries:
                return None
            time.sleep(0.5)
    return None


def _fetch_random(query: str, orientation: str = "landscape", retries: int = 2) -> Optional[bytes]:
    """
    /photos/random でフォールバック取得。
    """
    url = "https://api.unsplash.com/photos/random"
    params = {
        "query": query,
        "orientation": orientation,
        "content_filter": "high",
        "client_id": UNSPLASH_ACCESS_KEY,
    }
    headers = {"User-Agent": UA, "Accept-Version": "v1"}
    for i in range(retries + 1):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=15)
            if r.status_code == 403:
                time.sleep(1.2)
            r.raise_for_status()
            data = r.json()
            urls = data.get("urls") or {}
            img_url = urls.get("regular") or urls.get("full")
            if not img_url:
                return None
            return _download_bytes(img_url)
        except Exception:
            if i == retries:
                return None
            time.sleep(0.5)
    return None


def _download_bytes(img_url: str) -> Optional[bytes]:
    headers = {"User-Agent": UA}
    r = requests.get(img_url, headers=headers, timeout=20)
    r.raise_for_status()
    return r.content


def _stable_index(seed_text: str, modulo: int) -> int:
    if modulo <= 0:
        return 0
    h = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()
    num = int(h[:8], 16)
    return num % modulo


# ─────────────────────────────────────────────
# 画像整形＆保存
# ─────────────────────────────────────────────
def _resize_and_save(img_bytes: bytes, out_png: Path, size: Tuple[int, int], fit: str):
    """cover: 黒帯なし中央トリム / contain: 余白パッド"""
    w, h = size
    with Image.open(io.BytesIO(img_bytes)) as im:
        im = im.convert("RGB")
        if fit == "cover":
            fitted = ImageOps.fit(im, (w, h), Image.LANCZOS, centering=(0.5, 0.5))
        else:
            # contain: アスペクト維持で縮小 → 中央パッド
            fitted = im.copy()
            fitted.thumbnail((w, h), Image.LANCZOS)
            canvas = Image.new("RGB", (w, h), (10, 10, 10))
            x = (w - fitted.width) // 2
            y = (h - fitted.height) // 2
            canvas.paste(fitted, (x, y))
            fitted = canvas
        out_png.parent.mkdir(parents=True, exist_ok=True)
        fitted.save(out_png, "PNG", optimize=True)


# ─────────────────────────────────────────────
# フォールバック（単色 or グラデーション）
# ─────────────────────────────────────────────
def _fallback(out_png: Path, size: Tuple[int, int], style: str = "gradient"):
    w, h = size
    out_png.parent.mkdir(parents=True, exist_ok=True)
    if style == "gradient":
        img = _make_vertical_gradient(w, h, start=(16, 18, 22), end=(6, 6, 8))
    else:
        img = Image.new("RGB", (w, h), (10, 10, 10))
    img.save(out_png, "PNG")


def _make_vertical_gradient(w: int, h: int, start=(16, 18, 22), end=(6, 6, 8)) -> Image.Image:
    """上→下の簡易グラデーション背景"""
    img = Image.new("RGB", (w, h), start)
    draw = ImageDraw.Draw(img)
    for y in range(h):
        t = y / max(1, h - 1)
        r = int(start[0] * (1 - t) + end[0] * t)
        g = int(start[1] * (1 - t) + end[1] * t)
        b = int(start[2] * (1 - t) + end[2] * t)
        draw.line([(0, y), (w, y)], fill=(r, g, b))
    return img