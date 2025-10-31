# ================= subtitle_video.py =================
from moviepy import (
    ImageClip, TextClip, AudioFileClip, ColorClip, concatenate_videoclips
)
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
import os, unicodedata as ud, re, textwrap
from pathlib import Path

FONT_DIR  = Path(__file__).parent / "fonts"
FONT_LATN = str(FONT_DIR / "RobotoSerif_36pt-Bold.ttf")
FONT_JP   = str(FONT_DIR / "NotoSansJP-Bold.ttf")
FONT_KO   = str(FONT_DIR / "malgunbd.ttf")

SHIFT_X = 0
def xpos(w: int) -> int:
    return (SCREEN_W - w) // 2 + SHIFT_X

def wrap_cjk(text: str, width: int = 16) -> str:
    if re.search(r"[\u3040-\u30ff\u4e00-\u9fff]", text):
        return "\n".join(textwrap.wrap(text, width, break_long_words=True))
    return text

for f in (FONT_LATN, FONT_JP, FONT_KO):
    if not os.path.isfile(f):
        raise FileNotFoundError(f"Font not found: {f}")

def pick_font(text: str) -> str:
    for ch in text:
        name = ud.name(ch, "")
        if "HANGUL" in name:
            return FONT_KO
        if any(tag in name for tag in ("CJK", "HIRAGANA", "KATAKANA")):
            return FONT_JP
    return FONT_LATN

# 横動画レイアウト
SCREEN_W, SCREEN_H = 1920, 1080
DEFAULT_FSIZE_TOP  = 75
DEFAULT_FSIZE_BOT  = 70
TEXT_W             = 1500
POS_Y              = 880
LINE_GAP           = 26
BOTTOM_MARGIN      = 30
PAD_X, PAD_Y       = 22, 16

def _bg(txt: TextClip) -> ColorClip:
    return ColorClip((txt.w + PAD_X * 2, txt.h + PAD_Y * 2), (0, 0, 0)).with_opacity(0.55)

def build_video(
    lines,
    bg_path,
    voice_mp3,
    out_mp4,
    rows: int = 2,
    fsize_top: int = DEFAULT_FSIZE_TOP,
    fsize_bot: int = DEFAULT_FSIZE_BOT,
    # これらが来なくても動くようデフォルト定義 + 余剰吸収
    hide_n_label: bool = True,
    monologue_center: bool = True,
    **_
):
    """
    lines : [(speaker, row1_text, row2_text, duration_sec), ...]
    """
    # MoviePy のバージョン差吸収（.resized が無い環境向け）
    bg_clip = ImageClip(bg_path)
    try:
        bg_base = bg_clip.resized((SCREEN_W, SCREEN_H))
    except AttributeError:
        bg_base = bg_clip.resize((SCREEN_W, SCREEN_H))

    clips = []

    for speaker, *row_texts, dur in lines:
        # 上段
        top_body = wrap_cjk(row_texts[0])
        top_txt  = top_body if (hide_n_label and speaker == "N") else f"{speaker}: {top_body}"
        top_clip = TextClip(
            text=top_txt,
            font=pick_font(top_body),
            font_size=fsize_top,
            color="white", stroke_color="black", stroke_width=8,
            method="caption", size=(TEXT_W, None),
        )
        top_bg   = _bg(top_clip)

        x_top = xpos(top_bg.w)  # 中央寄せ（現仕様）
        elem = [
            top_bg  .with_position((x_top,  POS_Y - PAD_Y)),
            top_clip.with_position((xpos(top_clip.w), POS_Y)),
        ]
        block_h = top_bg.h

        # 下段
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

        # はみ出し補正
        overflow = POS_Y + block_h + BOTTOM_MARGIN - SCREEN_H
        if overflow > 0:
            elem = [c.with_position((c.pos(0)[0], c.pos(0)[1] - overflow)) for c in elem]

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