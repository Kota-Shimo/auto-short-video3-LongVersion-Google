# topic_picker.py – vocab専用のテーマを日替わりで返す（CEFR重み付き・学習本質寄り）

import os
import random
import datetime

# ==== vocab 用の最適化テーマ ====
# 機能別（Functional）：伝えたいことが言える中核スキル
VOCAB_THEMES_FUNCTIONAL = [
    "greetings & introductions", "numbers & prices", "time & dates",
    "asking & giving directions", "polite requests", "offers & suggestions",
    "clarifying & confirming", "describing problems", "apologizing & excuses",
    "agreeing & disagreeing", "preferences & opinions", "making plans",
    "past experiences", "future arrangements", "comparisons",
    "frequency & habits", "permission & ability", "cause & reason",
    "condition & advice", "small talk starters",
]

# シーン別（Scene）：高頻度の具体場面で使う語彙
VOCAB_THEMES_SCENE = [
    "shopping basics", "paying & receipts", "returns & exchanges",
    "restaurant ordering", "dietary needs", "transport tickets",
    "airport check-in", "security & boarding", "hotel check-in/out",
    "facilities & problems", "appointments", "pharmacy basics",
    "emergencies", "delivery and online shopping", "phone basics",
    "addresses & contact info",
]

def pick_by_content_type(content_type: str, audio_lang: str) -> str:
    """
    vocab の場合に、学習本質に沿ったテーマを日替わりで1つ返す。
    - CEFR_LEVEL（A1/A2/B1）で機能系とシーン系の比率を変更
    - UTC日付 + audio_lang で毎日安定した結果（言語ごとに独立ローテ）
    それ以外の content_type が来た場合は汎用値を返す（後方互換のため）。
    """
    ct = (content_type or "vocab").lower()
    if ct != "vocab":
        return "general vocabulary"

    level = os.getenv("CEFR_LEVEL", "A2").upper()
    # 日替わり安定シード（言語ごとに独立）
    today_seed = int(datetime.date.today().strftime("%Y%m%d")) + (hash(audio_lang) % 1000)
    rng = random.Random(today_seed)

    # 学習段階に応じた重み付け
    if level == "A1":
        pool = (VOCAB_THEMES_FUNCTIONAL * 7) + (VOCAB_THEMES_SCENE * 3)
    elif level == "B1":
        pool = (VOCAB_THEMES_FUNCTIONAL * 4) + (VOCAB_THEMES_SCENE * 6)
    else:  # 既定 A2
        pool = (VOCAB_THEMES_FUNCTIONAL * 5) + (VOCAB_THEMES_SCENE * 5)

    return rng.choice(pool)

# ローカルテスト用
if __name__ == "__main__":
    print(pick_by_content_type("vocab", "en"))