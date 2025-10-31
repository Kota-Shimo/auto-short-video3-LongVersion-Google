# dialogue.py
"""Generate short scripts via GPT-4o.
- Backward compatible: returns List[(speaker, text)] with 'Alice'/'Bob' alternating in dialogue modes.
- Monologue-first in specific modes (wisdom, fact), using speaker 'N' (Narrator).
- Growth structure: Hook → 3 beats → Closing. Short lines. Strictly monolingual & neutral (no language/country mentions).
"""

from typing import List, Tuple
import re
from openai import OpenAI
from config import OPENAI_API_KEY

client = OpenAI(api_key=OPENAI_API_KEY)

# ─────────────────────────────────────────
# モード定義（中立的ガイド）
# ─────────────────────────────────────────
MODE_GUIDE = {
    "dialogue": "Real-life roleplay. Hook, then three short turns, then a concise closing. Keep universal.",
    "howto":    "Actionable three-step guidance. Hook, then step one, step two, and a closing.",
    "listicle": "Three concise points. Hook, point one, point two, point three, closing.",
    "wisdom":   "Motivational. Hook, three short insights, closing.",
    "fact":     "Micro-knowledge. Hook, three small facts/examples, closing.",
    "qa":       "Three-step improvement: problem, better, best. Hook first, then closing.",
}

# ナレーション中心モード
MONOLOGUE_MODES = {"wisdom", "fact"}

# ─────────────────────────────────────────
# 厳密モノリンガル規則（国名/言語名の言及禁止・ASCII記号禁止）
# ─────────────────────────────────────────
def _lang_rules(lang: str) -> str:
    """
    出力言語を厳密に単一化し、他言語/他文字体系・翻訳注釈・学習者呼称を禁止。
    ASCII記号（/, -, →, (), [], <>, | など）も禁止。
    """
    if lang == "ja":
        return (
            "Write entirely in Japanese. "
            "Do not include Latin letters or other languages. "
            "Avoid ASCII symbols such as '/', '-', '→', '()', '[]', '<>', and '|'. "
            "No translation glosses, bracketed meanings, or language/country mentions."
        )
    return (
        f"Write entirely in {lang}. "
        "Do not code-switch or include other writing systems. "
        "Avoid ASCII symbols like '/', '-', '→', '()', '[]', '<>', and '|'. "
        "No translation glosses or language/country mentions."
    )

# 学習イントロ誘導（最初の1行のみ、簡潔）
def _learning_intro_hint(lang: str) -> str:
    if lang == "ja":
        return "Make the first line a clear learning intro like『今日の学び: 〜』『一緒に〜を練習しよう』. Keep it short."
    else:
        return "Make the first line clearly invite learning, e.g., 'Today we learn …' or 'Let’s practice … today.' Keep it short."

# ─────────────────────────────────────────
# TTS安定のための整形（JAは特別処理）
# ─────────────────────────────────────────
def _sanitize_line(lang: str, text: str) -> str:
    txt = (text or "").strip()
    if not txt:
        return ""
    if lang == "ja":
        txt = re.sub(r"[A-Za-z]+", "", txt)          # ローマ字/英単語を除去
        txt = txt.replace("...", "。").replace("…", "。")
        txt = re.sub(r"\s*:\s*", ": ", txt)
        txt = re.sub(r"\s+", " ", txt).strip()
        if txt and txt[-1] not in "。！？!?":
            txt += "。"
        # 行長を過度に伸ばさない（ざっくり）
        if len(txt) > 28:
            txt = txt[:28] + "…"
    else:
        txt = txt.replace("…", "...").strip()
        # 語数をざっくり制限
        words = txt.split()
        if len(words) > 12:
            txt = " ".join(words[:12]) + "..."
    return txt

# ─────────────────────────────────────────
# APIラッパ（安全フォールバック付き）
# ─────────────────────────────────────────
def _complete(messages, temperature=0.6, model="gpt-4o-mini") -> str:
    try:
        rsp = client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=temperature,
            top_p=0.9,
        )
        return (rsp.choices[0].message.content or "").strip()
    except Exception:
        return ""

# ─────────────────────────────────────────
# 本体
# ─────────────────────────────────────────
def make_dialogue(
    topic: str,
    lang: str,
    turns: int = 8,
    seed_phrase: str = "",
    mode: str = "dialogue",
) -> List[Tuple[str, str]]:
    """
    Returns: List[(speaker, text)]
    - dialogue/howto/listicle/qa → Alice/Bob 交互（最大 2*turns 行、空行は採用しない）
    - wisdom/fact → N（Narrator）モノローグ（最大 turns 行、空行は採用しない）
    - Hook → 3ビート → Closing を推奨
    - 厳密モノリンガル・言語名/国名の出力禁止・ASCII記号禁止
    """
    is_monologue = mode in MONOLOGUE_MODES
    topic_hint = f"「{topic}」" if lang == "ja" else topic
    lang_rules = _lang_rules(lang)
    mode_guide = MODE_GUIDE.get(mode, MODE_GUIDE["dialogue"])
    intro_hint = _learning_intro_hint(lang)

    # 行長ヒント
    length_hint = (
        "For alphabetic scripts: <= 12 words per line. "
        "For CJK or similar: keep lines concise (~<=20 characters)."
    )

    # モード別追加規則
    if mode == "dialogue":
        extra_rule = (
            "Include exactly one short learning tip as a plain sentence (no labels). "
            "Avoid slashes, arrows, or letter labels. "
            + intro_hint
        )
    elif mode == "fact":
        extra_rule = (
            "Include one short, surprising point about communication or a tiny example expression. "
            "Use plain sentences only. "
            + intro_hint
        )
    elif mode == "howto":
        extra_rule = (
            "A quick reason, two short steps, then a simple nudge at the end. "
            "No numbered lists or arrows; use plain sentences. "
            + intro_hint
        )
    elif mode == "listicle":
        extra_rule = (
            "Present three parallel points with clear rhythm as plain sentences (no bullets/arrows). "
            + intro_hint
        )
    elif mode == "wisdom":
        extra_rule = (
            "Reflective and encouraging: one key idea, a tiny example, and a gentle takeaway. "
            "Plain sentences only. "
            + intro_hint
        )
    elif mode == "qa":
        extra_rule = (
            "Three-step improvement as plain sentences: problem, better, best. "
            "No arrows or labels. "
            + intro_hint
        )
    else:
        extra_rule = intro_hint

    # プロンプト作成
    if is_monologue:
        user_prompt = f"""
You are a native-level narration writer.

Write a short, natural monologue in {lang} by a narrator 'N'.
Topic: {topic_hint}
Tone ref (seed): "{seed_phrase}" (style hint only; do not repeat literally)
Mode: {mode} ({mode_guide})
{extra_rule}

STRUCTURE:
- Line1 (Learning-intro Hook, 0–2s): clearly invite learning for today's topic
- Lines2–4 (Beats 1–2): add pattern change (numbers, contrast, concrete example)
- Lines5–6 (Beat 3): one visual tip/example
- Final line (Closing, <=8s left): one clear action; subtly echo topic for loop feel

Rules:
1) Produce up to {turns} lines (all spoken by 'N'), one short sentence each.
2) Prefix each line with 'N:'.
3) {lang_rules}
4) {length_hint}
5) Do not mention any language names, nationalities, or countries.
6) Avoid lists, stage directions, emojis.
7) Output ONLY the lines (no explanations).
""".strip()
    else:
        user_prompt = f"""
You are a native-level dialogue writer.

Write a short, natural 2-person conversation in {lang} between Alice and Bob.
Scene topic: {topic_hint}
Tone ref (seed): "{seed_phrase}" (style hint only; do not repeat literally)
Mode: {mode} ({mode_guide})
{extra_rule}

STRUCTURE (alternating lines):
- Line1 (Learning-intro Hook, 0–2s): clearly invite learning for today's topic
- Lines2–4 (Beats 1–2): pattern change (numbers, contrast, example)
- Lines5–6 (Beat 3): one concrete, visual tip/example
- Final line (Closing, <=8s left): one clear action; subtly echo topic for loop feel

Rules:
1) Alternate strictly: Alice:, Bob:, Alice:, Bob: ...
2) Each line = one short sentence; no lists, no stage directions, no emojis.
3) {lang_rules}
4) {length_hint}
5) Do not mention any language names, nationalities, or countries.
6) Avoid repetitive endings; vary rhythm every ~8 seconds.
7) Output ONLY the dialogue lines. No explanations.
""".strip()

    raw = _complete([{"role": "user", "content": user_prompt}], temperature=0.6)
    if not raw:
        # フォールバック（最小限）
        if is_monologue:
            base = [("N", "今日の学びを始めよう。")] if lang == "ja" else [("N", "Let’s start today’s practice.")]
            return [(spk, _sanitize_line(lang, txt)) for spk, txt in base]
        else:
            base = [("Alice", "始めよう。"), ("Bob", "うん。")] if lang == "ja" else [("Alice", "Let’s begin."), ("Bob", "Sure.")]
            return [(spk, _sanitize_line(lang, txt)) for spk, txt in base]

    raw_lines = [l.strip() for l in raw.splitlines() if l.strip()]
    result: List[Tuple[str, str]] = []

    if is_monologue:
        # "N:" 行のみ採用
        for ln in raw_lines[:turns]:
            if ln.startswith("N:"):
                txt = ln.split(":", 1)[1].strip()
                txt = _sanitize_line(lang, txt)
                if txt:
                    result.append(("N", txt))
        if result:
            return result
        # フォールバック（整形後1行）
        base = "今日の学びを始めよう。" if lang == "ja" else "Let’s start today’s practice."
        return [("N", _sanitize_line(lang, base))]

    # 会話モード："Alice:" / "Bob:" のみ採用
    for ln in raw_lines[: turns * 2]:
        if ln.startswith("Alice:") or ln.startswith("Bob:"):
            spk, txt = ln.split(":", 1)
            txt = _sanitize_line(lang, txt)
            if txt:
                result.append((spk.strip(), txt))

    # 交互が崩れた場合の簡易補正（奇数なら末尾を落とす）
    if len(result) % 2 == 1:
        result = result[:-1]

    if result:
        return result

    # 最終フォールバック（2行）
    if lang == "ja":
        fb = [("Alice", "今日の学びを始めよう。"), ("Bob", "いいね。")]
    else:
        fb = [("Alice", "Let’s begin today’s practice."), ("Bob", "Sounds good.")]
    return [(spk, _sanitize_line(lang, txt)) for spk, txt in fb]