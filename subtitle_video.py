# ================= subtitle_video.py =================
from moviepy import (
    ImageClip, TextClip, AudioFileClip, ColorClip, concatenate_videoclips
)
from moviepy.video.compositing.CompositeVideoClip import CompositeVideoClip
import os, unicodedata as ud, re, textwrap, math
from pathlib import Path

FONT_DIR  = Path(__file__).parent / "fonts"
FONT_LATN = str(FONT_DIR / "RobotoSerif_36pt-Bold.ttf")
FONT_JP   = str(FONT_DIR / "NotoSansJP-Bold.ttf")
FONT_KO   = str(FONT_DIR / "malgunbd.ttf")

# ===== レイアウト定数 =====
SCREEN_W, SCREEN_H = 1080, 1920
TEXT_W             = 880
POS_Y              = 920
LINE_GAP           = 28
BOTTOM_MARGIN      = 40
PAD_X, PAD_Y       = 22, 16

FONT_SIZE_TOP      = 50   # 上段（音声言語）
FONT_SIZE_BOT      = 45   # 下段（翻訳）
MAX_TOP_LINES      = 3
MAX_BOT_LINES      = 3
MIN_FONT_SIZE      = 28   # 自動縮小の下限

# ── X 位置ずらし（デフォはやや左寄せ） ─────────────
SHIFT_X = -45
def xpos(w: int) -> int:
    return (SCREEN_W - w) // 2 + SHIFT_X

# 中央寄せ（N のときのオプション）
def xpos_center(w: int) -> int:
    return (SCREEN_W - w) // 2

# ── CJK 折り返し ────────────────────────────────
_CJK_RE = re.compile(r"[\u3040-\u30ff\u4e00-\u9fff]")
def is_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(text or ""))

def wrap_cjk(text: str, width: int = 16) -> str:
    """日本語などCJK系を width 文字で手動改行"""
    if is_cjk(text):
        return "\n".join(textwrap.wrap(text, width, break_long_words=True))
    return text

def wrap_latn(text: str, width: int = 36) -> str:
    """アルファベット系は単語折り返し（語を極力切らない）"""
    return textwrap.fill(
        text or "",
        width=width,
        break_long_words=False,
        break_on_hyphens=True,
    )

def trim_lines(text: str, max_lines: int) -> str:
    """最大行数を超える場合は末尾に…を付けてトリム"""
    lines = (text or "").splitlines() or [""]
    if len(lines) <= max_lines:
        return "\n".join(lines)
    kept = lines[:max_lines]
    kept[-1] = kept[-1].rstrip() + "…"
    return "\n".join(kept)

# ── テキスト共通サニタイズ ───────────────────────
_CTRL = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F]")
def sanitize(t: str) -> str:
    t = (t or "").replace("\r", "")
    t = _CTRL.sub("", t).strip()
    return t

# ── フォント管理（存在しなければフォールバック） ─────────
def _first_existing(paths):
    for p in paths:
        if p and os.path.isfile(p):
            return p
    return None

def pick_font(text: str) -> str:
    """文字種を見て適切なフォントパスを返す（なければラテンにフォールバック）"""
    txt = text or ""
    for ch in txt:
        name = ud.name(ch, "")
        if "HANGUL" in name:
            return _first_existing([FONT_KO, FONT_JP, FONT_LATN]) or FONT_LATN
        if any(tag in name for tag in ("CJK", "HIRAGANA", "KATAKANA")):
            return _first_existing([FONT_JP, FONT_LATN]) or FONT_LATN
    return _first_existing([FONT_LATN, FONT_JP, FONT_KO]) or FONT_LATN

# ── 半透明黒帯 ──────────────────────────────────
def _bg(txt: TextClip) -> ColorClip:
    return (
        ColorClip((txt.w + PAD_X * 2, txt.h + PAD_Y * 2), (0, 0, 0))
        .with_opacity(0.55)
    )

# ── 過大レイアウト時のフォント縮小 ─────────────────
def _fit_block_height(top_h, bot_h, rows, fs_top, fs_bot):
    """はみ出す場合にフォントサイズを縮小して返す"""
    block_h = top_h
    if rows >= 2:
        block_h += LINE_GAP + bot_h
    overflow = POS_Y + block_h + BOTTOM_MARGIN - SCREEN_H
    if overflow <= 0:
        return fs_top, fs_bot, 0  # そのままでOK
    # はみ出し量から縮小率を概算（安全に 0.8〜0.95 程度で抑制）
    scale = max(0.8, min(0.95, 1.0 - overflow / max(1, block_h)))
    new_top = max(MIN_FONT_SIZE, math.floor(fs_top * scale))
    new_bot = max(MIN_FONT_SIZE, math.floor(fs_bot * scale))
    return new_top, new_bot, overflow

# ────────────────────────────────────────────────
def build_video(
    lines,
    bg_path,
    voice_mp3,
    out_mp4,
    rows: int = 2,
    # ▼ 受け口（chunk_builder から渡る）
    fsize_top=None,
    fsize_bot=None,
    hide_n_label: bool = True,
    monologue_center: bool = False
):
    """
    lines : [(speaker, row1_text, row2_text, duration_sec), ...]
    rows  : 1 = 上段のみ / 2 = 上段+下段
    hide_n_label    : True のとき N のラベルを表示しない
    monologue_center: True のとき N の本文ブロックを中央寄せ配置
    """
    # 入力存在チェック
    if not os.path.isfile(bg_path):
        raise FileNotFoundError(f"Background image not found: {bg_path}")
    if not os.path.isfile(voice_mp3):
        raise FileNotFoundError(f"Voice mp3 not found: {voice_mp3}")

    # 背景のロード
    bg_base = ImageClip(bg_path).resized((SCREEN_W, SCREEN_H))
    clips = []

    # フォントサイズ（指定があれば優先）
    base_FS_TOP = int(fsize_top or FONT_SIZE_TOP)
    base_FS_BOT = int(fsize_bot or FONT_SIZE_BOT)

    # 1 クリップずつ合成
    for entry in lines:
        # entry は [speaker, line1, line2, dur] 形式を想定
        # rows=1 の場合は line2 が無くても動くように防御
        speaker = entry[0] if len(entry) > 0 else "N"
        line1   = entry[1] if len(entry) > 1 else ""
        line2   = entry[2] if len(entry) > 2 else ""
        dur     = float(entry[3]) if len(entry) > 3 else 1.0
        if dur <= 0:
            dur = 0.8  # 最低限の秒数を確保

        is_n = (speaker == "N")
        posf = xpos_center if (is_n and monologue_center) else xpos

        # ---------- 上段テキスト整形 ----------
        top_body_raw = sanitize(line1)
        if is_cjk(top_body_raw):
            top_body = wrap_cjk(top_body_raw, width=16)
        else:
            top_body = wrap_latn(top_body_raw, width=36)
        top_body = trim_lines(top_body, MAX_TOP_LINES)

        # ラベル表示ルール：
        #  - N：hide_n_label=True なら非表示
        #  - Alice/Bob：常に表示（会話の可読性優先）
        show_label = (not is_n) or (is_n and not hide_n_label)
        top_txt = f"{speaker}: {top_body}" if show_label else top_body

        fs_top = base_FS_TOP
        fs_bot = base_FS_BOT

        # 一旦作ってサイズ確認 → はみ出すなら自動縮小
        # 先に上段だけ作る
        top_clip = TextClip(
            text=top_txt,
            font=pick_font(top_body),
            font_size=fs_top,
            color="white", stroke_color="black", stroke_width=4,
            method="caption", size=(TEXT_W, None),
        )
        top_bg   = _bg(top_clip)

        # 下段がある場合のみ準備
        bot_clip = None
        bot_bg   = None
        if rows >= 2:
            bot_body_raw = sanitize(line2)
            if is_cjk(bot_body_raw):
                bot_body = wrap_cjk(bot_body_raw, width=18) + "\n "
            else:
                bot_body = wrap_latn(bot_body_raw, width=40) + "\n "
            bot_body = trim_lines(bot_body, MAX_BOT_LINES)

            bot_clip = TextClip(
                text=bot_body,
                font=pick_font(bot_body),
                font_size=fs_bot,
                color="white", stroke_color="black", stroke_width=4,
                method="caption", size=(TEXT_W, None),
            )
            bot_bg = _bg(bot_clip)

        # 全体高さで適合チェック → 必要ならフォント縮小して作り直し
        top_h = top_bg.h
        bot_h = bot_bg.h if bot_bg else 0
        fs_top2, fs_bot2, overflow = _fit_block_height(top_h, bot_h, rows, fs_top, fs_bot)

        if (fs_top2 != fs_top) or (rows >= 2 and fs_bot2 != fs_bot):
            # 作り直し
            fs_top, fs_bot = fs_top2, fs_bot2
            top_clip = TextClip(
                text=top_txt,
                font=pick_font(top_body),
                font_size=fs_top,
                color="white", stroke_color="black", stroke_width=4,
                method="caption", size=(TEXT_W, None),
            )
            top_bg   = _bg(top_clip)
            if rows >= 2:
                bot_clip = TextClip(
                    text=bot_body,
                    font=pick_font(bot_body),
                    font_size=fs_bot,
                    color="white", stroke_color="black", stroke_width=4,
                    method="caption", size=(TEXT_W, None),
                )
                bot_bg = _bg(bot_clip)

        # ---------- 配置 ----------
        elem = [
            top_bg  .with_position((posf(top_bg.w),  POS_Y - PAD_Y)),
            top_clip.with_position((posf(top_clip.w), POS_Y)),
        ]
        y_cursor = POS_Y + top_bg.h

        if rows >= 2 and bot_clip is not None and bot_bg is not None:
            y_bot = y_cursor + LINE_GAP
            elem += [
                bot_bg  .with_position((posf(bot_bg.w),  y_bot - PAD_Y)),
                bot_clip.with_position((posf(bot_clip.w), y_bot)),
            ]
            y_cursor = y_bot + bot_bg.h

        # はみ出し微調整（最終安全策）
        overflow2 = y_cursor + BOTTOM_MARGIN - SCREEN_H
        if overflow2 > 0:
            elem = [c.with_position((c.pos(0)[0], c.pos(0)[1] - overflow2)) for c in elem]

        # ---------- 合成 ----------
        comp = CompositeVideoClip([bg_base, *elem]).with_duration(dur)
        clips.append(comp)

    # オーディオ合成
    video = concatenate_videoclips(clips, method="compose")
    audio = AudioFileClip(voice_mp3)
    video = video.with_audio(audio)

    # 出力（例外発生時でもリソースを閉じる）
    try:
        video.write_videofile(out_mp4, fps=30, codec="libx264", audio_codec="aac")
    finally:
        # 明示的にクローズしてファイルロックを避ける
        try:
            audio.close()
        except Exception:
            pass
        try:
            video.close()
        except Exception:
            pass
# =====================================================