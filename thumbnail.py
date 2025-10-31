# thumbnail.py – Landscape thumbnail (scene | phrase), centered glass panel
from pathlib import Path
from io import BytesIO
import textwrap, logging, requests, random
from PIL import (
    Image, ImageDraw, ImageFont, ImageFilter,
    ImageEnhance, ImageOps
)
from openai import OpenAI
from config import OPENAI_API_KEY, UNSPLASH_ACCESS_KEY
from translate import translate

# ------------ Canvas (YouTube landscape 16:9) --------------------
W, H = 1920, 1080                   # ← 横向きに変更
SAFE_BOTTOM_RATIO = 0.12            # 下の被り回避をやや小さめに

# ------------ Font set -------------------------------------------
FONT_DIR   = Path(__file__).parent / "fonts"
FONT_LATN  = FONT_DIR / "RobotoSerif_36pt-Bold.ttf"
FONT_CJK   = FONT_DIR / "NotoSansJP-Bold.ttf"
FONT_KO    = FONT_DIR / "malgunbd.ttf"

for fp in (FONT_LATN, FONT_CJK, FONT_KO):
    if not fp.exists():
        raise FileNotFoundError(f"Font missing: {fp}")

def pick_font(text: str) -> str:
    for ch in text:
        cp = ord(ch)
        if 0xAC00 <= cp <= 0xD7A3:        # Hangul
            return str(FONT_KO)
        if (0x4E00 <= cp <= 0x9FFF) or (0x3040 <= cp <= 0x30FF):
            return str(FONT_CJK)          # CJK/Kana
    return str(FONT_LATN)

# ------------ Language name (for GPT prompt) ---------------------
LANG_NAME = {
    "en": "English", "pt": "Portuguese", "id": "Indonesian",
    "ja": "Japanese", "ko": "Korean",    "es": "Spanish",
    "fr": "French",   "de": "German",    "it": "Italian",
    "zh": "Chinese",  "ar": "Arabic",
}

# ------------ Caption sizes / wrapping (landscape) ---------------
# 横は横幅が広いので文字サイズを少し下げて見やすく、wrap も緩めに
F_H1, F_H2       = 96, 68
WRAP_H1, WRAP_H2 = 20, 26

# ------------ Badge ----------------------------------------------
BADGE_BASE = "Lesson"
BADGE_SIZE = 56
BADGE_POS  = (40, 36)

client = OpenAI(api_key=OPENAI_API_KEY)

# ------------------------------------------------------ Unsplash BG
def _unsplash(topic: str) -> Image.Image:
    """
    Unsplash landscape → 1920×1080 fit.
    失敗時はダークグラデーション。
    """
    if not UNSPLASH_ACCESS_KEY:
        return Image.new("RGB", (W, H), (30, 30, 30))

    url = (
        "https://api.unsplash.com/photos/random"
        f"?query={requests.utils.quote(topic)}"
        f"&orientation=landscape&content_filter=high"  # ← landscape に固定
        f"&client_id={UNSPLASH_ACCESS_KEY}"
        f"&sig={random.randint(1, 999999)}"
    )
    try:
        r = requests.get(url, timeout=15); r.raise_for_status()
        img_url = r.json().get("urls", {}).get("regular")
        if not img_url:
            raise ValueError("Unsplash: no image url")
        raw = requests.get(img_url, timeout=15).content
        img = Image.open(BytesIO(raw)).convert("RGB")
    except Exception:
        logging.exception("[Unsplash]")
        # fallback: simple dark gradient
        grad = Image.new("L", (1, H))
        for y in range(H):
            grad.putpixel((0, y), int(60 + 120 * (y / H)))
        img = Image.merge("RGB", (
            grad.resize((W, H)), grad.resize((W, H)), grad.resize((W, H))
        ))

    # 横向き 1920x1080 に黒帯なしでフィット
    img = ImageOps.fit(img, (W, H), Image.LANCZOS, centering=(0.5, 0.5))
    img = img.filter(ImageFilter.GaussianBlur(1.6)).convert("RGBA")
    # 30% veil for text contrast（横は少し弱め）
    img.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 78)))
    return img

# ------------------------------------------------------ GPT Caption (scene | phrase)
def _caption(topic: str, lang_code: str) -> str:
    lang_name = LANG_NAME.get(lang_code, "English")
    # 横向き前提で文字数閾値を少し拡大
    prompt = (
        "You craft high-performing YouTube thumbnail captions.\n"
        f"Language: {lang_name} ONLY.\n"
        "Return TWO ultra-short lines separated by a single '|' character:\n"
        " - Line 1: the SCENE label (e.g., Hotel / Airport / Restaurant / At Work) — ≤ 18 chars.\n"
        " - Line 2: the key PHRASE learners will master — ≤ 26 chars.\n"
        "Rules: no quotes/emojis, no surrounding punctuation, no translation, "
        "use natural words in the requested language, avoid brand names.\n"
        f"Topic: {topic}\n"
        "Output example (do not translate this example):\n"
        "Hotel|Check-in made easy"
    )

    txt = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.55
    ).choices[0].message.content.strip()

    parts = [p.strip() for p in txt.split("|") if p.strip()]
    if len(parts) == 1:
        seg = parts[0]
        mid = max(1, min(len(seg) // 2, 18))
        parts = [seg[:mid].strip(), seg[mid:].strip()]
    # hard cap (visual safety for landscape)
    return f"{parts[0][:22]}|{parts[1][:28]}"

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

    f1 = ImageFont.truetype(pick_font(l1),          F_H1)
    f2 = ImageFont.truetype(pick_font(l2 or l1),    F_H2)

    t1 = textwrap.fill(l1, WRAP_H1)
    t2 = textwrap.fill(l2, WRAP_H2) if l2 else ""

    w1, h1 = _txt_size(draw, t1, f1)
    w2, h2 = (_txt_size(draw, t2, f2) if t2 else (0, 0))

    stroke = 4
    tw = max(w1, w2) + stroke*2
    th = h1 + (h2 + 14 if t2 else 0)

    # Panel padding (landscape / safe area)
    BASE_PAD_X, BASE_PAD_Y = 52, 36
    pad_x = min(BASE_PAD_X, max(24, (W - tw)//2))
    pad_y = min(BASE_PAD_Y, max(24, (H - th)//2))

    pw, ph = tw + pad_x*2, th + pad_y*2

    # y: 画面中央やや下、ただし下 UI 被りを避ける
    center_y = int(H * 0.58)
    y_panel  = min(center_y - ph//2, int(H * (1.0 - SAFE_BOTTOM_RATIO) - ph - 16))
    y_panel  = max(32, y_panel)
    x_panel  = (W - pw)//2

    x_txt, y_txt = x_panel + pad_x, y_panel + pad_y

    # glass panel
    radius = 34
    panel_bg = img.crop((x_panel, y_panel, x_panel+pw, y_panel+ph)) \
                  .filter(ImageFilter.GaussianBlur(12)).convert("RGBA")
    veil     = Image.new("RGBA", (pw, ph), (255,255,255,78))
    panel    = Image.alpha_composite(panel_bg, veil)

    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0,0,pw-1,ph-1], radius, fill=255)
    panel.putalpha(mask)

    border = Image.new("RGBA", (pw, ph))
    ImageDraw.Draw(border).rounded_rectangle(
        [0,0,pw-1,ph-1], radius, outline=(255,255,255,120), width=2)
    panel = Image.alpha_composite(panel, border)
    img.paste(panel, (x_panel, y_panel), panel)

    # glow
    glow = Image.new("RGBA", img.size, (0,0,0,0))
    gd   = ImageDraw.Draw(glow)
    gd.text((x_txt, y_txt), t1, font=f1, fill=(255,255,255,255))
    if t2:
        gd.text((x_txt, y_txt+h1+12), t2, font=f2, fill=(255,255,255,255))
    glow = glow.filter(ImageFilter.GaussianBlur(14))
    glow = ImageEnhance.Brightness(glow).enhance(1.15)
    img.alpha_composite(glow)

    # final text
    draw.text((x_txt, y_txt), t1, font=f1, fill=(255,255,255),
              stroke_width=stroke, stroke_fill=(0,0,0))
    if t2:
        draw.text((x_txt, y_txt+h1+12), t2, font=f2,
                  fill=(255,255,255), stroke_width=stroke, stroke_fill=(0,0,0))

    # badge
    bf  = ImageFont.truetype(pick_font(badge_txt), BADGE_SIZE)
    draw.text(BADGE_POS, badge_txt, font=bf,
              fill=(255,255,255), stroke_width=3, stroke_fill=(0,0,0))
    return img

# ------------------------------------------------------ public
def make_thumbnail(topic: str, lang_code: str, out: Path):
    """
    lang_code は main.py から渡される第二字幕言語（subs[1]）想定。
    横向き 1920×1080 サムネを生成。
    """
    bg    = _unsplash(topic)                 # ← landscape 取得＆フィット
    cap   = _caption(topic, lang_code)       # (scene|phrase)
    badge = translate(BADGE_BASE, lang_code) or BADGE_BASE
    thumb = _draw(bg, cap, badge)
    thumb.convert("RGB").save(out, "JPEG", quality=90, optimize=True)
    logging.info("🖼️  Thumbnail saved (Landscape) → %s", out.name)