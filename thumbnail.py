# thumbnail.py – centered glass panel + two-line caption (scene / phrase)
from pathlib import Path
from io import BytesIO
import textwrap, logging, requests, os
from PIL import (
    Image, ImageDraw, ImageFont, ImageFilter,
    ImageEnhance, ImageOps
)
from openai import OpenAI
from config import OPENAI_API_KEY, UNSPLASH_ACCESS_KEY
from translate import translate

# ------------ Canvas ---------------------------------
W, H = 1280, 720

# ------------ Font set --------------------------------
FONT_DIR   = Path(__file__).parent / "fonts"
FONT_LATN  = FONT_DIR / "RobotoSerif_36pt-Bold.ttf"
FONT_CJK   = FONT_DIR / "NotoSansJP-Bold.ttf"
FONT_KO    = FONT_DIR / "malgunbd.ttf"

# フォント存在チェック（落とさず警告だけ）
for fp in (FONT_LATN, FONT_CJK, FONT_KO):
    try:
        if not fp.exists():
            logging.warning(f"[font] Missing: {fp}")
    except Exception:
        logging.exception("[font] exists check failed")

def pick_font(text: str) -> str | None:
    """文字種に応じてフォントパスを返す。無ければ None（後段で fallback）。"""
    try:
        has_latn = FONT_LATN.exists()
        has_cjk  = FONT_CJK.exists()
        has_ko   = FONT_KO.exists()
    except Exception:
        has_latn = has_cjk = has_ko = False

    for ch in text or "":
        cp = ord(ch)
        if 0xAC00 <= cp <= 0xD7A3 and has_ko:             # Hangul
            return str(FONT_KO)
        if ((0x4E00 <= cp <= 0x9FFF) or (0x3040 <= cp <= 0x30FF)) and has_cjk:
            return str(FONT_CJK)                           # CJK/Kana
    if has_latn:
        return str(FONT_LATN)
    if has_cjk:
        return str(FONT_CJK)
    if has_ko:
        return str(FONT_KO)
    return None

def _load_font(font_path: str | None, size: int) -> ImageFont.FreeTypeFont:
    """指定フォントが無くても落ちない安全ロード"""
    try:
        if font_path:
            return ImageFont.truetype(font_path, size)
    except Exception:
        logging.exception("[font] truetype failed")
    return ImageFont.load_default()

# ------------ Language name map (ISO639-1 -> English name) ----
LANG_NAME = {
    "en": "English",
    "pt": "Portuguese",
    "id": "Indonesian",
    "ja": "Japanese",
    "ko": "Korean",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "zh": "Chinese",
    "ar": "Arabic",
}

# ------------ Caption sizes / wrapping ---------------
F_H1, F_H2          = 100, 70
WRAP_H1, WRAP_H2    = 16, 22

# ------------ Badge -----------------------------------
BADGE_BASE   = "Lesson"
BADGE_SIZE   = 60
BADGE_POS    = (40, 30)

# OpenAI クライアント（今は使わないが互換のため残す）
client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# ------------------------------------------------------ Unsplash BG
def _unsplash(topic: str) -> Image.Image:
    if not UNSPLASH_ACCESS_KEY:
        return Image.new("RGB", (W, H), (35, 35, 35))

    url = (
        "https://api.unsplash.com/photos/random"
        f"?query={requests.utils.quote(topic)}"
        f"&orientation=landscape&client_id={UNSPLASH_ACCESS_KEY}"
    )
    try:
        r = requests.get(url, timeout=15); r.raise_for_status()
        img_url = r.json().get("urls", {}).get("regular")
        if not img_url:
            raise ValueError("Unsplash: no image url")
        img = Image.open(BytesIO(requests.get(img_url, timeout=15).content)).convert("RGB")
    except Exception:
        logging.exception("[Unsplash]")
        return Image.new("RGB", (W, H), (35, 35, 35))

    img = ImageOps.fit(img, (W, H), Image.LANCZOS, centering=(0.5, 0.5))
    img = img.filter(ImageFilter.GaussianBlur(2)).convert("RGBA")
    img.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 77)))   # 30% dark veil
    return img

# ------------------------------------------------------ Simple Caption (topic / words / CEFR)
def _caption(topic: str, lang_code: str) -> str:
    """
    タイトルとのズレを減らすため、GPTには投げず、
    - topic（テーマ）
    - VOCAB_WORDS（単語数）
    - CEFR_LEVEL（難易度）
    だけで2行テキストを決める。
    """
    topic = (topic or "").strip() or "vocabulary"

    # テーマのローカライズ
    try:
        if lang_code == "en":
            theme_local = topic
        else:
            theme_local = translate(topic, lang_code) or topic
    except Exception:
        logging.exception("[thumb topic translate]")
        theme_local = topic
    theme_local = theme_local.strip()

    # 単語数・CEFR
    n_words = 0
    env_words = os.getenv("VOCAB_WORDS", "").strip()
    if env_words.isdigit():
        try:
            n_words = max(1, int(env_words))
        except Exception:
            n_words = 0
    level = os.getenv("CEFR_LEVEL", "").strip().upper()

    # 言語ごとの定型
    if lang_code == "ja":
        l1 = theme_local + " の語彙" if theme_local else "語彙レッスン"
        parts = []
        if n_words:
            parts.append(f"{n_words}語")
        if level:
            parts.append(f"CEFR {level}")
        if not parts:
            parts.append("単語レッスン")
        l2 = " ".join(parts)
    elif lang_code == "en":
        l1 = theme_local or "Vocabulary practice"
        if n_words and level:
            l2 = f"{n_words} words · CEFR {level}"
        elif n_words:
            l2 = f"{n_words} key words"
        elif level:
            l2 = f"CEFR {level} vocab"
        else:
            l2 = "Boost your vocabulary"
    else:
        l1 = theme_local or "Vocabulary practice"
        if n_words and level:
            base = f"{n_words} words · CEFR {level}"
        elif n_words:
            base = f"{n_words} key words"
        elif level:
            base = f"CEFR {level} vocabulary"
        else:
            base = "Vocabulary lesson"
        try:
            l2 = translate(base, lang_code) or base
        except Exception:
            logging.exception("[thumb caption translate]")
            l2 = base

    # 最終ガード＋長さ制限（もとの仕様に合わせる）
    l1 = (l1 or "Everyday").strip()
    l2 = (l2 or "Speak now").strip()
    return f"{l1[:22]}|{l2[:24]}"

# ------------------------------------------------------ helpers
def _txt_size(draw: ImageDraw.ImageDraw, txt: str, font: ImageFont.FreeTypeFont):
    if hasattr(draw, "textbbox"):
        x1, y1, x2, y2 = draw.textbbox((0, 0), txt, font=font)
        return x2 - x1, y2 - y1
    return draw.textsize(txt, font=font)

# ------------------------------------------------------ draw core
def _draw(img: Image.Image, cap: str, badge_txt: str) -> Image.Image:
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    draw = ImageDraw.Draw(img)

    l1, l2  = (cap.split("|") + [""])[:2]
    l1, l2  = l1.strip(), l2.strip()

    # フォント読み込みを安全化（無くても落ちない）
    f1 = _load_font(pick_font(l1),          F_H1)
    f2 = _load_font(pick_font(l2 or l1),    F_H2)

    t1 = textwrap.fill(l1, WRAP_H1) if l1 else ""
    t2 = textwrap.fill(l2, WRAP_H2) if l2 else ""

    w1, h1 = _txt_size(draw, t1, f1) if t1 else (0, 0)
    w2, h2 = _txt_size(draw, t2, f2) if t2 else (0, 0)

    stroke = 4
    tw = max(w1, w2) + stroke*2
    th = h1 + (h2 + 12 if t2 else 0)

    BASE_PAD_X, BASE_PAD_Y = 60, 40
    pad_x = min(BASE_PAD_X, max(20, (W - tw)//2))
    pad_y = min(BASE_PAD_Y, max(20, (H - th)//2))

    pw, ph = tw + pad_x*2, th + pad_y*2
    x_panel = (W - pw)//2
    y_panel = (H - ph)//2
    x_txt   = x_panel + pad_x
    y_txt   = y_panel + pad_y

    # glass panel（元のまま）
    radius = 35
    panel_bg = img.crop((x_panel, y_panel, x_panel+pw, y_panel+ph)) \
                  .filter(ImageFilter.GaussianBlur(12)).convert("RGBA")
    veil     = Image.new("RGBA", (pw, ph), (255,255,255,77))
    panel    = Image.alpha_composite(panel_bg, veil)

    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0,0,pw-1,ph-1], radius, fill=255)
    panel.putalpha(mask)

    border = Image.new("RGBA", (pw, ph))
    ImageDraw.Draw(border).rounded_rectangle(
        [0,0,pw-1,ph-1], radius, outline=(255,255,255,120), width=2)
    panel = Image.alpha_composite(panel, border)
    img.paste(panel, (x_panel, y_panel), panel)

    # glow（元のまま）
    glow = Image.new("RGBA", img.size, (0,0,0,0))
    gd   = ImageDraw.Draw(glow)
    if t1:
        gd.text((x_txt, y_txt), t1, font=f1, fill=(255,255,255,255))
    if t2:
        gd.text((x_txt, y_txt+h1+12), t2, font=f2, fill=(255,255,255,255))
    glow = glow.filter(ImageFilter.GaussianBlur(14))
    glow = ImageEnhance.Brightness(glow).enhance(1.2)
    img.alpha_composite(glow)

    # final text（元のまま）
    if t1:
        draw.text((x_txt, y_txt), t1, font=f1, fill=(255,255,255),
                  stroke_width=stroke, stroke_fill=(0,0,0))
    if t2:
        draw.text((x_txt, y_txt+h1+12), t2, font=f2,
                  fill=(255,255,255), stroke_width=stroke, stroke_fill=(0,0,0))

    # badge（フォント未配置でも落ちない）
    bf  = _load_font(pick_font(badge_txt), BADGE_SIZE)
    draw.text(BADGE_POS, badge_txt, font=bf,
              fill=(255,255,255), stroke_width=3, stroke_fill=(0,0,0))
    return img

# ------------------------------------------------------ public
def make_thumbnail(topic: str, lang_code: str, out: Path):
    """
    lang_code は main.py から渡される第二字幕言語（subs[1）を想定。
    ここでは theme/topic と単語数・CEFR をまとめたキャプションを描画する。
    """
    bg    = _unsplash(topic)
    cap   = _caption(topic, lang_code)
    try:
        badge = translate(BADGE_BASE, lang_code) or BADGE_BASE
    except Exception:
        logging.exception("[translate]")
        badge = BADGE_BASE
    thumb = _draw(bg, cap, badge)
    thumb.convert("RGB").save(out, "JPEG", quality=92)
    logging.info("🖼️  Thumbnail saved → %s", out.name)