# thumbnail.py – centered glass panel + two-line caption (scene / phrase) – Polished & Error-safe
from pathlib import Path
from io import BytesIO
import textwrap, logging, requests
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

# ------------ Language name map (ISO639-1 -> English name) ----
LANG_NAME = {
    "en": "English", "pt": "Portuguese", "id": "Indonesian",
    "ja": "Japanese", "ko": "Korean",    "es": "Spanish",
    "fr": "French",   "de": "German",    "it": "Italian",
    "zh": "Chinese",  "ar": "Arabic",
}

# ------------ Caption sizes / wrapping ---------------
F_H1, F_H2          = 100, 70
WRAP_H1, WRAP_H2    = 16, 22

# ------------ Badge -----------------------------------
BADGE_BASE   = "Lesson"
BADGE_SIZE   = 60
BADGE_POS    = (40, 30)

client = OpenAI(api_key=OPENAI_API_KEY)

# ------------------------------------------------------ small helpers
def _txt_size(draw: ImageDraw.ImageDraw, txt: str, font: ImageFont.FreeTypeFont):
    if hasattr(draw, "textbbox"):
        x1, y1, x2, y2 = draw.textbbox((0, 0), txt, font=font)
        return x2 - x1, y2 - y1
    return draw.textsize(txt, font=font)

def _stroke_for(font_px: int) -> int:
    # フォントサイズに対する割合で縁取り太さを決定（最小2px）
    return max(2, int(round(font_px * 0.045)))

def _shrink_to_fit(draw, text, font_path, start_size, max_width):
    # 文字がはみ出す場合、2ptずつ縮小して収める
    size = start_size
    while size >= max(28, start_size - 36):
        f = ImageFont.truetype(font_path, size)
        w, _ = _txt_size(draw, text, f)
        if w <= max_width:
            return f
        size -= 2
    return ImageFont.truetype(font_path, max(28, size))

def _rounded_panel(size, radius, fill_rgba, border_rgba=None, border_w=0):
    w, h = size
    panel = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    mask  = Image.new("L", (w, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, w-1, h-1], radius, fill=255)
    bg = Image.new("RGBA", (w, h), fill_rgba)
    bg.putalpha(mask)
    if border_rgba and border_w > 0:
        border = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(border).rounded_rectangle(
            [border_w//2, border_w//2, w-1-border_w//2, h-1-border_w//2],
            radius, outline=border_rgba, width=border_w
        )
        panel = Image.alpha_composite(bg, border)
    else:
        panel = bg
    return panel

def _vignette(alpha_strength=0.24) -> Image.Image:
    # 周辺減光（RGBA のアルファだけを変化させる）
    v = Image.new("L", (W, H), 0)
    cx, cy = W / 2, H / 2
    maxd = (cx**2 + cy**2) ** 0.5
    px = v.load()
    for y in range(H):
        for x in range(W):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / maxd
            a = int(max(0, min(255, (d ** 1.6) * 255 * alpha_strength)))
            px[x, y] = a
    rgb_layers = [Image.new("L", (W, H), 0)] * 3
    return Image.merge("RGBA", tuple(rgb_layers + [v]))

# ------------------------------------------------------ Unsplash BG
def _unsplash(topic: str) -> Image.Image:
    if not UNSPLASH_ACCESS_KEY:
        return Image.new("RGB", (W, H), (35, 35, 35))

    url = (
        "https://api.unsplash.com/photos/random"
        f"?query={requests.utils.quote(topic)}"
        f"&orientation=landscape&content_filter=high"
        f"&client_id={UNSPLASH_ACCESS_KEY}"
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
    img = img.filter(ImageFilter.GaussianBlur(1.8)).convert("RGBA")
    img.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, 80)))   # 31% dark veil
    # ほんのりビネットを重ねる
    img.alpha_composite(_vignette(0.22))
    return img

# ------------------------------------------------------ GPT Caption (scene | phrase)
def _caption(topic: str, lang_code: str) -> str:
    lang_name = LANG_NAME.get(lang_code, "English")
    prompt = (
        "You craft clicky YouTube video thumbnails.\n"
        f"Language: {lang_name} ONLY.\n"
        "Task: Produce TWO ultra-short lines separated by a single '|' character:\n"
        " - Line 1: the SCENE label (e.g., Hotel / Airport / Restaurant / At Work) — ≤ 16 chars.\n"
        " - Line 2: the key PHRASE learners will master — ≤ 20 chars.\n"
        "Rules: no quotes, no punctuation around the bar, no emojis, no translation, "
        "use natural {lang} words, and avoid brand names.\n"
        f"Topic: {topic}\n"
        "Output format example:\n"
        "Hotel|Check-in made easy"
    ).replace("{lang}", lang_name)

    txt = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.6
    ).choices[0].message.content.strip()

    parts = [p.strip() for p in txt.split("|") if p.strip()]
    if len(parts) == 1:
        seg = parts[0]
        mid = max(1, min(len(seg) // 2, 16))
        parts = [seg[:mid].strip(), seg[mid:].strip()]
    # 最終ガード（視認性上の上限）
    return f"{parts[0][:22]}|{parts[1][:24]}"

# ------------------------------------------------------ draw core
def _draw(img: Image.Image, cap: str, badge_txt: str) -> Image.Image:
    if img.mode != "RGBA":
        img = img.convert("RGBA")
    draw = ImageDraw.Draw(img)

    l1, l2  = (cap.split("|") + [""])[:2]
    l1, l2  = l1.strip(), l2.strip()

    # テキスト（自動縮小で横幅に収める）
    f1 = _shrink_to_fit(draw, textwrap.fill(l1, WRAP_H1), pick_font(l1), F_H1, int(W*0.82))
    f2 = _shrink_to_fit(draw, textwrap.fill(l2 or l1, WRAP_H2), pick_font(l2 or l1), F_H2, int(W*0.82))

    t1 = textwrap.fill(l1, WRAP_H1)
    t2 = textwrap.fill(l2, WRAP_H2) if l2 else ""

    w1, h1 = _txt_size(draw, t1, f1)
    w2, h2 = (_txt_size(draw, t2, f2) if t2 else (0, 0))

    stroke1 = _stroke_for(getattr(f1, "size", F_H1))
    stroke2 = _stroke_for(getattr(f2, "size", F_H2))
    stroke   = max(stroke1, stroke2)

    tw = max(w1, w2) + stroke*2
    th = h1 + (h2 + 12 if t2 else 0)

    BASE_PAD_X, BASE_PAD_Y = 60, 40
    # “最低”余白を守りつつ中央寄せ
    pad_x = max(BASE_PAD_X, max(20, (W - tw)//2))
    pad_y = max(BASE_PAD_Y, max(20, (H - th)//2))

    pw, ph = tw + pad_x*2, th + pad_y*2
    x_panel = (W - pw)//2
    y_panel = (H - ph)//2
    x_txt   = x_panel + pad_x
    y_txt   = y_panel + pad_y

    # glass panel（上下グラデ＋二重枠）
    radius = 32
    panel_bg = img.crop((x_panel, y_panel, x_panel+pw, y_panel+ph)) \
                  .filter(ImageFilter.GaussianBlur(12)).convert("RGBA")
    # 白ベールの上下グラデ
    veil = Image.new("RGBA", (pw, ph))
    grad = Image.new("L", (1, ph))
    for y in range(ph):
        alpha = int(64 + 22 * (1 - abs((y - ph/2) / (ph/2))))
        grad.putpixel((0, y), alpha)
    veil = Image.merge("RGBA", tuple([Image.new("L",(pw,ph),255)]*3 + [grad.resize((pw, ph))]))
    panel = Image.alpha_composite(panel_bg, veil)

    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0,0,pw-1,ph-1], radius, fill=255)
    panel.putalpha(mask)

    border1 = Image.new("RGBA", (pw, ph))
    ImageDraw.Draw(border1).rounded_rectangle([0,0,pw-1,ph-1], radius, outline=(255,255,255,120), width=2)
    panel = Image.alpha_composite(panel, border1)
    border2 = Image.new("RGBA", (pw, ph))
    ImageDraw.Draw(border2).rounded_rectangle([1,1,pw-2,ph-2], radius-1, outline=(0,0,0,40), width=1)
    panel = Image.alpha_composite(panel, border2)
    img.paste(panel, (x_panel, y_panel), panel)

    # glow（幅に応じた控えめ設定）
    glow = Image.new("RGBA", img.size, (0,0,0,0))
    gd   = ImageDraw.Draw(glow)
    gd.text((x_txt, y_txt), t1, font=f1, fill=(255,255,255,255))
    if t2:
        gd.text((x_txt, y_txt+h1+12), t2, font=f2, fill=(255,255,255,255))
    glow_radius = max(10, min(18, int((tw / W) * 22)))
    glow = glow.filter(ImageFilter.GaussianBlur(glow_radius))
    glow = ImageEnhance.Brightness(glow).enhance(1.10)
    img.alpha_composite(glow)

    # final text
    draw.text((x_txt, y_txt), t1, font=f1, fill=(255,255,255),
              stroke_width=stroke1, stroke_fill=(0,0,0))
    if t2:
        draw.text((x_txt, y_txt+h1+12), t2, font=f2,
                  fill=(255,255,255), stroke_width=stroke2, stroke_fill=(0,0,0))

    # badge（小さな半透明カプセルの上に載せる）
    bf  = _shrink_to_fit(draw, badge_txt, pick_font(badge_txt), BADGE_SIZE, int(W*0.28))
    bw, bh = _txt_size(draw, badge_txt, bf)
    cap_w, cap_h = bw + 24, bh + 16
    bx, by = BADGE_POS
    badge_bg_crop = img.crop((bx, by, bx+cap_w, by+cap_h)).filter(ImageFilter.GaussianBlur(6)).convert("RGBA")
    badge_veil = Image.new("RGBA", (cap_w, cap_h), (0, 0, 0, 80))
    badge_panel = Image.alpha_composite(badge_bg_crop, badge_veil)

    mask_b = Image.new("L", (cap_w, cap_h), 0)
    ImageDraw.Draw(mask_b).rounded_rectangle([0,0,cap_w-1,cap_h-1], 14, fill=255)
    badge_panel.putalpha(mask_b)
    img.paste(badge_panel, (bx, by), badge_panel)

    draw.text((bx+12, by+(cap_h-bh)//2 - 1), badge_txt, font=bf,
              fill=(255,255,255), stroke_width=2, stroke_fill=(0,0,0))

    return img

# ------------------------------------------------------ public
def make_thumbnail(topic: str, lang_code: str, out: Path):
    """
    lang_code は main.py から渡される第二字幕言語（subs[1]）を想定。
    """
    bg    = _unsplash(topic)
    cap   = _caption(topic, lang_code)  # ← 指定言語で (scene|phrase)
    badge = translate(BADGE_BASE, lang_code) or BADGE_BASE
    thumb = _draw(bg, cap, badge)
    thumb.convert("RGB").save(out, "JPEG", quality=92)
    logging.info("🖼️  Thumbnail saved → %s", out.name)