#!/usr/bin/env python
"""
main.py – VOCAB専用版（単純結合＋日本語ふりがな[TTSのみ]＋先頭無音＋最短1秒）

改善点:
- 例文は句読点付き自然文のみ生成（英字・記号除去・再生成あり）
- 単語行に句点を付けない（イントネーション崩れ防止）
- ENHANCE_DISABLE=1 でポスプロ無効化テスト可能
"""

import argparse, logging, re, json, subprocess, os
from datetime import datetime
from shutil import rmtree
from pathlib import Path
import yaml
from pydub import AudioSegment
from openai import OpenAI

from config import BASE, OUTPUT, TEMP
from translate import translate
from tts_openai import speak
from audio_fx import enhance
from bg_image import fetch as fetch_bg
from thumbnail import make_thumbnail
from upload_youtube import upload
from topic_picker import pick_by_content_type

# ───────────────────────────────────────────────
GPT = OpenAI()
DEBUG_SCRIPT = os.getenv("DEBUG_SCRIPT", "0") == "1"
GAP_MS = int(os.getenv("GAP_MS", "120"))
PRE_SIL_MS = int(os.getenv("PRE_SIL_MS", "120"))
MIN_UTTER_MS = int(os.getenv("MIN_UTTER_MS", "1000"))
ENHANCE_DISABLE = os.getenv("ENHANCE_DISABLE", "0") == "1"

# ───────────────────────────────────────────────
with open(BASE / "combos.yaml", encoding="utf-8") as f:
    COMBOS = yaml.safe_load(f)["combos"]

LANG_NAME = {
    "en": "English", "pt": "Portuguese", "id": "Indonesian",
    "ja": "Japanese", "ko": "Korean", "es": "Spanish",
}

# ───────────────────────────────────────────────
# 例文生成（厳格化）
# ───────────────────────────────────────────────
def _gen_example_sentence(word: str, lang_code: str) -> str:
    """単語を使った自然な短文を生成（句読点付き、英字・記号削除）"""
    lang = LANG_NAME.get(lang_code, "Japanese")
    prompt = (
        f"Write one short, natural sentence in {lang} using the word '{word}'.\n"
        "Rules:\n"
        "- Must sound natural and complete.\n"
        "- Use only the target language script (for Japanese: ひらがな・カタカナ・常用漢字)\n"
        "- No English letters, emojis, or brackets.\n"
        "- End with a full stop (for Japanese: 。)\n"
        "- Return only the sentence."
    )
    for _ in range(2):  # 最大2回まで再試行
        try:
            rsp = GPT.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.4,
            )
            sent = (rsp.choices[0].message.content or "").strip()
            sent = re.sub(r'[\"“”\'\[\]\(\){}<>/\\|~^_=+*#@🙂-🙃0-9]+', '', sent)
            sent = re.sub(r"\s+", " ", sent)
            sent = sent.strip()
            if lang_code == "ja" and not sent.endswith("。"):
                sent += "。"
            if 5 <= len(sent) <= 40:
                return sent
        except Exception:
            continue
    return f"{word} を使って練習しましょう。"

# ───────────────────────────────────────────────
# ふりがな処理
# ───────────────────────────────────────────────
_KANJI_ONLY = re.compile(r"^[一-龥々]+$")
def _kana_reading(word: str) -> str:
    try:
        rsp = GPT.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role": "user",
                       "content": f"次の日本語単語の読みをひらがなで1語だけ返してください。\n単語: {word}"}],
            temperature=0.0,
        )
        yomi = re.sub(r"[^ぁ-ゖゝゞー]", "", (rsp.choices[0].message.content or ""))
        return yomi[:20]
    except Exception:
        return ""

# ───────────────────────────────────────────────
# 音声の単純結合
# ───────────────────────────────────────────────
def _concat_with_gaps(audio_paths, gap_ms=120, pre_ms=120, min_ms=1000):
    combined = AudioSegment.silent(duration=0)
    durs = []
    for idx, path in enumerate(audio_paths):
        seg = AudioSegment.from_file(path)
        seg = AudioSegment.silent(duration=pre_ms) + seg
        if len(seg) < min_ms:
            seg += AudioSegment.silent(duration=min_ms - len(seg))
        combined += seg
        if idx < len(audio_paths) - 1:
            combined += AudioSegment.silent(duration=gap_ms)
        durs.append((len(seg) + (gap_ms if idx < len(audio_paths)-1 else 0)) / 1000)
    combined.export(TEMP / "full_raw.wav", format="wav")
    return durs

# ───────────────────────────────────────────────
# 1コンボ実行
# ───────────────────────────────────────────────
def run_one(topic, turns, audio_lang, subs, title_lang, yt_privacy, account, do_upload, chunk_size):
    from translate import translate
    from audio_fx import enhance
    reset_temp()

    raw = (topic or "").replace("\r", "\n").strip()
    words_count = int(os.getenv("VOCAB_WORDS", "6"))
    vocab_words = [w.strip() for w in re.split(r"[\n,;]+", raw) if w.strip()]
    if not vocab_words:
        vocab_words = ["check-in", "reservation", "checkout", "lobby", "upgrade", "receipt"]

    dialogue = []
    for w in vocab_words:
        ex = _gen_example_sentence(w, audio_lang)
        dialogue.extend([("N", w), ("N", w), ("N", ex)])

    audio_parts, sub_rows = [], [[] for _ in subs]
    plain_lines, tts_lines = [], []

    for i, (spk, line) in enumerate(dialogue, 1):
        block_pos = (i - 1) % 3  # 0:単語1, 1:単語2, 2:例文
        tts_line = line
        if audio_lang == "ja" and _KANJI_ONLY.fullmatch(line):
            yomi = _kana_reading(line)
            if yomi:
                tts_line = yomi
        # 例文行のみ句点を追加
        if audio_lang == "ja" and block_pos == 2 and not re.search(r"[。!?！？]$", tts_line):
            tts_line += "。"

        out_audio = TEMP / f"{i:02d}.wav"
        speak(audio_lang, spk, tts_line, out_audio, style="neutral")
        audio_parts.append(out_audio)
        plain_lines.append(line)
        tts_lines.append(tts_line)

        # 字幕
        for r, lang in enumerate(subs):
            sub_rows[r].append(line if lang == audio_lang else translate(line, lang))

    # 音声連結・エンハンス
    durs = _concat_with_gaps(audio_parts, gap_ms=GAP_MS, pre_ms=PRE_SIL_MS, min_ms=MIN_UTTER_MS)
    if ENHANCE_DISABLE:
        AudioSegment.from_file(TEMP/"full_raw.wav").export(TEMP/"full.mp3", format="mp3")
    else:
        enhance(TEMP/"full_raw.wav", TEMP/"full.wav")
        AudioSegment.from_file(TEMP/"full.wav").export(TEMP/"full.mp3", format="mp3")

    # デバッグ出力
    if DEBUG_SCRIPT:
        (TEMP/"script_raw.txt").write_text("\n".join(plain_lines), encoding="utf-8")
        (TEMP/"script_tts.txt").write_text("\n".join(tts_lines), encoding="utf-8")

    # lines.json 出力
    lines = []
    for (spk, txt), dur in zip(dialogue, durs):
        row = [spk] + [sub_rows[r][len(lines)] for r in range(len(subs))] + [dur]
        lines.append(row)
    (TEMP/"lines.json").write_text(json.dumps(lines, ensure_ascii=False, indent=2), encoding="utf-8")

    # 背景
    bg_png = TEMP / "bg.png"
    fetch_bg(vocab_words[0], bg_png)

    # 動画生成
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_mp4 = OUTPUT / f"{audio_lang}-{'_'.join(subs)}_{stamp}.mp4"
    subprocess.run([
        "python", str(BASE/"chunk_builder.py"),
        str(TEMP/"lines.json"), str(TEMP/"full.mp3"), str(bg_png),
        "--chunk", str(chunk_size), "--rows", str(len(subs)), "--out", str(out_mp4)
    ], check=True)

# ───────────────────────────────────────────────
def reset_temp():
    if TEMP.exists(): rmtree(TEMP)
    TEMP.mkdir(exist_ok=True)

# ───────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    ap = argparse.ArgumentParser()
    ap.add_argument("topic")
    ap.add_argument("--no-upload", action="store_true")
    args = ap.parse_args()
    run_one(args.topic, 0, "ja", ["ja","en"], "ja", "unlisted", "default", not args.no_upload, 9999)