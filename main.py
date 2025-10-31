#!/usr/bin/env python
"""
main.py – VOCAB専用ロング動画（横向き16:9 / ラウンド制 / 日本語TTS最適化）
- 1ラウンド = N単語（単語→単語→例文×N） + そのN語をすべて含む会話
- ラウンドごとに「単語群→会話」を入れて次の単語群へ進む（群ごと対話）
- 例文は常に「1文だけ」。失敗時は最大6回まで再生成、最後はフェールセーフ。
- 翻訳（字幕）は1行化、複文は先頭1文のみ採用。URL/絵文字/余分な空白を除去。
- 単語の翻訳は「例文＋テーマ＋品詞ヒント」で1語に確定（文脈訳）。
- 生成音声は横向きに最適化された本物の 1920x1080 キャンバス上でレンダ（黒帯なし）。
- ★ 重要：ko/ja/zh では英字混入を禁止（英字が出たら再生成 / TTS直前で英字除去）
"""

import argparse, logging, re, json, subprocess, os, sys
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

# オーディオ結合パラメータ
GAP_MS       = int(os.getenv("GAP_MS", "120"))
PRE_SIL_MS   = int(os.getenv("PRE_SIL_MS", "120"))
MIN_UTTER_MS = int(os.getenv("MIN_UTTER_MS", "1000"))

# ★ 日本語だけ個別調整
GAP_MS_JA       = int(os.getenv("GAP_MS_JA", str(GAP_MS)))
PRE_SIL_MS_JA   = int(os.getenv("PRE_SIL_MS_JA", str(PRE_SIL_MS)))
MIN_UTTER_MS_JA = int(os.getenv("MIN_UTTER_MS_JA", "800"))

# 生成温度
EX_TEMP_DEFAULT = float(os.getenv("EX_TEMP", "0.35"))   # 例文
LIST_TEMP       = float(os.getenv("LIST_TEMP", "0.30")) # 語彙リスト

# 語彙・ラウンド
VOCAB_WORDS   = int(os.getenv("VOCAB_WORDS", "2"))      # 1ラウンドの単語数
VOCAB_ROUNDS  = int(os.getenv("VOCAB_ROUNDS", "2"))     # ラウンド数
CONVO_LINES   = int(os.getenv("CONVO_LINES", "2"))      # そのラウンド末の会話行数（偶数推奨）

# 横向き 16:9 レンダ設定（chunk_builder に渡す）
RENDER_SIZE   = os.getenv("RENDER_SIZE", "1920x1080")
RENDER_BG_FIT = os.getenv("RENDER_BG_FIT", "cover")

LANG_NAME = {
    "en": "English", "pt": "Portuguese", "id": "Indonesian",
    "ja": "Japanese","ko": "Korean", "es": "Spanish", "fr": "French",
    "zh": "Chinese",
}

# ====== “出がちの安全すぎる語”を抑制（テーマ無関連化を避ける） ======
BANNED_COMMON_BY_LANG = {
    "ja": {"チェックイン", "チェックアウト", "予約", "領収書", "レシート", "ロビー", "エレベーター", "アップグレード", "客室", "部屋"},
    "ko": {"체크인", "체크아웃", "예약", "영수증", "로비", "엘리베이터", "업그레이드", "객실"},
    "zh": {"办理入住", "退房", "预订", "发票", "大堂", "电梯", "升级", "房间"},
    "es": {"registro", "reserva", "salida", "recibo", "ascensor", "vestíbulo", "mejora"},
    "pt": {"check-in", "reserva", "checkout", "recibo", "elevador", "saguão", "upgrade"},
    "fr": {"enregistrement", "réservation", "départ", "reçu", "ascenseur", "hall", "surclassement"},
    "id": {"check-in", "reservasi", "check-out", "struk", "lift", "lobi", "upgrade"},
    "en": {"check-in", "reservation", "checkout", "receipt", "elevator", "lobby", "upgrade"},
}

def _banned_for(lang_code: str) -> set[str]:
    return set(BANNED_COMMON_BY_LANG.get(lang_code, set()))

JP_CONV_LABEL = {
    "en": "英会話", "ja": "日本語会話", "es": "スペイン語会話",
    "pt": "ポルトガル語会話", "ko": "韓国語会話", "id": "インドネシア語会話",
    "fr": "フランス語会話", "zh": "中国語会話",
}

with open(BASE / "combos.yaml", encoding="utf-8") as f:
    COMBOS = yaml.safe_load(f)["combos"]

# 例文フォールバック統計
FALLBACK_STATS = {"example_attempts": 0, "example_fallbacks": 0}

def reset_temp():
    if TEMP.exists():
        rmtree(TEMP)
    TEMP.mkdir(exist_ok=True)

def sanitize_title(raw: str) -> str:
    """
    タイトルは“切らない”方針：
      - 余計な番号・全角空白などのノイズだけ除去
      - YouTube上限(100文字)は静かにカット（省略記号は付けない）
    """
    title = re.sub(r"^\s*(?:\d+\s*[.)]|[-•・])\s*", "", raw)  # 先頭の番号や記号
    title = re.sub(r"[\s\u3000]+", " ", title).strip()       # 連続スペース正規化
    return title[:100]  # 省略記号は付けない

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
    return arg_topic

# ───────────────────────────────────────────────
# クリーニング・バリデーション
# ───────────────────────────────────────────────
_URL_RE   = re.compile(r"https?://\S+")
_NUM_LEAD = re.compile(r"^\s*\d+[\).:\-]\s*")
_QUOTES   = re.compile(r'^[\"“”\']+|[\"“”\']+$')
_EMOJI_RE = re.compile(r"[\U00010000-\U0010ffff]")
_SENT_END = re.compile(r"[。.!?！？]")

def _normalize_spaces(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip()

def _clean_strict(text: str) -> str:
    t = (text or "").strip()
    t = _URL_RE.sub("", t)
    t = _NUM_LEAD.sub("", t)
    t = _QUOTES.sub("", t)
    t = _EMOJI_RE.sub("", t)
    t = re.sub(r"[\:\-–—]\s*$", "", t)
    return _normalize_spaces(t)

def _is_single_sentence(text: str) -> bool:
    return len(_SENT_END.findall(text or "")) <= 1

def _fits_length(text: str, lang_code: str) -> bool:
    if lang_code in ("ja", "ko", "zh"):
        return len(text or "") <= 30
    return len(re.findall(r"\b\w+\b", text or "")) <= 12

def _ensure_period_for_sentence(txt: str, lang_code: str) -> str:
    t = txt or ""
    return t if re.search(r"[。.!?！？]$", t) else t + ("。" if lang_code == "ja" else ".")

def _clean_sub_line(text: str, lang_code: str) -> str:
    t = _clean_strict(text).replace("\n", " ").strip()
    m = _SENT_END.search(t)
    if m:
        t = t[:m.end()]
    return t

# ───────────────────────────────────────────────
# モノリンガル強制（ko/ja/zhは英字禁止）
# ───────────────────────────────────────────────
_ASCII_LETTERS = re.compile(r"[A-Za-z]")

def _monolingual_ok(text: str, lang_code: str) -> bool:
    """ko/ja/zh は英字を含まないこと。その他は自由（Latin系言語のため）。"""
    if lang_code in ("ko", "ja", "zh"):
        return not _ASCII_LETTERS.search(text or "")
    return True

# TTS直前の非英語アスキー除去（安全側）
def _purge_ascii_for_tts(text: str, lang_code: str) -> str:
    if lang_code in ("ko", "ja", "zh"):
        t = re.sub(r"[A-Za-z]+", "", text or "")
        t = re.sub(r"\s{2,}", " ", t).strip()
        return t or (text or "")
    return text or ""

# 英語以外で紛れ込んだ英単語を弱める軽いフィルタ（既存互換）
_LATIN_WORD_RE = re.compile(r"\b[A-Za-z]{3,}\b")
def _clean_non_english_ascii(text: str, lang_code: str) -> str:
    if lang_code == "en":
        return text
    if lang_code in ("ko", "ja", "zh"):
        return _purge_ascii_for_tts(text, lang_code)
    t = text
    t = _LATIN_WORD_RE.sub("", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t or text

# ───────────────────────────────────────────────
# 日本語TTS最適化
# ───────────────────────────────────────────────
_KANJI_ONLY = re.compile(r"^[一-龥々]+$")
_PARENS_JA  = re.compile(r"\s*[\(\（][^)\）]{1,40}[\)\）]\s*")

def _to_kanji_digits(num_str: str) -> str:
    table = str.maketrans("0123456789", "〇一二三四五六七八九")
    return num_str.translate(table)

def normalize_ja_for_tts(text: str) -> str:
    t = text or ""
    t = re.sub(r"[\(（][^)\）]{1,40}[\)）]", "", t)
    t = t.replace("/", "、").replace("-", "、").replace(":", "、").replace("・ ・", "・")
    t = re.sub(r"\d{1,}", lambda m: _to_kanji_digits(m.group(0)), t)
    t = re.sub(r"([A-Za-z]{2,})", lambda m: "・".join(list(m.group(1).lower())), t)
    t = re.sub(r"[。]{2,}", "。", t)
    t = re.sub(r"[、]{2,}", "、", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    if t and t[-1] not in "。！？!?":
        t += "。"
    return t

# ───────────────────────────────────────────────
# 翻訳強化
# ───────────────────────────────────────────────
_ASCII_ONLY = re.compile(r'^[\x00-\x7F]+$')

def _looks_like_english(s: str) -> bool:
    s = (s or "").strip()
    return bool(_ASCII_ONLY.fullmatch(s)) and bool(re.search(r'[A-Za-z]', s))

def _needs_retranslate(output: str, src_lang: str, target_lang: str, original: str) -> bool:
    if target_lang == src_lang:
        return False
    out = (output or "").strip()
    if not out:
        return True
    if out.lower() == (original or "").strip().lower():
        return True
    if target_lang == "en" and not _looks_like_english(out):
        return True
    return False

def translate_sentence_strict(sentence: str, src_lang: str, target_lang: str) -> str:
    try:
        first = translate(sentence, target_lang)
    except Exception:
        first = ""
    if not _needs_retranslate(first, src_lang, target_lang, sentence):
        return _clean_sub_line(first, target_lang)
    try:
        _ = GPT.chat_completions.create
    except AttributeError:
        pass
    try:
        rsp = GPT.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{
                "role":"user",
                "content":(
                    f"Translate from {LANG_NAME.get(src_lang,'source language')} "
                    f"to {LANG_NAME.get(target_lang,'target language')}.\n"
                    "Return ONLY the translation as a single sentence. "
                    "No explanations, no quotes, no extra symbols.\n\n"
                    f"Text: {sentence}"
                )
            }],
            temperature=0.0, top_p=1.0
        )
        out = (rsp.choices[0].message.content or "").strip()
        out = _clean_sub_line(out, target_lang)
        if out:
            return out
    except Exception:
        pass
    return _clean_sub_line(sentence, target_lang)

# ───────────────────────────────────────────────
# ラングエージルール（厳密モノリンガル）
# ───────────────────────────────────────────────
def _lang_rules(lang_code: str) -> str:
    if lang_code == "ja":
        return (
            "Write entirely in Japanese. "
            "Do not include Latin letters or other languages. "
            "Avoid ASCII symbols such as '/', '-', '→', '()', '[]', '<>', and '|'. "
            "No translation glosses, brackets, or country/language mentions."
        )
    if lang_code == "ko":
        return (
            "Write entirely in Korean (Hangul only). "
            "Do not include Latin letters or other languages. "
            "Avoid ASCII symbols such as '/', '-', '→', '()', '[]', '<>', and '|'. "
            "No translation glosses or stage directions."
        )
    if lang_code == "zh":
        return (
            "Write entirely in Chinese. "
            "Do not include Latin letters or other languages. "
            "Avoid ASCII symbols such as '/', '-', '→', '()', '[]', '<>', and '|'. "
            "No translation glosses or stage directions."
        )
    lang_name = LANG_NAME.get(lang_code, "English")
    return (
        f"Write entirely in {lang_name}. "
        "Do not code-switch or include other writing systems. "
        "Avoid ASCII symbols like '/', '-', '→', '()', '[]', '<>', and '|'. "
        "No translation glosses, brackets, or country/language mentions."
    )

# ───────────────────────────────────────────────
# 日本語 fallback
# ───────────────────────────────────────────────
def _guess_ja_pos(word: str) -> str:
    w = (word or "").strip()
    if not w:
        return "noun"
    if w.endswith(("する", "します", "したい", "した", "しない", "しよう")):
        return "verb"
    if re.search(r"(う|く|ぐ|す|つ|ぬ|む|ぶ|る)$", w):
        return "verb"
    if w.endswith("い"):
        return "iadj"
    if w.endswith(("的", "的な", "風")):
        return "naadj"
    if re.fullmatch(r"[ァ-ヶー]+", w):
        return "noun"
    return "noun"

def _ja_template_fallback(word: str) -> str:
    kind = _guess_ja_pos(word)
    if kind == "verb":
        return f"{word}ところです。"
    if kind == "iadj":
        return f"{word}ですね。"
    if kind == "naadj":
        return f"{word}だね。"
    return f"{word}が必要です。"

# ───────────────────────────────────────────────
# 語彙ユーティリティ
# ───────────────────────────────────────────────
def _example_temp_for(lang_code: str) -> float:
    return 0.20 if lang_code == "ja" else EX_TEMP_DEFAULT

_WIDE_DASH = re.compile(r"[‐-‒–—]")

def _contains_word_relaxed(word: str, cand: str, lang_code: str) -> bool:
    """
    ja/ko/zh は活用・派生・送り仮名差を許容して '含む' とみなす。
    他言語は大文字小文字無視の単純部分一致。
    """
    if not word or not cand:
        return False
    t = _WIDE_DASH.sub("-", cand)

    if lang_code in ("ja", "ko", "zh"):
        w = word.strip()
        # 完全一致/部分一致
        if w in t:
            return True
        # JA: する動詞
        if lang_code == "ja" and w.endswith("する"):
            stem = w[:-2]
            if stem and (stem in t or f"{stem}し" in t):
                return True
        # JA: 漢字2文字以上の連続一致
        kanjis = "".join(ch for ch in w if "\u4e00" <= ch <= "\u9fff" or ch == "々")
        if len(kanjis) >= 2 and kanjis in t:
            return True
        # KO: 하다 の語幹ゆる一致
        if lang_code == "ko" and w.endswith("하다"):
            base = w[:-2]
            if base and base in t:
                return True
        # ZH: 了/著/过 の付随許容
        if lang_code == "zh":
            if w in t or (w + "了") in t or (w + "著") in t or (w + "过") in t:
                return True
        # 空白除去で再チェック
        if w.replace(" ", "") and w.replace(" ", "") in t.replace(" ", ""):
            return True
        return False

    # Latin 系：単純部分一致（大小無視）
    return (word or "").lower() in t.lower()

def _gen_example_sentence(word: str, lang_code: str, context_hint: str = "", difficulty: str | None = None) -> str:
    lang_name = LANG_NAME.get(lang_code, "English")
    ctx = (context_hint or "").strip()
    rules = _lang_rules(lang_code)
    system = {
        "role": "system",
        "content": (
            "You write exactly ONE natural sentence. "
            "No lists, no quotes, no emojis, no URLs. Keep it monolingual."
        ),
    }

    # 難易度指示
    level_line = f" CEFR {difficulty} level の難易度にしてください。" if difficulty else ""

    if lang_code == "ja":
        user = (
            f"{rules} "
            f"単語「{word}」を必ず含めて、日本語で自然な一文をちょうど1つだけ書いてください。"
            "日常の簡単な状況を想定し、助詞の使い方を自然にしてください。"
            "かっこ書きや翻訳注釈は不要です。英字は禁止。"
            "可能であれば見出し語に近い形で使ってください（ただし不自然なら活用して構いません）。"
            f"{level_line}"
        )
        if ctx:
            user += f" シーンの文脈: {ctx}"

    elif lang_code == "ko":
        user = (
            f"{rules} "
            f"다음 단어를 반드시 포함하여 한국어로 자연스러운 문장을 정확히 1개만 쓰세요: {word} "
            "대괄호나 번역 메모 금지. 영문자 사용 금지."
            "가능하면 기본형에 가깝게 쓰되, 부자연스러우면 활용해도 됩니다."
            f"{level_line}"
        )
        if ctx:
            user += f" 장면 힌트: {ctx}"

    elif lang_code == "zh":
        user = (
            f"{rules} "
            f"必须包含该词，并只写一句自然的句子：{word}。"
            "不要使用括号或翻译注释。不要使用拉丁字母。"
            "若可能请使用词典形式，若不自然可以适度变化。"
            f"{level_line}"
        )
        if ctx:
            user += f" 场景提示：{ctx}"

    else:
        user = (
            f"{rules} "
            f"Write exactly ONE short, natural sentence in {lang_name} that uses the word: {word}. "
            "Return ONLY the sentence. Prefer using the dictionary form if natural."
            f"{level_line}"
        )
        if ctx:
            user += f" Scene hint: {ctx}"

    for _ in range(6):
        try:
            rsp = GPT.chat.completions.create(
                model="gpt-4o-mini",
                messages=[system, {"role": "user", "content": user}],
                temperature=_example_temp_for(lang_code),
                top_p=0.9,
            )
            raw = (rsp.choices[0].message.content or "").strip()
        except Exception:
            raw = ""
        cand = _clean_strict(raw)
        FALLBACK_STATS["example_attempts"] += 1
        if not _monolingual_ok(cand, lang_code):
            continue
        valid = bool(cand) and _is_single_sentence(cand) and _fits_length(cand, lang_code)
        try:
            contains_word = _contains_word_relaxed(word, cand, lang_code)
        except Exception:
            contains_word = True
        if valid and contains_word:
            return _ensure_period_for_sentence(cand, lang_code)

    # フェールセーフ（バックアップ例文）
    FALLBACK_STATS["example_fallbacks"] += 1
    if lang_code == "ja":
        return _ja_template_fallback(word)
    elif lang_code == "ko":
        return _ensure_period_for_sentence(f"{word}를 연습해 봅시다", lang_code)
    elif lang_code == "zh":
        return _ensure_period_for_sentence(f"让我们练习{word}", lang_code)
    elif lang_code == "es":
        return _ensure_period_for_sentence(f"Practiquemos {word}", lang_code)
    elif lang_code == "pt":
        return _ensure_period_for_sentence(f"Vamos praticar {word}", lang_code)
    elif lang_code == "fr":
        return _ensure_period_for_sentence(f"Pratiquons {word}", lang_code)
    elif lang_code == "id":
        return _ensure_period_for_sentence(f"Ayo berlatih {word}", lang_code)
    else:
        return _ensure_period_for_sentence(f"Let's practice {word}", lang_code)

def _extract_words(text: str, lang_code: str, n: int, banned: set[str]) -> list[str]:
    """
    厳密パーサ：行頭番号除去/句読点除去/スクリプト規則/重複除去/バンリスト除去。
    Latin系は最初のトークンのみ採用（"credit card" → "credit"）※必要なら hyphen は許可。
    """
    if not text:
        return []
    lines = [ (ln or "").strip() for ln in text.splitlines() ]
    out: list[str] = []
    seen: set[str] = set()
    for ln in lines:
        if not ln:
            continue
        # 先頭番号・接頭句を除去
        ln = re.sub(r"^\s*(?:[-•・]|\d+[\).:]?)\s*", "", ln)
        # 末尾の句読点や装飾を除去
        ln = re.sub(r"[，、。.!?！？…:;]+$", "", ln).strip()
        if not ln:
            continue

        # 言語別スクリプト制約
        if lang_code in ("ja", "ko", "zh"):
            # 英字混入を弾く
            if _ASCII_LETTERS.search(ln):
                continue
            w = ln.replace("　", " ").split()[0]  # 万一スペースがあれば先頭トークン
        else:
            # Latin系は1トークン化（hyphenは許容）
            token = ln.split()[0]
            token = re.sub(r"[^\w\-’']", "", token)
            w = token

        if not w:
            continue

        # 短すぎ/長すぎ/数字のみ を除外
        if len(w) < 2 or len(w) > 24 or re.fullmatch(r"\d+", w):
            continue

        # バンリスト・重複
        key = (w.lower() if lang_code not in ("ja", "ko", "zh") else w)
        if w in banned or key in seen:
            continue

        out.append(w)
        seen.add(key)
        if len(out) >= n:
            break
    return out

def _gen_more_words_excluding(theme_for_prompt: str, lang_code: str, need: int, exclude: list[str], diff_hint: str = "") -> str:
    """
    不足分のみ追加取得。すでに得た語(exclude)とバンリストを明示して生成。
    """
    lang_name = LANG_NAME.get(lang_code, "the target language")
    banned = sorted(_banned_for(lang_code) | set(exclude))
    banned_line = ", ".join(banned[:50])  # 長すぎ回避

    lines = [
        f"List {need} HIGH-FREQUENCY words for: {theme_for_prompt}.",
        f"Language: {lang_name}. Return ONLY one word per line, no numbering.",
        "No explanations. No examples. No punctuation.",
        f"Do NOT include any of these words: {banned_line or '(none)'}."
    ]
    if diff_hint:
        lines.append(f"Approximate CEFR level: {diff_hint}. Prefer common, practical words.")
    if lang_code in ("ko","ja","zh"):
        lines.append("Use ONLY the target script. Do not use Latin letters.")
    return "\n".join(lines)

def _gen_vocab_list(theme: str, lang_code: str, n: int) -> list[str]:
    """
    汎用：強プロンプト → 厳密パース → 不足分だけ再生成 → 最後にフォールバック。
    """
    theme_for_prompt = translate(theme, lang_code) if lang_code != "en" else theme
    lang_name = LANG_NAME.get(lang_code, "the target language")
    banned = _banned_for(lang_code)

    base_prompt = (
        f"List {n} HIGH-FREQUENCY words for the topic: {theme_for_prompt}.\n"
        f"Language: {lang_name}. Return ONLY one word per line, no numbering.\n"
        "No explanations. No examples. No punctuation."
        + ("\nUse ONLY the target script (no Latin letters)." if lang_code in ("ko","ja","zh") else "")
        + ("\nAvoid over-generic hotel words such as check-in / reservation equivalents.")
    )

    content = ""
    for attempt in range(3):
        try:
            rsp = GPT.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": base_prompt}],
                temperature=LIST_TEMP + 0.05 * attempt,
                top_p=0.9,
            )
            content = (rsp.choices[0].message.content or "")
        except Exception:
            content = ""
        if content and content.strip():
            break

    words = _extract_words(content, lang_code, n, banned=banned)

    if len(words) < n:
        need = n - len(words)
        prompt2 = _gen_more_words_excluding(theme_for_prompt, lang_code, need, exclude=words, diff_hint="")
        content2 = ""
        for attempt in range(2):
            try:
                rsp2 = GPT.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role": "user", "content": prompt2}],
                    temperature=LIST_TEMP + 0.1 * attempt,
                    top_p=0.9,
                )
                content2 = (rsp2.choices[0].message.content or "")
            except Exception:
                content2 = ""
            if content2 and content2.strip():
                break
        more = _extract_words(content2, lang_code, need, banned=banned | set(words))
        words.extend([w for w in more if w not in words])

    if len(words) < n:
        FALLBACKS = {
            "ja": ["清掃", "鍵", "フロント", "支払い", "荷物", "朝食", "通路", "館内", "地図", "案内"],
            "ko": ["청소", "열쇠", "프런트", "결제", "짐", "아침식사", "복도", "관내", "지도", "안내"],
            "zh": ["清扫", "钥匙", "前台", "付款", "行李", "早餐", "走廊", "馆内", "地图", "指引"],
            "es": ["llave", "pago", "equipaje", "desayuno", "pasillo", "mapa", "servicio"],
            "pt": ["chave", "pagamento", "bagagem", "café da manhã", "corredor", "mapa", "serviço"],
            "fr": ["clé", "paiement", "bagage", "petit-déjeuner", "couloir", "plan", "service"],
            "id": ["kunci", "pembayaran", "bagasi", "sarapan", "lorong", "peta", "layanan"],
            "en": ["key", "payment", "luggage", "breakfast", "hallway", "map", "service"],
        }
        fb = FALLBACKS.get(lang_code, FALLBACKS["en"])
        for fw in fb:
            if len(words) >= n:
                break
            key = fw.lower() if lang_code not in ("ja", "ko", "zh") else fw
            if fw not in banned and fw not in words:
                words.append(fw)

    return words[:n]

def _gen_vocab_list_from_spec(spec: dict, lang_code: str) -> list[str]:
    """
    spec（theme/context/pos/relation_mode/difficulty/pattern_hint）を尊重して語彙抽出。
    まず spec 準拠で強プロンプト、足りない分は exclude 指定で追加生成、最後にフォールバック。
    """
    n   = int(spec.get("count", VOCAB_WORDS))
    th  = spec.get("theme") or "general vocabulary"
    pos = spec.get("pos") or []
    rel = (spec.get("relation_mode") or "").strip().lower()
    diff = (spec.get("difficulty") or "").strip().upper()
    patt = (spec.get("pattern_hint") or "").strip()
    morph = spec.get("morphology") or []
    theme_for_prompt = translate(th, lang_code) if lang_code != "en" else th
    lang_name = LANG_NAME.get(lang_code, "the target language")
    banned = _banned_for(lang_code)

    lines = [
        f"You are selecting {n} HIGH-FREQUENCY words for the topic: {theme_for_prompt}.",
        f"Language: {lang_name}. Return ONLY one word (or short hyphenated term) per line, no numbering.",
        "No explanations. No examples. No punctuation.",
    ]
    if pos:
        lines.append("Restrict part-of-speech to: " + ", ".join(pos) + ".")
    if rel == "synonym":
        lines.append("Prefer near-synonyms around the central topic.")
    elif rel == "antonym":
        lines.append("Include at most one useful antonym if natural; otherwise skip.")
    elif rel == "collocation":
        lines.append("Prefer common collocations used in everyday speech.")
    elif rel == "pattern":
        lines.append("Prefer short reusable set phrases (but output as single tokens if possible).")
    if patt:
        lines.append(f"Pattern focus hint: {patt}.")
    if morph:
        lines.append("If natural, include related morphological family: " + ", ".join(morph) + ".")
    if diff in ("A1","A2","B1","B2"):
        lines.append(f"Approximate CEFR level: {diff}. Prefer common, practical words at this level.")
    lines.append("Avoid over-generic hotel words (check-in / reservation / receipt / lobby / elevator equivalents).")
    if lang_code in ("ko","ja","zh"):
        lines.append("Use ONLY the target script. Do not use Latin letters.")

    prompt = "\n".join(lines)

    content = ""
    for attempt in range(3):
        try:
            rsp = GPT.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role":"user","content":prompt}],
                temperature=LIST_TEMP + 0.05 * attempt,
                top_p=0.9,
            )
            content = (rsp.choices[0].message.content or "")
        except Exception:
            content = ""
        if content and content.strip():
            break

    words = _extract_words(content, lang_code, n, banned=banned)

    if len(words) < n:
        need = n - len(words)
        prompt2 = _gen_more_words_excluding(theme_for_prompt, lang_code, need, exclude=words, diff_hint=diff if diff in ("A1","A2","B1","B2") else "")
        content2 = ""
        for attempt in range(2):
            try:
                rsp2 = GPT.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=[{"role":"user","content":prompt2}],
                    temperature=LIST_TEMP + 0.1 * attempt,
                    top_p=0.9,
                )
                content2 = (rsp2.choices[0].message.content or "")
            except Exception:
                content2 = ""
            if content2 and content2.strip():
                break
        more = _extract_words(content2, lang_code, need, banned=banned | set(words))
        words.extend([w for w in more if w not in words])

    if len(words) < n:
        return _gen_vocab_list(th, lang_code, n)
    return words[:n]

# ───────────────────────────────────────────────
# 日本語TTS用ふりがな（単語が漢字のみの時）
# ───────────────────────────────────────────────
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
            top_p=1.0,
        )
        yomi = (rsp.choices[0].message.content or "").strip()
        yomi = re.sub(r"[^ぁ-ゖゝゞー]+", "", yomi)
        return yomi[:20]
    except Exception:
        return ""

# ───────────────────────────────────────────────
# 単語の文脈つき1語訳（字幕用）
# ───────────────────────────────────────────────
def translate_word_context(word: str, target_lang: str, src_lang: str, theme: str, example: str, pos_hint: str | None = None) -> str:
    theme = (theme or "").strip()
    example = (example or "").strip()
    pos_line = f"Part of speech hint: {pos_hint}." if pos_hint else ""
    prompt_lines = [
        "You are a precise bilingual lexicon generator.",
        f"Source language code: {src_lang}",
        f"Target language code: {target_lang}",
        "Task: Translate the SINGLE WORD below into exactly ONE natural target-language word that matches the intended meaning in the given context.",
        "Rules:",
        "- Output ONLY one word, no punctuation, no quotes, no explanations.",
        "- The output must be written entirely in the target language.",
        f"- Return ONLY one word in {LANG_NAME.get(target_lang,'target language')} language.",
        "- Choose the sense that fits the context (theme and example sentence).",
        "- Avoid month-name or book-title senses unless clearly indicated by context.",
        pos_line,
        "",
        f"Word: {word}",
        f"Theme: {theme}" if theme else "Theme: (none)",
        f"Example sentence for context: {example}" if example else "Example sentence for context: (none)",
    ]
    try:
        rsp = GPT.chat.completions.create(
            model="gpt-4o-mini",
            messages=[{"role":"user","content":"\n".join(prompt_lines)}],
            temperature=0.0, top_p=1.0
        )
        out = (rsp.choices[0].message.content or "").strip()
        out = re.sub(r"[，、。.!?！？]+$", "", out).strip()
        out = out.split()[0] if out else ""
        return out or word
    except Exception:
        return word

# ───────────────────────────────────────────────
# 冒頭タイトル（Nが1回だけ）
# ───────────────────────────────────────────────
def _build_intro_line(theme: str, audio_lang: str, difficulty: str | None) -> str:
    try:
        theme_local = theme if audio_lang == "en" else translate(theme, audio_lang)
    except Exception:
        theme_local = theme
    if audio_lang == "ja":
        return f"今日のテーマは「{theme_local}」。"
    if audio_lang == "ko":
        return f"오늘의 주제는 {theme_local}입니다."
    if audio_lang == "zh":
        return f"今天的主题是{theme_local}。"
    if audio_lang == "id":
        return f"Topik hari ini: {theme_local}."
    if audio_lang == "pt":
        return f"O tema de hoje é {theme_local}."
    if audio_lang == "es":
        return f"El tema de hoy es {theme_local}."
    if audio_lang == "fr":
        return f"Le thème d’aujourd’hui est {theme_local}."
    return f"Today's theme: {theme_local}."

# ───────────────────────────────────────────────
# そのラウンドの全語を使う短い会話（Alice/Bob）
# ───────────────────────────────────────────────
def _gen_conversation_using_words(words: list[str], lang_code: str, lines_per_round: int = CONVO_LINES) -> list[tuple[str, str]]:
    """
    与えた語をすべて1回以上使う短い会話。
    出力はセリフのみ（話者名なし）を想定し、こちらで Alice/Bob を交互に付与する。
    ko/ja/zh では英字が混ざったら再生成。
    """
    rules = _lang_rules(lang_code)
    lang_name = LANG_NAME.get(lang_code, "English")
    word_list = ", ".join(words)

    system = {
        "role": "system",
        "content": (
            "Write a short, natural two-person dialogue. "
            "Use ALL given words at least once, distributed naturally. "
            "Do NOT add speaker names or bullets. No emojis, no stage directions. "
            "Return EXACTLY the requested number of lines, one sentence per line. "
            "Keep strictly monolingual."
        ),
    }
    user = (
        f"{rules}\n"
        f"Language: {lang_name}\n"
        f"Number of lines: {lines_per_round}\n"
        f"Words to use (all of them, at least once): {word_list}\n"
        "Output: just the lines, no names, no numbering."
        + (" Do not use any Latin letters." if lang_code in ("ko","ja","zh") else "")
    )

    raw = ""
    for _ in range(5):
        try:
            rsp = GPT.chat.completions.create(
                model="gpt-4o-mini",
                messages=[system, {"role": "user", "content": user}],
                temperature=0.5, top_p=0.9,
            )
            raw = (rsp.choices[0].message.content or "").strip()
        except Exception:
            raw = ""
        if raw and _monolingual_ok(raw, lang_code):
            break

    lines = [ln.strip() for ln in (raw or "").splitlines() if ln.strip()]
    if len(lines) < lines_per_round:
        lines += (lines[-1:] * (lines_per_round - len(lines))) if lines else [""] * lines_per_round
    lines = lines[:lines_per_round]

    fixed = []
    for t in lines:
        t = _clean_strict(t)
        if not _monolingual_ok(t, lang_code):
            t = _purge_ascii_for_tts(t, lang_code)
        fixed.append(_ensure_period_for_sentence(t, lang_code))

    out: list[tuple[str, str]] = []
    for i, t in enumerate(fixed):
        spk = "Alice" if i % 2 == 0 else "Bob"
        out.append((spk, t))
    return out

# ───────────────────────────────────────────────
# 重複を避けつつ語彙を集める（ラウンドごとに新規語）
# ───────────────────────────────────────────────
def _pick_unique_words(theme: str, audio_lang: str, n: int, base_spec: dict | None, seen: set[str]) -> list[str]:
    words: list[str] = []
    attempts = 0
    while len(words) < n and attempts < 6:
        attempts += 1
        cand = _gen_vocab_list_from_spec(base_spec, audio_lang) if isinstance(base_spec, dict) else _gen_vocab_list(theme, audio_lang, n*2)
        for w in cand:
            norm = w.strip()
            if not norm:
                continue
            if audio_lang in ("ko","ja","zh") and _ASCII_LETTERS.search(norm):
                continue
            key = norm.lower() if audio_lang not in ("ja","ko","zh") else norm
            if key in seen or key in { (x.lower() if audio_lang not in ("ja","ko","zh") else x) for x in words }:
                continue
            words.append(norm)
            if len(words) >= n:
                break
    # 不足をフォールバックで補完（言語別安全語）
    if len(words) < n:
        FALLBACKS = {
            "ja": ["チェックイン", "予約", "チェックアウト", "領収書", "エレベーター", "ロビー", "アップグレード", "領域", "清掃"],
            "ko": ["체크인", "예약", "체크아웃", "영수증", "엘리베이터", "로비", "업그레이드", "청소", "객실"],
            "zh": ["办理入住", "预订", "退房", "发票", "电梯", "大堂", "升级", "房间"],
            "es": ["registro", "reserva", "salida", "recibo", "ascensor", "vestíbulo", "mejora"],
            "pt": ["check-in", "reserva", "checkout", "recibo", "elevador", "saguão", "upgrade"],
            "fr": ["enregistrement", "réservation", "départ", "reçu", "ascenseur", "hall", "surclassement"],
            "id": ["check-in", "reservasi", "check-out", "struk", "lift", "lobi", "upgrade"],
            "en": ["check-in", "reservation", "checkout", "receipt", "elevator", "lobby", "upgrade"],
        }
        fb = FALLBACKS.get(audio_lang, FALLBACKS["en"])
        for fw in fb:
            if len(words) >= n: break
            key = fw.lower() if audio_lang not in ("ja","ko","zh") else fw
            if key not in seen and key not in { (x.lower() if audio_lang not in ("ja","ko","zh") else x) for x in words }:
                words.append(fw)
    for w in words:
        key = w.lower() if audio_lang not in ("ja","ko","zh") else w
        seen.add(key)
    return words[:n]

# ───────────────────────────────────────────────
# 音声の単純結合（先頭無音/最短尺/行間）
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
# 1コンボ処理（全ラウンド統合）
# ───────────────────────────────────────────────
def run_one(topic, turns, audio_lang, subs, title_lang, yt_privacy, account, do_upload, chunk_size, context_hint="", spec=None):
    reset_temp()

    raw = (topic or "").replace("\r", "\n").strip()
    is_word_list = bool(re.search(r"[,;\n]", raw)) and len([w for w in re.split(r"[\n,;]+", raw) if w.strip()]) >= 2

    # 全体統一の難易度（指定なければA2）
    difficulty_for_all = os.getenv("CEFR_LEVEL", "").strip().upper() or "A2"
    pattern_for_all = None
    pos_for_all = None

    # テーマ/文脈/単語取得の根拠（全体で統一）
    if is_word_list:
        master_theme = "custom list"
        master_context = ""
        base_spec = {"theme": master_theme, "context": master_context, "difficulty": difficulty_for_all, "count": VOCAB_WORDS}
        vocab_seed_list = [w.strip() for w in re.split(r"[\n,;]+", raw) if w.strip()]
    else:
        if isinstance(spec, dict):
            master_theme   = spec.get("theme") or topic
            master_context = (spec.get("context") or context_hint or "")
            pos_for_all    = spec.get("pos") or None
            pattern_for_all= spec.get("pattern_hint") or None
            base_spec = dict(spec)
            base_spec["count"] = VOCAB_WORDS
            base_spec["difficulty"] = (spec.get("difficulty") or difficulty_for_all).upper()
        else:
            master_theme   = topic
            master_context = context_hint or ""
            base_spec = {"theme": master_theme, "context": master_context, "difficulty": difficulty_for_all, "count": VOCAB_WORDS}

    # 冒頭タイトル（Nのみ1回）
    audio_parts, sub_rows = [], [[] for _ in subs]
    plain_lines, tts_lines = [], []

    intro_line = _build_intro_line(master_theme, audio_lang, difficulty_for_all)
    intro_tts  = _ensure_period_for_sentence(intro_line, audio_lang) if audio_lang != "ja" else intro_line
    if audio_lang == "ja":
        intro_tts = normalize_ja_for_tts(intro_tts)
    if audio_lang in ("ko","ja","zh"):
        intro_tts = _purge_ascii_for_tts(intro_tts, audio_lang)
    out_audio = TEMP / f"00_intro.wav"
    speak(audio_lang, "N", intro_tts, out_audio, style=("calm" if audio_lang == "ja" else "neutral"))
    audio_parts.append(out_audio)
    plain_lines.append(intro_line)
    tts_lines.append(intro_tts)
    for r, lang in enumerate(subs):
        if lang == audio_lang:
            sub_rows[r].append(_clean_sub_line(intro_line, lang))
        else:
            try:
                sub_rows[r].append(_clean_sub_line(translate_sentence_strict(intro_line, audio_lang, lang), lang))
            except Exception:
                sub_rows[r].append(_clean_sub_line(intro_line, lang))

    # ラウンドごとの処理
    seen_words: set[str] = set()
    round_count = VOCAB_ROUNDS

    for round_idx in range(1, round_count + 1):
        # 1) このラウンドの単語を決定（重複なし）
        if is_word_list:
            pool = []
            for w in vocab_seed_list:
                key = w.lower() if audio_lang not in ("ja","ko","zh") else w
                if key not in seen_words and not (audio_lang in ("ko","ja","zh") and _ASCII_LETTERS.search(w)):
                    pool.append(w)
            if len(pool) < VOCAB_WORDS:
                # ★ 修正：既存 seen_words を渡して、他ラウンドとの重複を防止
                pool.extend(_pick_unique_words(master_theme, audio_lang, VOCAB_WORDS - len(pool), base_spec, seen_words=seen_words))
            words_round = pool[:VOCAB_WORDS]
            for w in words_round:
                key = w.lower() if audio_lang not in ("ja","ko","zh") else w
                seen_words.add(key)
        else:
            words_round = _pick_unique_words(master_theme, audio_lang, VOCAB_WORDS, base_spec, seen_words)

        # 保険：このラウンド確定語を即時 seen に（デバッグログも）
        for _w in words_round:
            _key = _w.lower() if audio_lang not in ("ja","ko","zh") else _w
            if _key in seen_words:
                logging.info(f"[DEDUP] skip-dup {_w}")
            seen_words.add(_key)

        # 2) 単語→単語→例文（×N語）
        round_examples: list[str] = []
        difficulty_for_this = spec.get("difficulty") if isinstance(spec, dict) else None

        for w in words_round:
            # 難易度を一緒に渡す（_gen_example_sentence 内で CEFRレベル調整）
            if difficulty_for_this:
                ex = _gen_example_sentence(w, audio_lang, master_context, difficulty=difficulty_for_this)
            else:
                ex = _gen_example_sentence(w, audio_lang, master_context)
            round_examples.append(ex)

            # 単語（2回）
            for _rep in (0, 1):
                line = w
                tts_line = line
                if audio_lang == "ja":
                    if _KANJI_ONLY.fullmatch(line):
                        yomi = _kana_reading(line)
                        if yomi:
                            tts_line = yomi
                    base = re.sub(r"[。！？!?]+$", "", tts_line).strip()
                    tts_line = base + ("。" if len(base) >= 2 else "")
                    tts_line = normalize_ja_for_tts(tts_line)
                else:
                    tts_line = _ensure_period_for_sentence(tts_line, audio_lang)
                if audio_lang in ("ko","ja","zh"):
                    tts_line = _purge_ascii_for_tts(tts_line, audio_lang)
                else:
                    tts_line = _clean_non_english_ascii(tts_line, audio_lang)

                out_audio = TEMP / f"{len(audio_parts)+1:02d}.wav"
                style_for_tts = "neutral"
                speak(audio_lang, "N", tts_line, out_audio, style=style_for_tts)
                audio_parts.append(out_audio)
                plain_lines.append(line)
                tts_lines.append(tts_line)

                # 字幕
                for r, lang in enumerate(subs):
                    if lang == audio_lang:
                        sub_rows[r].append(_clean_sub_line(line, lang))
                    else:
                        try:
                            pos_hint = None
                            if isinstance(base_spec, dict) and base_spec.get("pos"):
                                pos_hint = ",".join(base_spec["pos"])
                            elif audio_lang == "ja":
                                _k = _guess_ja_pos(line)
                                pos_map = {"verb":"verb", "iadj":"adjective", "naadj":"adjective", "noun":"noun"}
                                pos_hint = pos_map.get(_k, None)
                            trans = translate_word_context(
                                word=line, target_lang=lang, src_lang=audio_lang,
                                theme=master_theme, example=ex, pos_hint=pos_hint
                            )
                        except Exception:
                            trans = line
                        sub_rows[r].append(_clean_sub_line(trans, lang))

            # 例文（1文）
            line = ex
            if audio_lang == "ja":
                tts_line = _PARENS_JA.sub(" ", line).strip()
                tts_line = _ensure_period_for_sentence(tts_line, audio_lang)
                tts_line = normalize_ja_for_tts(tts_line)
                style_for_tts = "calm"
            else:
                tts_line = _ensure_period_for_sentence(line, audio_lang)
                style_for_tts = "calm"

            if audio_lang in ("ko","ja","zh"):
                tts_line = _purge_ascii_for_tts(tts_line, audio_lang)
            else:
                tts_line = _clean_non_english_ascii(tts_line, audio_lang)

            out_audio = TEMP / f"{len(audio_parts)+1:02d}.wav"
            speak(audio_lang, "N", tts_line, out_audio, style=style_for_tts)
            audio_parts.append(out_audio)
            plain_lines.append(line)
            tts_lines.append(tts_line)
            for r, lang in enumerate(subs):
                if lang == audio_lang:
                    sub_rows[r].append(_clean_sub_line(line, lang))
                else:
                    try:
                        trans = translate_sentence_strict(line, src_lang=audio_lang, target_lang=lang)
                    except Exception:
                        trans = line
                    sub_rows[r].append(_clean_sub_line(trans, lang))

        # 3) まとめ会話（このラウンドの語を全部使う）
        convo = _gen_conversation_using_words(words_round, audio_lang, lines_per_round=CONVO_LINES)
        for spk, line in convo:
            if audio_lang == "ja":
                base = re.sub(r"[。！？!?]+$", "", line).strip()
                tts_line = base + ("。" if base and base[-1] not in "。！？!?" else "")
                tts_line = normalize_ja_for_tts(tts_line)
            else:
                tts_line = _ensure_period_for_sentence(line, audio_lang)
            if audio_lang in ("ko","ja","zh"):
                tts_line = _purge_ascii_for_tts(tts_line, audio_lang)
            else:
                tts_line = _clean_non_english_ascii(tts_line, audio_lang)

            out_audio = TEMP / f"{len(audio_parts)+1:02d}.wav"
            speak(audio_lang, spk, tts_line, out_audio, style=("calm" if audio_lang == "ja" else "neutral"))
            audio_parts.append(out_audio)
            plain_lines.append(line)
            tts_lines.append(tts_line)
            for r, lang in enumerate(subs):
                if lang == audio_lang:
                    sub_rows[r].append(_clean_sub_line(line, lang))
                else:
                    try:
                        trans = translate_sentence_strict(line, src_lang=audio_lang, target_lang=lang)
                    except Exception:
                        trans = line
                    sub_rows[r].append(_clean_sub_line(trans, lang))

    # ── 単純結合 → 整音 → mp3 ─────────────────────────────
    gap_ms = GAP_MS_JA if audio_lang == "ja" else GAP_MS
    pre_ms = PRE_SIL_MS_JA if audio_lang == "ja" else PRE_SIL_MS
    min_ms = MIN_UTTER_MS_JA if audio_lang == "ja" else MIN_UTTER_MS

    new_durs = _concat_with_gaps(audio_parts, gap_ms=gap_ms, pre_ms=pre_ms, min_ms=min_ms)
    enhance(TEMP/"full_raw.wav", TEMP/"full.wav")
    AudioSegment.from_file(TEMP/"full.wav").export(TEMP/"full.mp3", format="mp3")

    # 背景画像（テーマ英訳で検索）
    bg_png = TEMP / "bg.png"
    try:
        theme_en = translate(master_theme, "en")
    except Exception:
        theme_en = master_theme
    fetch_bg(theme_en or "learning", bg_png)

    # lines.json（冒頭タイトル＋全ラウンド）
    lines_data = []
    for i, dur in enumerate(new_durs):
        row = ["N"]
        for r in range(len(subs)):
            row.append(sub_rows[r][i])
        row.append(dur)
        lines_data.append(row)
    (TEMP/"lines.json").write_text(json.dumps(lines_data, ensure_ascii=False, indent=2), encoding="utf-8")

    # デバッグ出力
    try:
        (TEMP / "script_raw.txt").write_text("\n".join(plain_lines), encoding="utf-8")
        (TEMP / "script_tts.txt").write_text("\n".join(tts_lines), encoding="utf-8")
        with open(TEMP / "subs_table.tsv", "w", encoding="utf-8") as f:
            header = ["idx", "text"] + [f"sub:{code}" for code in subs]
            f.write("\t".join(header) + "\n")
            for idx_tbl in range(len(plain_lines)):
                row = [str(idx_tbl+1), _clean_sub_line(plain_lines[idx_tbl], audio_lang)]
                for r in range(len(subs)):
                    row.append(sub_rows[r][idx_tbl])
                f.write("\t".join(row) + "\n")
        with open(TEMP / "durations.txt", "w", encoding="utf-8") as f:
            total = 0.0
            for i, d in enumerate(new_durs, 1):
                total += d
                f.write(f"{i:02d}\t{d:.3f}s\n")
            f.write(f"TOTAL\t{total:.3f}s\n")
        # 例文フォールバック統計
        with open(TEMP / "fallback_stats.json", "w", encoding="utf-8") as f:
            json.dump(FALLBACK_STATS, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logging.warning(f"[DEBUG_SCRIPT] write failed: {e}")

    if args.lines_only:
        return

    # サムネ
    thumb = TEMP / "thumbnail.jpg"
    thumb_lang = subs[1] if len(subs) > 1 else audio_lang
    make_thumbnail(master_theme, thumb_lang, thumb)

    # 動画生成（横向き16:9 / cover）
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    final_mp4 = OUTPUT / f"{audio_lang}-{'_'.join(subs)}_{stamp}.mp4"
    final_mp4.parent.mkdir(parents=True, exist_ok=True)

    cmd = [
        "python", str(BASE/"chunk_builder.py"),
        str(TEMP/"lines.json"), str(TEMP/"full.mp3"), str(bg_png),
        "--chunk", str(chunk_size),
        "--rows", str(len(subs)),
        "--out", str(final_mp4),
        "--center-n",
    ]
    logging.info("🔹 chunk_builder cmd: %s", " ".join(cmd))
    subprocess.run(cmd, check=True)

    if not do_upload:
        return

    # ───────────────────────────── メタ生成＆アップロード ─────────────────────────────
    INCLUDE_VOCAB_IN_TITLE = os.getenv("TITLE_VOCAB", "0") == "1"
    SERIES_LABELS = {
        "en": "Real Practice Series",
        "ja": "Real Practice シリーズ",
        "ko": "Real Practice 시리즈",
        "zh": "Real Practice 系列",
        "es": "Serie Real Practice",
        "pt": "Série Real Practice",
        "fr": "Série Real Practice",
        "id": "Seri Real Practice",
    }

    def make_title(theme, title_lang: str, audio_lang_for_label: str | None = None,
                   pos: list[str] | None = None, difficulty: str | None = None,
                   pattern_hint: str | None = None):
        """
        形式：「<翻訳済みTopic>[ 語彙/Vocabulary] | <シリーズ名> (A2)」
        “見切れ対策の手動クリップ”は廃止。100文字上限だけ sanitize_title() に委ねる。
        """
        level = (difficulty or "A2").upper()
        series_name = SERIES_LABELS.get(title_lang, "Real Practice Series")
        try:
            theme_local = theme if title_lang == "en" else translate(theme, title_lang)
        except Exception:
            theme_local = theme

        if title_lang == "ja":
            topic_part = f"{theme_local} 語彙" if INCLUDE_VOCAB_IN_TITLE else f"{theme_local}"
        else:
            topic_part = f"{theme_local} Vocabulary" if INCLUDE_VOCAB_IN_TITLE else f"{theme_local}"

        title_raw = f"{topic_part} | {series_name} ({level})"
        return sanitize_title(title_raw)

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
            "fr": f"Entraînement rapide du vocabulaire {theme_local}. Répétez à voix haute ! #vocab #apprentissage",
            "zh": f"快速练习 {theme_local} 词汇。跟着音频大声练习！ #vocab #learning",
        }
        return msg.get(title_lang, msg["en"])

    def make_tags(theme, audio_lang, subs, title_lang, difficulty=None, pos=None):
        tags = [
            theme, "vocabulary", "language learning", "speaking practice",
            "listening practice", "subtitles"
        ]
        if difficulty:
            tags.append(f"CEFR {difficulty}")
        if pos:
            for p in pos:
                tags.append(p)
        for code in subs:
            if code in LANG_NAME:
                tags.append(f"{LANG_NAME[code]} subtitles")
        seen_t, out = set(), []
        for t in tags:
            if t not in seen_t:
                seen_t.add(t)
                out.append(t)
        return out[:15]

    pos_for_title = pos_for_all
    difficulty_for_title = difficulty_for_all
    pattern_for_title = pattern_for_all

    title = make_title(
        master_theme, title_lang, audio_lang_for_label=audio_lang,
        pos=pos_for_title, difficulty=difficulty_for_title, pattern_hint=pattern_for_title
    )
    desc  = make_desc(master_theme, title_lang)
    tags  = make_tags(master_theme, audio_lang, subs, title_lang,
                      difficulty=difficulty_for_title, pos=pos_for_title)

    def _is_limit_error(err: Exception) -> bool:
        s = str(err)
        return ("uploadLimitExceeded" in s or "quotaExceeded" in s or
                "The user has exceeded the number of videos they may upload" in s)

    def _is_token_error(err: Exception) -> bool:
        s = str(err).lower()
        return ("token" in s and "expired" in s) or ("invalid_grant" in s) or ("unauthorized_client" in s) or ("invalid_credentials" in s) or ("401" in s)

    def _try_upload_with_fallbacks() -> bool:
        fb = os.getenv("UPLOAD_FALLBACKS", "").strip()
        fallbacks = [x.strip() for x in fb.split(",") if x.strip()]
        tried = []
        for acc in [account] + [a for a in fallbacks if a != account]:
            tried.append(acc)
            try:
                upload(
                    video_path=final_mp4, title=title, desc=desc, tags=tags,
                    privacy=yt_privacy, account=acc, thumbnail=thumb, default_lang=audio_lang
                )
                logging.info(f"[UPLOAD] ✅ success on account='{acc}'")
                return True
            except Exception as e:
                if _is_limit_error(e):
                    logging.warning(f"[UPLOAD] ⚠️ limit reached on account='{acc}' → trying next fallback.")
                    continue
                if _is_token_error(e):
                    logging.error(f"[UPLOAD] ❌ TOKEN ERROR on account='{acc}'")
                    try:
                        (TEMP / "TOKEN_EXPIRED.txt").write_text(
                            f"Token expired or revoked for account='{acc}'.\nDetail:\n{e}",
                            encoding="utf-8",
                        )
                    except Exception:
                        pass
                    raise SystemExit(f"[ABORT] Token expired/revoked for account='{acc}'. Please reauthorize.")
                logging.exception(f"[UPLOAD] unexpected error on account='{acc}'")
                raise
        msg = f"Upload skipped due to per-account limits. tried={tried}"
        logging.warning("[UPLOAD] " + msg)
        try:
            (TEMP / "UPLOAD_SKIPPED.txt").write_text(msg, encoding="utf-8")
        except Exception:
            pass
        return False

    _try_upload_with_fallbacks()

# ───────────────────────────────────────────────
def run_all(topic, turns, privacy, do_upload, chunk_size):
    isolated = os.getenv("ISOLATED_RUN", "0") == "1"
    if isolated:
        for combo in COMBOS:
            audio_lang  = combo["audio"]
            subs        = combo["subs"]
            account     = combo.get("account","default")
            title_lang  = _infer_title_lang(audio_lang, subs, combo)

            if TARGET_ONLY and account != TARGET_ONLY:
                continue

            picked_topic = topic
            context_hint = ""
            spec_for_run = None

            if topic.strip().lower() == "auto":
                try:
                    picked_raw = pick_by_content_type("vocab", audio_lang, return_context=True)
                    if isinstance(picked_raw, dict):
                        picked_topic = picked_raw.get("theme") or "general vocabulary"
                        context_hint = picked_raw.get("context") or ""
                        spec_for_run = dict(picked_raw)
                        spec_for_run["count"] = VOCAB_WORDS
                    elif isinstance(picked_raw, tuple) and len(picked_raw) == 2:
                        picked_topic, context_hint = picked_raw[0], picked_raw[1]
                        spec_for_run = {"theme": picked_topic, "context": context_hint, "count": VOCAB_WORDS}
                    else:
                        picked_topic = str(picked_raw)
                        spec_for_run = {"theme": picked_topic, "context": "", "count": VOCAB_WORDS}
                except TypeError:
                    picked_raw = pick_by_content_type("vocab", audio_lang)
                    picked_topic = picked_raw if isinstance(picked_raw, str) else str(picked_raw)
                    spec_for_run = {"theme": picked_topic, "context": "", "count": VOCAB_WORDS}

            logging.info(f"[ISOLATED] {audio_lang} | subs={subs} | account={account} | theme={picked_topic}")
            run_one(
                picked_topic, turns, audio_lang, subs, title_lang,
                privacy, account, do_upload, chunk_size,
                context_hint=context_hint, spec=spec_for_run
            )
        return

    for combo in COMBOS:
        account = combo.get("account", "default")
        if TARGET_ONLY and account != TARGET_ONLY:
            continue
        cmd = [
            sys.executable, str(BASE / "main.py"),
            topic,
            "--privacy", privacy,
            "--chunk", str(chunk_size),
            "--account", account,
        ]
        if not do_upload:
            cmd.append("--no-upload")
        env = os.environ.copy()
        env["ISOLATED_RUN"] = "1"
        logging.info(f"▶ Spawning isolated run for account={account}: {' '.join(cmd)}")
        subprocess.run(cmd, check=False, env=env)

# ───────────────────────────────────────────────
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("topic", help="語彙テーマ。AUTO で自動選択。カンマ/改行区切りなら単語リストとして使用")
    ap.add_argument("--turns", type=int, default=8)  # 互換用
    ap.add_argument("--privacy", default="unlisted", choices=["public","unlisted","private"])
    ap.add_argument("--lines-only", action="store_true")
    ap.add_argument("--no-upload", action="store_true")
    ap.add_argument("--chunk", type=int, default=9999, help="Long動画は分割せず1本でOK")
    ap.add_argument("--account", type=str, default="", help="この account のみ実行（combos.yaml の account 値に一致）")
    args = ap.parse_args()

    target_cli = (args.account or "").strip()
    target_env = os.getenv("TARGET_ACCOUNT", "").strip()
    TARGET_ONLY = target_cli or target_env

    if TARGET_ONLY:
        selected = [c for c in COMBOS if c.get("account", "default") == TARGET_ONLY]
        if not selected:
            logging.error(f"[ABORT] No combos matched account='{TARGET_ONLY}'. Check combos.yaml.")
            raise SystemExit(2)
        COMBOS[:] = selected
        logging.info(f"[ACCOUNT FILTER] Running only for account='{TARGET_ONLY}' ({len(COMBOS)} combo(s)).")

    topic = resolve_topic(args.topic)
    run_all(topic, args.turns, args.privacy, not args.no_upload, args.chunk)