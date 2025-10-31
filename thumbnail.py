# thumbnail.py – Landscape thumbnail (scene | phrase), hero-phrase design / error-safe
from pathlib import Path
from io import BytesIO
import textwrap, logging, requests, random, os, time
from PIL import (
    Image, ImageDraw, ImageFont, ImageFilter,
    ImageEnhance, ImageOps
)
from openai import OpenAI
from config import OPENAI_API_KEY, UNSPLASH_ACCESS_KEY
from translate import translate

# ------------ Canvas (YouTube landscape 16:9) --------------------
W, H = 1920, 1080
SAFE_BOTTOM_RATIO = 0.12  # 下UI回避

# ------------ Font set (安全フォールバック) -----------------------
FONT_DIR   = Path(__file__).parent / "fonts"
FONT_LATN  = FONT_DIR / "RobotoSerif_36pt-Bold.ttf"
FONT_CJK   = FONT_DIR / "NotoSansJP-Bold.ttf"
FONT_KO    = FONT_DIR / "malgunbd.ttf"

def _font_exists(p: Path) -> bool:
    try:
        return p.exists()
    except Exception:
        return False

HAS_LATN = _font_exists(FONT_LATN)
HAS_CJK  = _font_exists(FONT_CJK)
HAS_KO   = _font_exists(FONT_KO)

def pick_font_path(text: str) -> Path | None:
    # 文字種に応じた候補、無ければ手持ちの中で最初にあるもの
    if text:
        for ch in text:
            cp = ord(ch)
            if 0xAC00 <= cp <= 0xD7A3:        # Hangul
                if HAS_KO:  return FONT_KO
                break
            if (0x4E00 <= cp <= 0x9FFF) or (0x3040 <= cp <= 0x30FF):  # CJK/Kana
                if HAS_CJK: return FONT_CJK
                break
    if HAS_LATN: return FONT_LATN
    if HAS_CJK:  return FONT_CJK
    if HAS_KO:   return FONT_KO
    return None

def load_font(text: str, size: int) -> ImageFont.FreeTypeFont:
    path = pick_font_path(text)
    try:
        if path:
            return ImageFont.truetype(str(path), size)
    except Exception:
        pass
    # 最終退避：デフォルト等幅（サイズは固定だが落ちない）
    return ImageFont.load_default()

# ------------ Language name (for GPT prompt) ---------------------
LANG_NAME = {
    "en": "English", "pt": "Portuguese", "id": "Indonesian",
    "ja": "Japanese", "ko": "Korean",    "es": "Spanish",
    "fr": "French",   "de": "German",    "it": "Italian",
    "zh": "Chinese",  "ar": "Arabic",
}

# ------------ Series / Colors -----------------------------------
SERIES_NAME = "Real Practice"
LEVEL_COLORS = {  # CEFRカラー：A1/A2/B1/B2
    "A1": (34, 197, 94),   # green
    "A2": (59, 130, 246),  # blue
    "B1": (168, 85, 247),  # purple
    "B2": (239, 68, 68),   # red
}
def _level_from_env() -> str:
    v = os.getenv("CEFR_LEVEL", "").strip().upper()
    return v if v in ("A1", "A2", "B1", "B2") else "A2"

HOOK_BY_LANG = {
    "ja": "今すぐ声に出そう",
    "en": "Say it out loud",
    "ko": "지금 바로 말해요",
    "es": "Dilo en voz alta",
    "pt": "Fale em voz alta",
    "fr": "Dites-le à voix haute",
    "id": "Ucapkan sekarang",
    "zh": "大声说出来",
}
def _hook(lang_code: str) -> str:
    return HOOK_BY_LANG.get(lang_code, HOOK_BY_LANG["en"])

# ------------ Typography / Layout --------------------------------
# フレーズ主役：フレーズ(F_H_MAIN)を大きく、シーン(F_H_SCENE)は小タグ
F_H_MAIN = 112
F_H_SCENE = 44
WRAP_MAIN = 18
WRAP_SCENE= 18

BADGE_BASE = "Lesson"
BADGE_SIZE = 56
BADGE_POS  = (40, 36)
BADGE_MAX_CHARS = 12

client = OpenAI(api_key=OPENAI_API_KEY)

# ------------------------------ helpers --------------------------
def _txt_size(draw: ImageDraw.ImageDraw, txt: str, font: ImageFont.FreeTypeFont):
    if hasattr(draw, "textbbox"):
        x1, y1, x2, y2 = draw.textbbox((0, 0), txt, font=font)
        return x2 - x1, y2 - y1
    return draw.textsize(txt, font=font)

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

def _stroke_for(font_px: int) -> int:
    return max(2, int(round(font_px * 0.05)))

def _vignette(alpha_strength=0.26) -> Image.Image:
    # 周辺減光 RGBA
    v = Image.new("L", (W, H), 0)
    cx, cy = W / 2, H / 2
    maxd = (cx**2 + cy**2) ** 0.5
    px = v.load()
    for y in range(H):
        for x in range(W):
            d = ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 / maxd
            a = int(max(0, min(255, (d ** 1.7) * 255 * alpha_strength)))
            px[x, y] = a
    rgb_layers = [Image.new("L", (W, H), 0)] * 3
    return Image.merge("RGBA", tuple(rgb_layers + [v]))

def _light_flow(alpha_max=48) -> Image.Image:
    # 右方向が僅かに明るい RGBA
    grad = Image.new("L", (W, 1))
    for x in range(W):
        val = int(alpha_max * (x / W))
        grad.putpixel((x, 0), val)
    g = grad.resize((W, H))
    rgb_layers = [Image.new("L", (W, H), 255)] * 3
    return Image.merge("RGBA", tuple(rgb_layers + [g]))

def _tone_grade(img: Image.Image) -> Image.Image:
    # Deep-Green × Gold のトーンに軽く統一（派手すぎない）
    try:
        img = ImageEnhance.Color(img).enhance(0.92)
        img = ImageEnhance.Contrast(img).enhance(1.05)
        overlay = Image.new("RGBA", img.size, (24, 46, 16, 28))   # 深緑ベール
        img.alpha_composite(overlay)
        gold = Image.new("RGBA", img.size, (255, 220, 120, 12))   # 金味
        img.alpha_composite(gold)
    except Exception:
        pass
    return img

# ------------------------------ BG / Unsplash --------------------
def _busyness_score(img_rgb: Image.Image) -> float:
    # 簡易：エッジ量平均
    try:
        e = img_rgb.convert("L").filter(ImageFilter.FIND_EDGES)
        return sum(e.getdata()) / (img_rgb.width * img_rgb.height)
    except Exception:
        return 0.0

def _unsplash(topic: str) -> Image.Image:
    """
    人物寄りの検索語で取得 → 忙し背景は強ブラー＆ベール強化。
    失敗時はダークグラデ。
    """
    if not UNSPLASH_ACCESS_KEY:
        return Image.new("RGBA", (W, H), (30, 30, 30, 255))

    query = f"{topic} person, talking, face, mouth, hand gesture"
    url = (
        "https://api.unsplash.com/photos/random"
        f"?query={requests.utils.quote(query)}"
        f"&orientation=landscape&content_filter=high"
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
        img = Image.merge("RGB", (grad.resize((W, H)),)*3)

    # フィット → 忙しさに応じブラー/ベール
    img = ImageOps.fit(img, (W, H), Image.LANCZOS, centering=(0.5, 0.5))
    score = _busyness_score(img)
    blur_r = 2.6 if score > 26 else 1.6
    veil_a = 110 if score > 26 else 80
    img = img.filter(ImageFilter.GaussianBlur(blur_r)).convert("RGBA")
    img.alpha_composite(Image.new("RGBA", (W, H), (0, 0, 0, veil_a)))
    return img

# ------------------------------ Caption (GPT + 安全化) -----------
def _punch_up(scene: str, phrase: str, lang: str) -> tuple[str, str]:
    try:
        if lang == "en":
            repl = [("Please ", ""), ("You can ", ""), ("You should ", ""), ("How to ", "")]
            for a,b in repl: phrase = phrase.replace(a,b)
            words = phrase.split()
            phrase = " ".join(words[:4]) if words else "Speak now"
            strong = {"get","ask","make","fix","stop","save","ace","say","ask"}
            if words and words[0].lower() not in strong:
                phrase = "Get " + phrase
        elif lang in ("ja","ko","zh"):
            for s in ["しましょう", "してください", "です", "ます", "ください"]:
                phrase = phrase.replace(s, "")
            if len(phrase) > 10:
                phrase = phrase[:10]
    except Exception:
        pass
    return scene, phrase

def _caption(topic: str, lang_code: str) -> str:
    lang_name = LANG_NAME.get(lang_code, "English")
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

    txt = ""
    try:
        for i in range(3):
            try:
                txt = client.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.55,
                    timeout=15
                ).choices[0].message.content.strip()
                if "|" in txt and len(txt) >= 3:
                    break
            except Exception:
                if i == 2: raise
            time.sleep(0.5 * (i + 1))
    except Exception:
        logging.exception("[OpenAI _caption]")
        # ローカルフォールバック
        default = {
            "ja": "ホテル|アップグレードお願い",
            "en": "Hotel|Ask for upgrade",
            "ko": "호텔|업그레이드 부탁해",
            "es": "Hotel|Pedir upgrade",
        }
        txt = default.get(lang_code, "Hotel|Ask for upgrade")

    parts = [p.strip() for p in txt.split("|") if p.strip()]
    if len(parts) < 2:
        seg = parts[0] if parts else "Everyday|Speak now"
        if "|" in seg:
            parts = [s.strip() for s in seg.split("|", 1)]
        else:
            mid = max(1, min(len(seg)//2, 18))
            parts = [seg[:mid].strip(), seg[mid:].strip() or "Speak now"]

    l1 = (parts[0] or "Everyday")[:22]
    l2 = (parts[1] or "Speak now")[:28]
    l1, l2 = _punch_up(l1, l2, lang_code)
    return f"{l1}|{l2}"

# ------------------------------ draw core ------------------------
def _draw(img: Image.Image, cap: str, badge_txt: str, lang_code: str) -> Image.Image:
    if img.mode != "RGBA":
        img = img.convert("RGBA")

    # 視線集中
    img.alpha_composite(_vignette(0.26))

    draw = ImageDraw.Draw(img)

    # ===== 0) CEFR バンド + 左カラーバー =====
    level = _level_from_env()
    band_color = LEVEL_COLORS.get(level, LEVEL_COLORS["A2"])
    band_h = 56
    band = Image.new("RGBA", (W, band_h), band_color + (66,))
    img.alpha_composite(band, (0, 0))
    img.alpha_composite(Image.new("RGBA", (8, H), band_color + (90,)), (0, 0))

    # 右上シリーズラベル
    series_label = f"{SERIES_NAME} | {level}"
    f_series = load_font(series_label, 40)
    sw, sh = _txt_size(draw, series_label, f_series)
    pad_x, pad_y = 18, 10
    capsule = _rounded_panel((sw + pad_x*2, sh + pad_y*2), 18, (0, 0, 0, 96), (255, 255, 255, 140), 2)
    sx = W - capsule.width - 28
    sy = (band_h - capsule.height) // 2
    img.alpha_composite(capsule, (sx, sy))
    ImageDraw.Draw(img).text((sx + pad_x, sy + pad_y), series_label, font=f_series,
                             fill=(255, 255, 255), stroke_width=1, stroke_fill=(0, 0, 0))

    # ===== 1) 見出し：フレーズ主役／シーンは小タグ =====
    l1, l2 = (cap.split("|") + [""])[:2]
    l1, l2 = l1.strip(), l2.strip()
    t_scene = textwrap.fill(l1, WRAP_SCENE) if l1 else ""
    t_phrase= textwrap.fill(l2 or l1, WRAP_MAIN)

    f_main = load_font(t_phrase, F_H_MAIN)
    f_scene= load_font(t_scene or "N",  F_H_SCENE)

    # ガラスパネルのサイズ計算（主役=フレーズで決める）
    w_main, h_main = _txt_size(draw, t_phrase, f_main)
    stroke_main = _stroke_for(getattr(f_main, "size", F_H_MAIN))
    tw = w_main + stroke_main*2
    th = h_main

    # 余白とパネル
    BASE_PAD_X, BASE_PAD_Y = 52, 36
    pad_x2 = max(BASE_PAD_X, max(24, (W - tw)//2))
    pad_y2 = max(BASE_PAD_Y, max(24, (H - th)//2))
    pw, ph = tw + pad_x2*2, th + pad_y2*2

    center_y = int(H * 0.58)
    y_panel  = min(center_y - ph//2, int(H * (1.0 - SAFE_BOTTOM_RATIO) - ph - 16))
    y_panel  = max(32, min(y_panel, H - ph - 24))
    x_panel  = (W - pw)//2

    x_txt, y_txt = x_panel + pad_x2, y_panel + pad_y2

    # ガラス（上下グラデ＋二重枠）
    radius = 34
    panel_bg = img.crop((x_panel, y_panel, x_panel+pw, y_panel+ph)).filter(ImageFilter.GaussianBlur(12)).convert("RGBA")
    veil = Image.new("RGBA", (pw, ph))
    grad = Image.new("L", (1, ph))
    for y in range(ph):
        alpha = int(64 + 24 * (1 - abs((y - ph/2) / (ph/2))))
        grad.putpixel((0, y), alpha)
    veil = Image.merge("RGBA", tuple([Image.new("L",(pw,ph),255)]*3 + [grad.resize((pw, ph))]))
    panel = Image.alpha_composite(panel_bg, veil)

    mask = Image.new("L", (pw, ph), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, pw-1, ph-1], radius, fill=255)
    panel.putalpha(mask)

    border1 = Image.new("RGBA", (pw, ph))
    ImageDraw.Draw(border1).rounded_rectangle([0, 0, pw-1, ph-1], radius, outline=(255, 255, 255, 120), width=2)
    panel = Image.alpha_composite(panel, border1)
    border2 = Image.new("RGBA", (pw, ph))
    ImageDraw.Draw(border2).rounded_rectangle([1, 1, pw-2, ph-2], radius-1, outline=(0, 0, 0, 40), width=1)
    panel = Image.alpha_composite(panel, border2)
    img.paste(panel, (x_panel, y_panel), panel)

    # シーン小タグ（パネル上に小さく）
    if t_scene:
        sc_f = load_font(t_scene, 34)
        sc_w, sc_h = _txt_size(draw, t_scene, sc_f)
        sc_capsule = _rounded_panel((sc_w+24, sc_h+16), 14, (0,0,0,84), (255,255,255,140), 2)
        img.alpha_composite(sc_capsule, (x_txt, y_txt - sc_capsule.height - 14))
        ImageDraw.Draw(img).text((x_txt+12, y_txt - sc_capsule.height - 14 + 8), t_scene,
                                 font=sc_f, fill=(255,255,255), stroke_width=1, stroke_fill=(0,0,0))

    # フレーズ（主役）Glow → 本描画
    glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    gd   = ImageDraw.Draw(glow)
    gd.text((x_txt, y_txt), t_phrase, font=f_main, fill=(255, 255, 255, 255))
    glow_radius = max(10, min(18, int(((w_main + stroke_main*2) / W) * 22)))
    glow = glow.filter(ImageFilter.GaussianBlur(glow_radius))
    glow = ImageEnhance.Brightness(glow).enhance(1.08)
    img.alpha_composite(glow)

    ImageDraw.Draw(img).text((x_txt, y_txt), t_phrase, font=f_main, fill=(255, 255, 255),
                             stroke_width=stroke_main, stroke_fill=(0, 0, 0))

    # ===== 2) 左上バッジ（ガラスミニカプセル） =====
    try:
        badge_txt = (badge_txt or BADGE_BASE).strip()
        if len(badge_txt) > BADGE_MAX_CHARS:
            badge_txt = badge_txt[:BADGE_MAX_CHARS - 1] + "…"
        bf  = load_font(badge_txt, BADGE_SIZE)

        bx, by = BADGE_POS
        bw, bh = int(W*0.14), int(BADGE_SIZE*1.6)
        badge_bg = img.crop((bx, by, bx+bw, by+bh)).filter(ImageFilter.GaussianBlur(6)).convert("RGBA")
        badge_veil = Image.new("RGBA", (bw, bh), (0, 0, 0, 80))
        bb = Image.alpha_composite(badge_bg, badge_veil)
        mask_b = Image.new("L", (bw, bh), 0)
        ImageDraw.Draw(mask_b).rounded_rectangle([0, 0, bw-1, bh-1], 14, fill=255)
        bb.putalpha(mask_b)
        img.paste(bb, (bx, by), bb)

        ImageDraw.Draw(img).text((bx+16, by+(bh-BADGE_SIZE)//2 - 2), badge_txt, font=bf,
                                 fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))
    except Exception:
        pass  # バッジは描けなくても致命ではない

    # ===== 3) フック（下部） =====
    hook = _hook(lang_code)
    f_hook = load_font(hook, 44)
    hw, hh = _txt_size(draw, hook, f_hook)
    hx = (W - hw)//2
    hy = int(H * (1.0 - SAFE_BOTTOM_RATIO) - hh - 18)
    hy = min(hy, H - hh - 24)

    hook_capsule = _rounded_panel((hw+28, hh+18), 16, band_color + (96,), (255, 255, 255, 140), 2)
    img.alpha_composite(hook_capsule, (hx-14, hy-9))
    inner = Image.new("RGBA", (hw+28, hh+18), (0, 0, 0, 0))
    ImageDraw.Draw(inner).rounded_rectangle([2, 2, hw+28-2, hh+18-2], 16, outline=(0, 0, 0, 70), width=2)
    img.alpha_composite(inner, (hx-14, hy-9))

    ImageDraw.Draw(img).text((hx, hy), hook, font=f_hook,
                             fill=(255, 255, 255), stroke_width=2, stroke_fill=(0, 0, 0))

    # 右方向の光
    img.alpha_composite(_light_flow(alpha_max=48))

    return img

# ------------------------------ public ---------------------------
def make_thumbnail(topic: str, lang_code: str, out: Path):
    """
    lang_code は main.py から渡される第二字幕言語（subs[1]）想定。
    1920×1080 サムネを生成（フレーズ主役・人物寄り背景・視線誘導・ブランド色）。
    """
    try:
        bg  = _unsplash(topic)
    except Exception:
        logging.exception("[BG]")
        bg  = Image.new("RGBA", (W, H), (30,30,30,255))

    try:
        cap = _caption(topic, lang_code)       # "scene|phrase"
    except Exception:
        logging.exception("[Caption]")
        cap = "Everyday|Speak now"

    try:
        badge = translate(BADGE_BASE, lang_code) or BADGE_BASE
    except Exception:
        logging.exception("[translate BADGE]")
        badge = BADGE_BASE

    thumb = _draw(bg, cap, badge, lang_code)

    # 色調統一
    thumb = _tone_grade(thumb)

    # シャープ
    try:
        thumb = thumb.filter(ImageFilter.UnsharpMask(radius=1.4, percent=120, threshold=3))
    except Exception:
        pass

    thumb.convert("RGB").save(out, "JPEG", quality=90, optimize=True)
    logging.info("🖼️  Thumbnail saved (Landscape, Hero Phrase) → %s", out.name)