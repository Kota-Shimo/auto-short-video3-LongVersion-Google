# topic_picker.py – VOCAB専用：語彙カテゴリ（試験・コア語彙・日常会話など）＋難易度ベース
import os
import random
from typing import List, Tuple, Dict

rng = random.SystemRandom()

# ========== 既存定義（例文側・後方互換用に残す） ==========
# 機能（Functional）＝「何をしたいか」の核
FUNCTIONALS = [
    "greetings & introductions",
    "numbers & prices",
    "time & dates",
    "asking & giving directions",
    "polite requests",
    "offers & suggestions",
    "clarifying & confirming",
    "describing problems",
    "apologizing & excuses",
    "agreeing & disagreeing",
    "preferences & opinions",
    "making plans",
    "past experiences",
    "future arrangements",
    "comparisons",
    "frequency & habits",
    "permission & ability",
    "cause & reason",
    "condition & advice",
    "small talk starters",
]

# シーン（Scene）＝「どこで使うか」
SCENES_BASE = [
    "shopping basics",
    "paying & receipts",
    "returns & exchanges",
    "restaurant ordering",
    "dietary needs",
    "transport tickets",
    "airport check-in",
    "security & boarding",
    "hotel check-in/out",
    "facilities & problems",
    "appointments",
    "pharmacy basics",
    "emergencies",
    "delivery and online shopping",
    "phone basics",
    "addresses & contact info",
]

# Functional → 相性の良い Scene 候補（なければ SCENES_BASE を使う）
SCENES_BY_FUNCTIONAL: Dict[str, List[str]] = {
    "greetings & introductions": ["hotel check-in/out", "small talk at lobby", "phone basics", "restaurant ordering"],
    "numbers & prices": ["shopping basics", "paying & receipts", "transport tickets"],
    "time & dates": ["appointments", "transport tickets", "restaurant ordering"],
    "asking & giving directions": ["transport tickets", "airport check-in", "hotel check-in/out"],
    "polite requests": ["restaurant ordering", "hotel check-in/out", "facilities & problems"],
    "offers & suggestions": ["restaurant ordering", "making plans in lobby", "phone basics"],
    "clarifying & confirming": ["paying & receipts", "appointments", "security & boarding"],
    "describing problems": ["facilities & problems", "pharmacy basics", "returns & exchanges"],
    "apologizing & excuses": ["appointments", "restaurant ordering", "transport tickets"],
    "agreeing & disagreeing": ["making plans in lobby", "restaurant ordering"],
    "preferences & opinions": ["restaurant ordering", "shopping basics"],
    "making plans": ["appointments", "restaurant ordering", "phone basics"],
    "past experiences": ["small talk at lobby", "restaurant ordering"],
    "future arrangements": ["appointments", "transport tickets"],
    "comparisons": ["shopping basics", "restaurant ordering"],
    "frequency & habits": ["small talk at lobby", "pharmacy basics"],
    "permission & ability": ["security & boarding", "hotel check-in/out"],
    "cause & reason": ["returns & exchanges", "facilities & problems"],
    "condition & advice": ["pharmacy basics", "emergencies", "dietary needs"],
    "small talk starters": ["small talk at lobby", "restaurant ordering", "phone basics"],
}

# テーマ文脈（英語）…モデルへの指示用（出力言語とは別）
def _context_for_theme(functional: str, scene: str) -> str:
    f = (functional or "").lower()
    s = (scene or "").lower()

    # scene 優先の短文脈
    if "hotel" in s:
        return "A guest talks to the hotel front desk about a simple request."
    if "restaurant" in s:
        return "A customer orders food and asks a brief question politely."
    if "shopping" in s:
        return "A customer asks for items and prices in a store."
    if "paying" in s or "receipt" in s:
        return "A customer pays and asks for a receipt at the counter."
    if "return" in s or "exchange" in s:
        return "A customer politely asks to return or exchange an item."
    if "airport" in s or "boarding" in s or "security" in s:
        return "A traveler checks in at the airport and asks a simple question."
    if "transport" in s or "ticket" in s:
        return "A traveler buys a ticket and checks a simple detail."
    if "facility" in s or "problem" in s:
        return "A guest reports a small problem and asks for help."
    if "appointment" in s:
        return "A person makes or confirms a simple appointment time."
    if "pharmacy" in s:
        return "A customer asks for basic medicine politely."
    if "emergenc" in s:
        return "A person quickly explains a simple urgent situation."
    if "delivery" in s or "online" in s:
        return "A customer checks delivery status or address details."
    if "phone" in s:
        return "A caller asks a simple question on the phone."
    if "address" in s or "contact" in s:
        return "Two people exchange addresses or contact information."

    # functional による汎用 fallback
    if "greeting" in f or "introductions" in f:
        return "Two people meet for the first time and introduce themselves."
    if "number" in f or "price" in f:
        return "A buyer asks the price and understands simple numbers."
    if "time" in f or "date" in f:
        return "People check the time or set a simple date."
    if "direction" in f:
        return "Someone asks how to get to a nearby place."
    if "polite request" in f:
        return "Someone politely asks for a small favor or item."
    if "offer" in f or "suggestion" in f:
        return "A person makes a friendly suggestion."
    if "clarifying" in f or "confirm" in f:
        return "People clarify a small detail to avoid confusion."
    if "problem" in f:
        return "Someone explains a small issue and asks for help."
    if "apolog" in f:
        return "Someone says sorry briefly for a small mistake."
    if "agree" in f or "disagree" in f:
        return "People show simple agreement or polite disagreement."
    if "preference" in f or "opinion" in f:
        return "Someone says what they like or prefer."
    if "making plan" in f:
        return "Two friends plan a simple meetup."
    if "past" in f:
        return "Someone shares a short past experience."
    if "future" in f:
        return "People schedule something in the near future."
    if "comparison" in f:
        return "A person compares two options briefly."
    if "frequency" in f or "habit" in f:
        return "Someone talks about how often they do something."
    if "permission" in f or "ability" in f:
        return "A person asks for permission or says they can or cannot."
    if "cause" in f or "reason" in f:
        return "Someone gives a short reason for something."
    if "condition" in f or "advice" in f:
        return "A person gives a simple if-advice."
    if "small talk" in f:
        return "Two people start light small talk."
    return "A simple everyday situation with polite, practical language."

# pattern 候補と、Functional による重み（難易度も後で掛ける）
PATTERN_CANDIDATES = [
    "polite_request", "ask_permission", "ask_availability",
    "confirm_detail", "make_suggestion", "give_advice",
    "express_opinion", "express_consequence", ""
]

PATTERN_WEIGHTS_BY_FUNCTIONAL: Dict[str, Dict[str, int]] = {
    # 例：polite requests では polite_request/ask_permission/confirm_detail を重め
    "polite requests": {
        "polite_request": 6, "ask_permission": 4, "confirm_detail": 3, "make_suggestion": 2
    },
    "asking & giving directions": {
        "ask_availability": 4, "confirm_detail": 4, "give_advice": 2, "polite_request": 2
    },
    "making plans": {
        "make_suggestion": 6, "ask_availability": 3, "confirm_detail": 3, "express_consequence": 2
    },
    "describing problems": {
        "confirm_detail": 5, "polite_request": 3, "give_advice": 3, "express_consequence": 2
    },
    "returns & exchanges": {  # scene 側名でも拾えるように後でマージする
        "confirm_detail": 5, "express_reason": 3 if "express_reason" in PATTERN_CANDIDATES else 0,
        "polite_request": 4
    },
}

# 難易度ごとの functional・scene の重み（相対値）
# A1/A2 は基礎機能・基礎シーンを厚め、B1/B2 は問題解決・議論系を厚め
FUNCTIONAL_WEIGHTS_BY_LEVEL: Dict[str, Dict[str, int]] = {
    "A1": {
        "greetings & introductions": 8, "numbers & prices": 7, "time & dates": 6,
        "polite requests": 5, "asking & giving directions": 5,
        "making plans": 4, "preferences & opinions": 3, "small talk starters": 3,
    },
    "A2": {
        "greetings & introductions": 6, "numbers & prices": 5, "time & dates": 5,
        "polite requests": 6, "asking & giving directions": 5,
        "making plans": 5, "clarifying & confirming": 4,
        "preferences & opinions": 4, "small talk starters": 4,
        "describing problems": 3, "permission & ability": 3,
    },
    "B1": {
        "clarifying & confirming": 6, "describing problems": 6,
        "condition & advice": 5, "cause & reason": 5,
        "agreeing & disagreeing": 4, "comparisons": 4,
        "past experiences": 3, "future arrangements": 3,
        "polite requests": 3, "making plans": 3,
    },
    "B2": {
        "agreeing & disagreeing": 6, "preferences & opinions": 6,
        "cause & reason": 5, "condition & advice": 5,
        "comparisons": 4, "clarifying & confirming": 4,
        "describing problems": 4,
    },
}

SCENE_WEIGHTS_BY_LEVEL: Dict[str, Dict[str, int]] = {
    "A1": {
        "shopping basics": 8, "restaurant ordering": 7, "paying & receipts": 6,
        "phone basics": 5, "addresses & contact info": 5, "transport tickets": 4,
        "hotel check-in/out": 4,
    },
    "A2": {
        "restaurant ordering": 7, "shopping basics": 6, "paying & receipts": 6,
        "appointments": 5, "transport tickets": 5, "hotel check-in/out": 5,
        "facilities & problems": 4, "pharmacy basics": 4,
    },
    "B1": {
        "facilities & problems": 6, "returns & exchanges": 6,
        "appointments": 5, "security & boarding": 5,
        "delivery and online shopping": 4, "pharmacy basics": 4,
    },
    "B2": {
        "emergencies": 6, "security & boarding": 5,
        "returns & exchanges": 5, "facilities & problems": 5,
        "delivery and online shopping": 4,
    },
}

# ========== 語彙カテゴリ（単語用）定義 ==========
# 単語は「functional/scene」ではなく、このカテゴリ＋CEFRで選ぶ
VOCAB_SOURCES = {
    "exam_core": "exam core vocabulary",             # 試験頻出・アカデミックより
    "core_general": "core general vocabulary",       # NGSL 等のコア語彙
    "daily_conversation": "everyday spoken vocabulary",  # 日常会話でよく使う
    "survival_travel": "travel & survival vocabulary",   # 旅行・生活サバイバル
    "idioms_phrasal": "idioms & phrasal verbs",      # 熟語・句動詞系
}

def _env_level() -> str:
    v = os.getenv("CEFR_LEVEL", "").strip().upper()
    # ENV が有効ならそれ、無ければ A2/B1/B2 からランダム（= 既存互換）
    return v if v in ("A1", "A2", "B1", "B2") else rng.choice(["A2", "B1", "B2"])

def _choose_weighted(items: List[Tuple[str, int]]) -> str:
    pool, weights = zip(*[(k, max(0, w)) for k, w in items if w > 0])
    return rng.choices(pool, weights=weights, k=1)[0]

def _weights_from_dict(keys: List[str], table: Dict[str, int]) -> List[Tuple[str, int]]:
    return [(k, table.get(k, 1)) for k in keys]

def _pick_functional() -> str:
    # いまは単語では使わないが、後方互換用に残す
    override = os.getenv("FUNCTIONAL_OVERRIDE", "").strip()
    if override:
        return override
    level = _env_level()
    weights = FUNCTIONAL_WEIGHTS_BY_LEVEL.get(level, {})
    items = _weights_from_dict(FUNCTIONALS, weights)
    return _choose_weighted(items)

def _pick_scene(functional: str) -> str:
    # いまは単語では使わないが、後方互換用に残す
    override = os.getenv("SCENE_OVERRIDE", "").strip()
    if override:
        return override

    level = _env_level()
    base = SCENES_BY_FUNCTIONAL.get(functional, SCENES_BASE)

    # level 重み
    level_weights = SCENE_WEIGHTS_BY_LEVEL.get(level, {})
    items = _weights_from_dict(base, level_weights)

    # base 内に weight 定義が薄い場合に最低限の重み確保
    if not any(w > 1 for _, w in items):
        items = [(s, 2) for s in base]  # 均等だが少し厚め

    return _choose_weighted(items)

def _pick_pattern(functional: str, scene: str) -> str:
    # 例文用の pattern。単語では使わないがキーだけ残す。
    override = os.getenv("PATTERN_HINT", "").strip()
    if override:
        return override

    # functional での重み
    f_weights = PATTERN_WEIGHTS_BY_FUNCTIONAL.get(functional, {})

    # scene 名が PATTERN_WEIGHTS に居るケースも少しマージ（任意）
    s_weights = PATTERN_WEIGHTS_BY_FUNCTIONAL.get(scene, {})

    # level による傾向（A1/A2は polite/confirm 系、B1/B2 は advice/opinion/consequence やや厚め）
    level = _env_level()
    level_bias = {
        "A1": {"polite_request": 3, "ask_permission": 2, "confirm_detail": 2},
        "A2": {"polite_request": 2, "confirm_detail": 2, "ask_availability": 2},
        "B1": {"give_advice": 3, "make_suggestion": 2, "express_opinion": 2},
        "B2": {"express_opinion": 3, "express_consequence": 2, "give_advice": 2},
    }.get(level, {})

    merged: Dict[str, int] = {k: 1 for k in PATTERN_CANDIDATES}
    for k, w in f_weights.items():
        merged[k] = merged.get(k, 1) + w
    for k, w in s_weights.items():
        merged[k] = merged.get(k, 1) + w
    for k, w in level_bias.items():
        merged[k] = merged.get(k, 1) + w

    items = [(k, merged.get(k, 1)) for k in PATTERN_CANDIDATES]
    return _choose_weighted(items)

def _random_pos_from_env_or_default():
    v = os.getenv("VOCAB_POS", "").strip()
    if v:
        return [x.strip() for x in v.split(",") if x.strip()]
    # POS は main.py 側で言語別に最終決定してもOK。ここでは未指定 or ランダム軽量にしておく
    return []  # ここは空（＝未指定）にして、main.py 内のロジックに委ねる運用が安全

def _random_difficulty():
    # 常に _env_level() と同じロジックを使い、ズレを無くす
    return _env_level()

def _parse_csv_env(name: str):
    v = os.getenv(name, "").strip()
    if not v:
        return []
    return [x.strip() for x in v.split(",") if x.strip()]

# ─────────────────────────────────────────
# 言語別の難易度ラベル（試験名）を付与
# ─────────────────────────────────────────
def _difficulty_metadata(audio_lang: str, cefr: str) -> Dict[str, str]:
    cefr = (cefr or "").upper()
    base = f"CEFR {cefr}"
    exam = ""
    exam_level = ""
    extra = ""

    if audio_lang == "ja":
        exam = "JLPT"
        mapping = {"A1": "N5", "A2": "N4", "B1": "N3", "B2": "N2"}
        exam_level = mapping.get(cefr, "")
        extra = f"{exam} {exam_level}" if exam_level else ""
    elif audio_lang == "ko":
        exam = "TOPIK"
        mapping = {"A1": "1", "A2": "2", "B1": "3", "B2": "4"}
        exam_level = mapping.get(cefr, "")
        extra = f"{exam} {exam_level}" if exam_level else ""
    elif audio_lang == "zh":
        exam = "HSK"
        mapping = {"A1": "1", "A2": "2", "B1": "3", "B2": "4"}
        exam_level = mapping.get(cefr, "")
        extra = f"{exam} {exam_level}" if exam_level else ""
    elif audio_lang == "es":
        exam = "DELE"
        exam_level = cefr
        extra = f"{exam} {exam_level}"
    elif audio_lang == "fr":
        exam = "DELF"
        exam_level = cefr
        extra = f"{exam} {exam_level}"
    else:
        # en / pt / id などは CEFR 表示のみ（安全）
        extra = ""

    label = base + (f" / {extra}" if extra else "")
    return {
        "label": label,
        "exam": exam,
        "exam_level": exam_level,
    }

# ─────────────────────────────────────────
# 単語専用：語彙カテゴリ＋難易度ベースの spec を組み立てる
# ─────────────────────────────────────────
def _pick_lex_source(audio_lang: str) -> str:
    """
    単語用の語彙カテゴリを選ぶ。
    ENV:
      - LEX_SOURCE=exam_core/core_general/... で強制指定可能。
    """
    override = os.getenv("LEX_SOURCE", "").strip()
    if override and override in VOCAB_SOURCES:
        return override

    # デフォルトの重み（長尺向けにバランス良く）
    base_weights = {
        "exam_core": 3,
        "core_general": 4,
        "daily_conversation": 3,
        "survival_travel": 2,
        "idioms_phrasal": 2,
    }

    # 言語別に少しだけ傾向を変えたい場合はここで調整
    lang = (audio_lang or "").lower()
    if lang == "ja":
        # 日本語：JLPT＋日常会話を少し厚めに
        base_weights["exam_core"] += 1
        base_weights["daily_conversation"] += 1
    elif lang == "ko":
        base_weights["exam_core"] += 1
    elif lang == "es":
        base_weights["daily_conversation"] += 1

    items = list(base_weights.items())
    return _choose_weighted(items)

def _build_vocab_spec(audio_lang: str) -> dict:
    """
    単語（vocab）専用 spec。
    functional / scene は使わず、
    - lex_source（語彙カテゴリ）
    - difficulty（CEFR / 試験ラベル）
    だけで単語を選ぶ。
    """
    cefr = _random_difficulty()
    meta = _difficulty_metadata(audio_lang, cefr)
    lex_source = _pick_lex_source(audio_lang)
    lex_label = VOCAB_SOURCES.get(lex_source, lex_source)

    spec = {
        # vocab では「テーマ文脈」は使わないので空にしておく
        "theme": "",
        "context": "",
        "count": int(os.getenv("VOCAB_WORDS", "6")),
        "pos": _random_pos_from_env_or_default(),
        "relation_mode": os.getenv("RELATION_MODE", "").strip().lower(),
        "difficulty": cefr,                     # CEFR A1/A2/B1/B2
        "difficulty_label": meta["label"],      # 例: "CEFR A2 / JLPT N4"
        "exam": meta["exam"],                   # 例: "JLPT"
        "exam_level": meta["exam_level"],       # 例: "N4"
        # vocab では pattern は使わないので空
        "pattern_hint": "",
        "morphology": _parse_csv_env("MORPHOLOGY"),
        # 新規：語彙カテゴリ
        "lex_source": lex_source,               # 例: "exam_core"
        "lex_source_label": lex_label,          # 例: "exam core vocabulary"
    }
    return spec

# 既存シグネチャを壊さないラッパー（外部から直接呼ばれていても安全）
def _build_spec(functional: str, scene: str, audio_lang: str) -> dict:
    """
    後方互換用：以前は functional/scene ベースだったが、
    いまは vocab 用の _build_vocab_spec に委譲する。
    functional/scene は無視される。
    """
    return _build_vocab_spec(audio_lang)

# ========== 外部API ==========
def pick_by_content_type(content_type: str, audio_lang: str, return_context: bool = False):
    """
    vocab の場合：
      - functional / scene は使わず、
        語彙カテゴリ（lex_source）＋難易度で単語セットを決定する。
    ENV:
      - CEFR_LEVEL=A1/A2/B1/B2 で難易度固定
      - LEX_SOURCE=exam_core/core_general/... で語彙カテゴリ固定
      - THEME_OVERRIDE: return_context=False のときだけ、タイトル文字列を上書き
    """
    ct = (content_type or "vocab").lower()

    # ── vocab 専用ロジック ─────────────────────
    if ct == "vocab":
        # タイトル文字列だけ欲しいときの互換
        theme_override = os.getenv("THEME_OVERRIDE", "").strip()
        if theme_override and not return_context:
            return theme_override

        spec = _build_vocab_spec(audio_lang)

        if not return_context:
            # サムネ用など：語彙カテゴリラベルをそのまま返す
            return spec.get("lex_source_label") or "general vocabulary"

        return spec

    # ── 非 vocab（後方互換の簡易実装）─────────────────
    if return_context:
        theme = "general vocabulary"
        diff = _random_difficulty()
        return {
            "theme": theme,
            "context": "A simple everyday situation with polite, practical language.",
            "count": int(os.getenv("VOCAB_WORDS", "6")),
            "pos": [],
            "relation_mode": "",
            "difficulty": diff,
            "difficulty_label": f"CEFR {diff}",
            "exam": "",
            "exam_level": "",
            "pattern_hint": "",
            "morphology": [],
        }
    return "general vocabulary"

# ローカルテスト
if __name__ == "__main__":
    print(pick_by_content_type("vocab", "en"))
    print(pick_by_content_type("vocab", "en", return_context=True))