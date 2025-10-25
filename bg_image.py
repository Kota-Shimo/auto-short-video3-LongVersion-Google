"""
bg_image.py – Unsplash から検索キーワードで **縦向き (Shorts)** 画像を取得し，
中央トリムして 1080×1920 PNG を生成（失敗時は単色）。
"""
from pathlib import Path
import logging, io, requests, random, time
from PIL import Image, ImageOps
from config import UNSPLASH_ACCESS_KEY

# translate() がある環境なら英語化して検索ヒット率を上げる
try:
    from translate import translate as _tr
except Exception:
    _tr = None

# ------------------------------------------------------------
W, H = 1080, 1920        # Shorts 縦動画解像度
TIMEOUT = 20
RETRIES = 3

_HEADERS = {
    "Accept": "application/json",
    "Accept-Version": "v1",
    "User-Agent": "AutoVocab/1.0",
}

def _to_english(topic: str) -> str:
    t = (topic or "").strip() or "language learning"
    if _tr:
        try:
            en = _tr(t, "en")
            if isinstance(en, str) and en.strip():
                return en.strip()
        except Exception:
            pass
    return t

def _get_json(url: str, params: dict) -> dict:
    headers = dict(_HEADERS)
    if UNSPLASH_ACCESS_KEY:
        headers["Authorization"] = f"Client-ID {UNSPLASH_ACCESS_KEY}"
    r = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()

def _download_bytes(url: str) -> bytes:
    r = requests.get(url, headers=_HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    return r.content

def fetch(topic: str, out_png: Path) -> bool:
    """
    1) topic を英語化して Random API
    2) 失敗したら Search API にフォールバック
    3) なお失敗なら単色
    """
    if not UNSPLASH_ACCESS_KEY:
        logging.warning("[Unsplash] KEY 未設定 → 単色背景")
        _fallback_solid(out_png)
        return False

    query = _to_english(topic)

    # 1) Random endpoint（軽いリトライ）
    for attempt in range(1, RETRIES + 1):
        try:
            data = _get_json(
                "https://api.unsplash.com/photos/random",
                {
                    "query": query,
                    "orientation": "portrait",
                    "content_filter": "high",
                    "sig": random.randint(1, 999999),  # CDNキャッシュ回避
                },
            )
            urls = data.get("urls") or {}
            img_url = urls.get("regular") or urls.get("full")
            if not img_url:
                raise ValueError("random: urls missing")
            img_bytes = _download_bytes(img_url)
            _resize_1080x1920(img_bytes, out_png)
            return True
        except Exception as e:
            logging.warning(f"[Unsplash random] attempt {attempt}/{RETRIES} failed: {e}")
            time.sleep(0.7 * attempt)

    # 2) Search endpoint フォールバック
    try:
        data = _get_json(
            "https://api.unsplash.com/search/photos",
            {
                "query": query,
                "orientation": "portrait",
                "content_filter": "high",
                "per_page": 30,
                "order_by": "relevant",
            },
        )
        results = data.get("results") or []
        if not results:
            raise ValueError("search: no results")
        # 縦長優先で選ぶ（無ければ全体から）
        portraits = [r for r in results if r.get("width", 0) < r.get("height", 1)]
        pick = random.choice(portraits or results)
        urls = pick.get("urls") or {}
        img_url = urls.get("regular") or urls.get("full")
        if not img_url:
            raise ValueError("search: urls missing")
        img_bytes = _download_bytes(img_url)
        _resize_1080x1920(img_bytes, out_png)
        return True
    except Exception as e:
        logging.exception(f"[Unsplash search] failed: {e}")
        _fallback_solid(out_png)
        return False

# ------------------------------------------------------------
def _resize_1080x1920(img_bytes: bytes, out_png: Path):
    """ImageOps.fit で黒帯なし中央フィット → 1080×1920 で保存"""
    with Image.open(io.BytesIO(img_bytes)) as im:
        fitted = ImageOps.fit(im.convert("RGB"), (W, H), Image.LANCZOS, centering=(0.5, 0.5))
        fitted.save(out_png, "PNG", optimize=True)

# 単色フォールバック
def _fallback_solid(out_png: Path, color=(10, 10, 10)):
    Image.new("RGB", (W, H), color).save(out_png, "PNG")