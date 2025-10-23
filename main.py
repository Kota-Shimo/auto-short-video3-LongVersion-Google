#!/usr/bin/env python
"""
main.py – VOCAB専用版（単純結合＋日本語ふりがな[TTSのみ]＋先頭無音＋最短1秒）
"""

import argparse, logging, re, json, subprocess, os
from datetime import datetime
from pathlib import Path
from shutil import rmtree

import yaml
from pydub import AudioSegment
from openai import OpenAI

from config         import BASE, OUTPUT, TEMP
from translate      import translate
from tts_openai     import speak
from audio_fx       import enhance
from bg_image       import fetch as fetch_bg
from thumbnail      import make_thumbnail
from upload_youtube import upload
from topic_picker   import pick_by_content_type

# ───────────────────────────────────────────────
GPT = OpenAI()
CONTENT_MODE = "vocab"
DEBUG_SCRIPT = os.getenv("DEBUG_SCRIPT", "0") == "1"
GAP_MS       = int(os.getenv("GAP_MS", "120"))
PRE_SIL_MS   = int(os.getenv("PRE_SIL_MS", "120"))
MIN_UTTER_MS = int(os.getenv("MIN_UTTER_MS", "1000"))

LANG_NAME = {
    "en": "English", "pt": "Portuguese", "id": "Indonesian",
    "ja": "Japanese","ko": "Korean", "es": "Spanish",
}

JP_CONV_LABEL = {
    "en": "英会話", "ja": "日本語会話", "es": "スペイン語会話",
    "pt": "ポルトガル語会話", "ko": "韓国語会話", "id": "インドネシア語会話",
}

with open(BASE / "combos.yaml", encoding="utf-8") as f:
    COMBOS = yaml.safe_load(f)["combos"]

def reset_temp():
    if TEMP.exists():
        rmtree(TEMP)
    TEMP.mkdir(exist_ok=True)

def sanitize_title(raw: str) -> str:
    title = re.sub(r"^\s*(?:\d+\s*[.)]|[-•・])\s*", "", raw)
    title = re.sub(r"[\s\u3000]+", " ", title).strip()
    return title[:97] + "…" if len(title) > 100 else title or "Auto Video"

def _infer_title_lang(audio_lang: str, subs: list[str], combo: dict) -> str:
    if "title_lang" in combo and combo["title_lang"]:
        return combo["title_lang"]
    if len(subs) >= 2:
        return subs[1]
    for s in subs:
        if s != audio_lang:
            return s
    return audio_lang

def resolve_topic(arg_topic: str) -> str:
    if arg_topic and arg_topic.strip().lower() == "auto":
        first_audio_lang = COMBOS[0]["audio"]
        topic = pick_by_content_type("vocab", first_audio_lang)
        logging.info(f"[AUTO VOCAB THEME] {topic}")
        return topic
    return arg_topic

# ───────────────────────────────────────────────
# 例文クリーンアップ（控えめ）
# ───────────────────────────────────────────────
_URL_RE   = re.compile(r"https?://\S+")
_NUM_LEAD = re.compile(r"^\s*\d+[\).:\-]\s*")
_QUOTES   = re.compile(r'^[\"“”\']+|[\"“”\']+$')

def _clean_example(text: str) -> str:
    if not text:
        return ""
    t = text.strip()
    t = _URL_RE.sub("", t)
    t = _NUM_LEAD.sub("", t)
    t = _QUOTES.sub("", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t

def _ensure_period_for_sentence(txt: str, lang_code: str) -> str:
    # 文の場合のみ終止記号を足す（単語行には使わない）
    if re.search(r"[。.!?！？]$", txt):
        return txt
    return txt + ("。" if lang_code == "ja" else ".")

# ───────────────────────────────────────────────
# 語彙ユーティリティ
# ───────────────────────────────────────────────
def _gen_example_sentence(word: str, lang_code: str) -> str:
    prompt = (
        f"Write one short, natural example sentence (<=12 words) in "
        f"{LANG_NAME.get(lang_code,'English')} using the word: {word}. "
        "No translation, no quotes."
    )
    try:
        rsp = GPT.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],
            temperature=0.6,
        )
        raw = (rsp.choices[0].message.content or "").strip()
    except Exception:
        raw = ""

    cleaned = _clean_example(raw)
    return cleaned if cleaned else f"Let's practice {word}."

def _gen_vocab_list(theme: str, lang_code: str, n: int) -> list[str]:
    theme_for_prompt = translate(theme, lang_code) if lang_code != "en" else theme
    prompt = (
        f"List {n} essential single or hyphenated words for {theme_for_prompt} context "
        f"in {LANG_NAME.get(lang_code,'English')}. Return ONLY one word per line, no numbering."
    )
    try:
        rsp = GPT.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":prompt}],
            temperature=0.5,
        )
        content = (rsp.choices[0].message.content or "")
    except Exception:
        content = ""
    words = [w.strip() for w in content.splitlines() if w and w.strip()]
    cleaned = []
    for w in words:
        w = re.sub(r"^\d+[\).]?\s*", "", w)
        if w and w not in cleaned:
            cleaned.append(w)
    if len(cleaned) >= n:
        return cleaned[:n]
    fallback = ["check-in", "reservation", "checkout", "receipt", "elevator", "lobby", "upgrade"]
    return fallback[:n]

# ───────────────────────────────────────────────
# 日本語TTS用ふりがな
# ───────────────────────────────────────────────
_KANJI_ONLY = re.compile(r"^[一-龥々]+$")

# 日本語TTS用：括弧を読まない（TTS文字列だけ）
_PARENS_JA = re.compile(r"\s*[\(\（][^)\）]{1,40}[\)\）]\s*")

def _kana_reading(word: str) -> str:
    try:
        rsp = GPT.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role":"user",
                "content":(
                    "次の日本語単語の読みをひらがなだけで1語返してください。"
                    "記号・括弧・説明は不要。\n"
                    f"単語: {word}"
                )
            }],
            temperature=0.0,
        )
        yomi = (rsp.choices[0].message.content or "").strip()
        yomi = re.sub(r"[^ぁ-ゖゝゞー]+", "", yomi)
        return yomi[:20]
    except Exception:
        return ""

# ───────────────────────────────────────────────
# メタ生成（タイトル言語に統一）
# ───────────────────────────────────────────────
def make_title(theme, title_lang: str, audio_lang_for_label: str | None = None):
    if title_lang not in LANG_NAME:
        title_lang = "en"
    try:
        theme_local = theme if title_lang == "en" else translate(theme, title_lang)
    except Exception:
        theme_local = theme

    if title_lang == "ja":
        base = f"{theme_local} で使える一言"
        label = JP_CONV_LABEL.get(audio_lang_for_label or "", "")
        t = f"{label} {base}" if label and label not in base else base
        return sanitize_title(t)[:28]
    else:
        if title_lang == "en":
            t = f"{theme_local.capitalize()} vocab in one minute"
        else:
            t = f"{theme_local} vocabulary"
        return sanitize_title(t)[:55]

def make_desc(theme, title_lang: str):
    if title_lang not in LANG_NAME:
        title_lang = "en"
    try:
        theme_local = theme if title_lang == "en" else translate(theme, title_lang)
    except Exception:
        theme_local = theme

    msg = {
        "ja": f"{theme_local} に必須の語彙を短時間でチェック。声に出して一緒に練習しよう！ #vocab #learning",
        "en": f"Quick practice for {theme_local} vocabulary. Repeat after the audio! #vocab #learning",
        "pt": f"Pratique rápido o vocabulário de {theme_local}. Repita em voz alta! #vocab #aprendizado",
        "es": f"Práctica rápida de vocabulario de {theme_local}. ¡Repite en voz alta! #vocab #aprendizaje",
        "ko": f"{theme_local} 어휘를 빠르게 연습하세요. 소리 내어 따라 말해요! #vocab #learning",
        "id": f"Latihan cepat kosakata {theme_local}. Ucapkan keras-keras! #vocab #belajar",
    }
    return msg.get(title_lang, msg["en"])

def make_tags(theme, audio_lang, subs, title_lang):
    tags = [
        theme, "vocabulary", "language learning", "speaking practice",
        "listening practice", "subtitles"
    ]
    for code in subs:
        if code in LANG_NAME:
            tags.append(f"{LANG_NAME[code]} subtitles")
    seen, out = set(), []
    for t in tags:
        if t not in seen:
            seen.add(t); out.append(t)
    return out[:15]

# ───────────────────────────────────────────────
# 単純結合（WAV中間・行頭無音・最短尺・行間ギャップ）
# ───────────────────────────────────────────────
def _concat_with_gaps(audio_paths, gap_ms=120, pre_ms=120, min_ms=1000):
    combined = AudioSegment.silent(duration=0)
    durs = []
    for idx, p in enumerate(audio_paths):
        seg = AudioSegment.from_file(p)
        seg = AudioSegment.silent(duration=pre_ms) + seg
        if len(seg) < min_ms:
            seg += AudioSegment.silent(duration=min_ms - len(seg))
        seg_ms = len(seg)
        extra = gap_ms if idx < len(audio_paths) - 1 else 0
        combined += seg
        if extra:
            combined += AudioSegment.silent(duration=extra)
        durs.append((seg_ms + extra) / 1000.0)
    (TEMP / "full_raw.wav").unlink(missing_ok=True)
    combined.export(TEMP / "full_raw.wav", format="wav")
    return durs

# ───────────────────────────────────────────────
# 1コンボ処理
# ───────────────────────────────────────────────
def run_one(topic, turns, audio_lang, subs, title_lang, yt_privacy, account, do_upload, chunk_size):
    reset_temp()

    raw = (topic or "").replace("\r", "\n").strip()
    is_word_list = bool(re.search(r"[,;\n]", raw)) and len([w for w in re.split(r"[\n,;]+", raw) if w.strip()]) >= 2

    words_count = int(os.getenv("VOCAB_WORDS", "6"))
    if is_word_list:
        vocab_words = [w.strip() for w in re.split(r"[\n,;]+", raw) if w.strip()]
        theme = "custom list"
    else:
        theme = topic
        vocab_words = _gen_vocab_list(theme, audio_lang, words_count)

    # 3行ブロック: 単語 → 単語 → 例文
    dialogue = []
    for w in vocab_words:
        ex = _gen_example_sentence(w, audio_lang)
        dialogue.extend([("N", w), ("N", w), ("N", ex)])

    valid_dialogue = [(spk, line) for (spk, line) in dialogue if line.strip()]
    audio_parts, sub_rows = [], [[] for _ in subs]

    plain_lines = [line for (_, line) in valid_dialogue]
    tts_lines   = []

    for i, (spk, line) in enumerate(valid_dialogue, 1):
        # 行の種類（1:単語, 2:単語, 3:例文）
        role_idx = (i - 1) % 3  # 0/1/2

        tts_line = line
        if audio_lang == "ja":
            # 例文の括弧はTTSでは読まない（日本語のみ）
            if role_idx == 2:
                tts_line = _PARENS_JA.sub(" ", tts_line).strip()
            # 漢字のみの「単語」は読みへ（1・2行目のみ対象で十分）
            if role_idx in (0, 1) and _KANJI_ONLY.fullmatch(line):
                yomi = _kana_reading(line)
                if yomi:
                    tts_line = yomi

        # 句読点付与は「例文行のみ」
        if role_idx == 2:
            tts_line = _ensure_period_for_sentence(tts_line, audio_lang)

        out_audio = TEMP / f"{i:02d}.wav"
        speak(audio_lang, spk, tts_line, out_audio, style="neutral")
        audio_parts.append(out_audio)
        tts_lines.append(tts_line)

        # 字幕（音声言語=原文、他言語=翻訳）
        for r, lang in enumerate(subs):
            sub_rows[r].append(line if lang == audio_lang else translate(line, lang))

    # 単純結合 → 整音 → mp3
    new_durs = _concat_with_gaps(audio_parts, gap_ms=GAP_MS, pre_ms=PRE_SIL_MS, min_ms=MIN_UTTER_MS)
    enhance(TEMP/"full_raw.wav", TEMP/"full.wav")
    AudioSegment.from_file(TEMP/"full.wav").export(TEMP/"full.mp3", format="mp3")

    # 背景画像
    bg_png = TEMP / "bg.png"
    first_word = valid_dialogue[0][1] if valid_dialogue else theme
    fetch_bg(first_word, bg_png)

    # lines.json
    lines_data = []
    for i, ((spk, txt), dur) in enumerate(zip(valid_dialogue, new_durs)):
        row = [spk]
        for r in range(len(subs)):
            row.append(sub_rows[r][i])
        row.append(dur)
        lines_data.append(row)
    (TEMP/"lines.json").write_text(json.dumps(lines_data, ensure_ascii=False, indent=2), encoding="utf-8")

    if DEBUG_SCRIPT:
        try:
            (TEMP / "script_raw.txt").write_text("\n".join(plain_lines), encoding="utf-8")
            (TEMP / "script_tts.txt").write_text("\n".join(tts_lines), encoding="utf-8")
            with open(TEMP / "subs_table.tsv", "w", encoding="utf-8") as f:
                header = ["idx", "text"] + [f"sub:{code}" for code in subs]
                f.write("\t".join(header) + "\n")
                for idx in range(len(valid_dialogue)):
                    row = [str(idx+1), valid_dialogue[idx][1]] + [sub_rows[r][idx] for r in range(len(subs))]
                    f.write("\t".join(row) + "\n")
            with open(TEMP / "durations.txt", "w", encoding="utf-8") as f:
                total = 0.0
                for i, d in enumerate(new_durs, 1):
                    total += d
                    f.write(f"{i:02d}\t{d:.3f}s\n")
                f.write(f"TOTAL\t{total:.3f}s\n")
        except Exception as e:
            logging.warning(f"[DEBUG_SCRIPT] write failed: {e}")

    if args.lines_only:
        return

    # サムネ
    thumb = TEMP / "thumbnail.jpg"
    thumb_lang = subs[1] if len(subs) > 1 else audio_lang
    make_thumbnail(theme, thumb_lang, thumb)

    # 動画生成
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_mp4 = OUTPUT / f"{audio_lang}-{'_'.join(subs)}_{stamp}.mp4"
    final_mp4.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "python", str(BASE/"chunk_builder.py"),
        str(TEMP/"lines.json"), str(TEMP/"full.mp3"), str(bg_png),
        "--chunk", str(chunk_size),
        "--rows", str(len(subs)),
        "--out", str(final_mp4),
    ]
    logging.info("🔹 chunk_builder cmd: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)

    if not do_upload:
        return

    # メタ生成＆アップロード
    title = make_title(theme, title_lang, audio_lang_for_label=audio_lang)
    desc  = make_desc(theme, title_lang)
    tags  = make_tags(theme, audio_lang, subs, title_lang)

    upload(video_path=final_mp4, title=title, desc=desc, tags=tags,
           privacy=yt_privacy, account=account, thumbnail=thumb, default_lang=audio_lang)

# ───────────────────────────────────────────────
def run_all(topic, turns, privacy, do_upload, chunk_size):
    for combo in COMBOS:
        audio_lang  = combo["audio"]
        subs        = combo["subs"]
        account     = combo.get("account","default")
        title_lang  = _infer_title_lang(audio_lang, subs, combo)

        logging.info(f"=== Combo: {audio_lang}, subs={subs}, account={account}, title_lang={title_lang}, mode={CONTENT_MODE} ===")

        picked_topic = topic
        if topic.strip().lower() == "auto":
            picked_topic = pick_by_content_type("vocab", audio_lang)
            logging.info(f"[{audio_lang}] picked vocab theme: {picked_topic}")

        run_one(picked_topic, turns, audio_lang, subs, title_lang, privacy, account, do_upload, chunk_size)

# ───────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("topic", help="語彙テーマ。AUTO で自動選択。カンマ/改行区切りなら単語リストとして使用")
    ap.add_argument("--turns", type=int, default=8)  # 互換用
    ap.add_argument("--privacy", default="unlisted", choices=["public","unlisted","private"])
    ap.add_argument("--lines-only", action="store_true")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--chunk", type=int, default=9999, help="Shortsは分割せず1本推奨")
    args = ap.parse_args()

    topic = resolve_topic(args.topic)
    run_all(topic, args.turns, args.privacy, not args.no_upload, args.chunk)