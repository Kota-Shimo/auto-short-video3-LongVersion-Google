# ================= subtitle_video.py =================
from moviepy import (
    ImageClip, TextClip, AudioFileClip, ColorClip, concatenate_videoclips
)
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
import os, unicodedata as ud, re, textwrap
from pathlib import Path

# ---------- フォント設定 ----------
FONT_DIR  = Path(__file__).parent / "fonts"
FONT_LATN = str(FONT_DIR / "RobotoSerif_36pt-Bold.ttf")
FONT_JP   = str(FONT_DIR / "NotoSansJP-Bold.ttf")
FONT_KO   = str(FONT_DIR / "malgunbd.ttf")

# ---------- X 位置ずらし ----------
SHIFT_X = 0                    # 横動画なので中央寄せ
def xpos(w: int) -> int:
    return (SCREEN_W - w) // 2 + SHIFT_X

# ---------- CJK 折り返し ----------
def wrap_cjk(text: str, width: int = 16) -> str:
    if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text):
        return "\n".join(textwrap.wrap(text, width, break_long_words=True))
    return text

# ---------- フォント存在チェック（フェイルバック有り） ----------
def _ensure_font(path: str, fallback: str) -> str:
    if os.path.isfile(path):
        return path
    # fallback が存在すればそれを返す。無ければ path のまま返す（後段 pick_font でもう一段フォールバック）
    if os.path.isfile(fallback):
        print(f"[WARN] Font not found: {path} -> fallback to {fallback}")
        return fallback
    print(f"[WARN] Font not found and no fallback present: {path}")
    return path

# 少なくともどれか1つは存在していることを期待（JP優先）
if not (os.path.isfile(FONT_JP) or os.path.isfile(FONT_LATN) or os.path.isfile(FONT_KO)):
    raise FileNotFoundError(
        "No font found. Please place at least one of these into ./fonts : "
        "NotoSansJP-Bold.ttf / RobotoSerif_36pt-Bold.ttf / malgunbd.ttf"
    )

# ラテンと韓国語は、無ければ日本語フォントへフェイルバック
FONT_LATN = _ensure_font(FONT_LATN, FONT_JP if os.path.isfile(FONT_JP) else FONT_LATN)
FONT_KO   = _ensure_font(FONT_KO,   FONT_JP if os.path.isfile(FONT_JP) else FONT_LATN)
# 日本語フォントが無い場合はラテンへ
if not os.path.isfile(FONT_JP) and os.path.isfile(FONT_LATN):
    print("[WARN] JP font not found -> fallback to LATN font for JP text.")
    FONT_JP = FONT_LATN

def pick_font(text: str) -> str:
    """文字種から推奨フォントを返す（最終的に存在確認して返却）。"""
    chosen = FONT_LATN
    for ch in text:
        name = ud.name(ch, "")
        if "HANGUL" in name:
            chosen = FONT_KO
            break
        if any(tag in name for tag in ("CJK", "HIRAGANA", "KATAKANA")):
            chosen = FONT_JP
            break
    # 最後に存在確認し、無ければ安全側で LATN → JP → KO の順で代替
    if os.path.isfile(chosen):
        return chosen
    if os.path.isfile(FONT_LATN):
        return FONT_LATN
    if os.path.isfile(FONT_JP):
        return FONT_JP
    if os.path.isfile(FONT_KO):
        return FONT_KO
    # ここに来るのは稀。上の総合チェックで止まる想定
    return chosen

# ============ レイアウト定数（横動画用） ============
SCREEN_W, SCREEN_H = 1920, 1080
DEFAULT_FSIZE_TOP  = 75   # ← デフォルト上段サイズ
DEFAULT_FSIZE_BOT  = 70   # ← デフォルト下段サイズ
TEXT_W             = 1500
POS_Y              = 880
LINE_GAP           = 26
BOTTOM_MARGIN      = 30
PAD_X, PAD_Y       = 22, 16
# ===================================================

# ---------- 半透明黒帯 ----------
def _bg(txt: TextClip) -> ColorClip:
    return ColorClip((txt.w + PAD_X * 2, txt.h + PAD_Y * 2), (0, 0, 0)).with_opacity(0.55)

# ---------- メインビルド関数 ----------
def build_video(
    lines,
    bg_path,
    voice_mp3,
    out_mp4,
    rows: int = 2,
    fsize_top: int = DEFAULT_FSIZE_TOP,
    fsize_bot: int = DEFAULT_FSIZE_BOT,
    # ▼ 追加（既定は N ラベル非表示・中央寄せON）
    hide_n_label: bool = True,
    monologue_center: bool = True,
):
    """
    lines : [(speaker, row1_text, row2_text, duration_sec), ...]
    rows  : 1 = 上段のみ / 2 = 上段+下段
    fsize_top / fsize_bot : 字幕フォントサイズを外部から可変指定
    hide_n_label : True のとき話者が 'N' の上段テキストにラベルを付けない
    monologue_center : True のとき N の行は中央寄せ（現レイアウトは常時中央寄せ）
    """
    # 背景は 1920x1080 前提（bg_image.py が既にフィットさせている想定）
    # MoviePy v2系：.resize が正式ですが、既存環境が .resized を許容している可能性があるため現状維持
    bg_base = ImageClip(bg_path).resized((SCREEN_W, SCREEN_H))
    clips = []

    for speaker, *row_texts, dur in lines:
        # ----- 上段 -----
        top_body = wrap_cjk(row_texts[0])
        # N のラベルを隠す場合は頭の "N: " を付けない
        if hide_n_label and (speaker == "N"):
            top_txt = top_body
        else:
            top_txt = f"{speaker}: {top_body}"
        top_clip = TextClip(
            text=top_txt,
            font=pick_font(top_body),
            font_size=fsize_top,
            color="white", stroke_color="black", stroke_width=8,
            method="caption", size=(TEXT_W, None),
        )
        top_bg   = _bg(top_clip)

        # monologue_center は現行の中央寄せレイアウトと同じ（将来の調整フック用）
        x_top = xpos(top_bg.w if monologue_center and speaker == "N" else top_bg.w)

        elem = [
            top_bg  .with_position((x_top,  POS_Y - PAD_Y)),
            top_clip.with_position((xpos(top_clip.w), POS_Y)),
        ]
        block_h = top_bg.h

        # ----- 下段 -----
        if rows >= 2 and len(row_texts) >= 2:
            bot_body = wrap_cjk(row_texts[1]) + "\n "
            bot_clip = TextClip(
                text=bot_body,
                font=pick_font(bot_body),
                font_size=fsize_bot,
                color="white", stroke_color="black", stroke_width=4,
                method="caption", size=(TEXT_W, None),
            )
            bot_bg = _bg(bot_clip)
            y_bot  = POS_Y + top_bg.h + LINE_GAP
            elem += [
                bot_bg  .with_position((xpos(bot_bg.w),  y_bot - PAD_Y)),
                bot_clip.with_position((xpos(bot_clip.w), y_bot)),
            ]
            block_h += LINE_GAP + bot_bg.h

        # ----- はみ出し補正 -----
        overflow = POS_Y + block_h + BOTTOM_MARGIN - SCREEN_H
        if overflow > 0:
            elem = [c.with_position((c.pos(0)[0], c.pos(0)[1] - overflow)) for c in elem]

        # ----- 合成 -----
        comp = CompositeVideoClip([bg_base, *elem]).with_duration(dur)
        clips.append(comp)

    video = concatenate_videoclips(clips, method="compose").with_audio(AudioFileClip(voice_mp3))
    video.write_videofile(
        str(out_mp4),
        fps=30,
        codec="libx264",
        audio_codec="aac",
        temp_audiofile=str(Path("temp") / "temp-audio.m4a"),
        remove_temp=True
    )
# =====================================================