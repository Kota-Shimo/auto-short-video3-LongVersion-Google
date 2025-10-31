# topic_picker.py – vocab専用テーマを「機能系→シーン系」の二段階でランダム選出
# ・CEFR重み付け（機能/シーンの比率は維持）
# ・言語別の品詞プール（ENV: VOCAB_POS で上書き可）
# ・pos/difficulty/pattern/relation はENV未指定ならランダム
# ・specに functional_theme / scene_theme を含めて返す（return_context=True時）

import os
import random

# ========== 汎用ユーティリティ ==========
def _rng():
    # SystemRandomで暗号学的に十分な乱数
    return random.SystemRandom()

def _parse_csv_env(name: str):
    v = os.getenv(name, "").strip()
    if not v:
        return []
    return [x.strip() for x in v.split(",") if x.strip()]

# ========== テーマ定義 ==========
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

# Functional → Scene の対応（無ければ全体からfallback）
FUNCTIONAL_TO_SCENE_MAP = {
    "greetings & introductions": ["hotel check-in/out", "restaurant ordering", "phone basics"],
    "numbers & prices":          ["shopping basics", "paying & receipts", "transport tickets"],
    "time & dates":              ["appointments", "transport tickets", "airport check-in"],
    "asking & giving directions":["transport tickets", "addresses & contact info", "shopping basics"],
    "polite requests":           ["hotel check-in/out", "restaurant ordering", "pharmacy basics"],
    "offers & suggestions":      ["restaurant ordering", "making plans", "small talk starters"],
    "clarifying & confirming":   ["facilities & problems", "phone basics", "appointments"],
    "describing problems":       ["facilities & problems", "pharmacy basics", "emergencies"],
    "apologizing & excuses":     ["hotel check-in/out", "returns & exchanges", "phone basics"],
    "agreeing & disagreeing":    ["making plans", "restaurant ordering", "small talk starters"],
    "preferences & opinions":    ["restaurant ordering", "shopping basics", "hotel check-in/out"],
    "making plans":              ["appointments", "transport tickets", "restaurant ordering"],
    "past experiences":          ["small talk starters", "hotel check-in/out", "restaurant ordering"],
    "future arrangements":       ["appointments", "airport check-in", "transport tickets"],
    "comparisons":               ["shopping basics", "restaurant ordering", "returns & exchanges"],
    "frequency & habits":        ["small talk starters", "shopping basics", "restaurant ordering"],
    "permission & ability":      ["security & boarding", "hotel check-in/out", "pharmacy basics"],
    "cause & reason":            ["returns & exchanges", "facilities & problems"],
    "condition & advice":        ["pharmacy basics", "facilities & problems", "airport check-in"],
    "small talk starters":       ["hotel check-in/out", "restaurant ordering", "shopping basics"],
}

def _context_for_theme(theme: str) -> str:
    """
    例文生成の安定化用に、テーマに合う超短いシーン文脈を英語で返す。
    （モデル指示用なので英語固定／出力言語は別で指定）
    """
    t = (theme or "").lower()

    # --- Scene 系の文脈 ---
    if "hotel" in t:
        return "A guest talks to the hotel front desk about a simple request."
    if "restaurant" in t:
        return "A customer orders food and asks simple questions at a restaurant."
    if "dietary" in t:
        return "A diner explains a simple dietary need to staff."
    if "shopping" in t:
        return "A customer asks for items and prices in a store."
    if "paying" in t or "receipts" in t:
        return "A customer pays and asks for a receipt at the counter."
    if "returns" in t or "exchanges" in t:
        return "A customer politely asks to return or exchange an item."
    if "airport" in t or "boarding" in t or "security" in t:
        return "A traveler checks in at the airport and asks a brief question."
    if "transport" in t or "tickets" in t:
        return "A traveler buys a ticket and asks for the right line."
    if "facilities" in t or "problems" in t:
        return "A guest reports a small problem and asks for help."
    if "appointments" in t:
        return "A person makes or confirms a simple appointment time."
    if "pharmacy" in t:
        return "A customer asks for basic medicine at a pharmacy."
    if "emergencies" in t:
        return "A person quickly explains a simple urgent situation."
    if "delivery" in t or "online shopping" in t:
        return "A customer checks delivery status or address details."
    if "phone basics" in t or "phone" in t:
        return "A caller asks a simple question on the phone."
    if "addresses" in t or "contact" in t:
        return "Two people exchange addresses or contact information."

    # --- Functional 系の文脈 ---
    if "greetings" in t or "introductions" in t:
        return "Two people meet for the first time and introduce themselves."
    if "numbers" in t or "prices" in t:
        return "A buyer asks the price and understands simple numbers."
    if "time" in t or "dates" in t:
        return "People check the time or set a simple date."
    if "directions" in t:
        return "Someone asks how to get to a nearby place."
    if "polite requests" in t:
        return "Someone politely asks for a small favor or item."
    if "offers" in t or "suggestions" in t:
        return "A person makes a friendly suggestion."
    if "clarifying" in t or "confirming" in t:
        return "People clarify a small detail to avoid confusion."
    if "describing problems" in t:
        return "Someone explains a small issue and asks for help."
    if "apologizing" in t or "excuses" in t:
        return "Someone says sorry briefly for a small mistake."
    if "agreeing" in t or "disagreeing" in t:
        return "People show simple agreement or polite disagreement."
    if "preferences" in t or "opinions" in t:
        return "Someone says what they like or prefer."
    if "making plans" in t:
        return "Two friends plan a simple meetup."
    if "past experiences" in t:
        return "Someone shares a short past experience."
    if "future arrangements" in t:
        return "People schedule something in the near future."
    if "comparisons" in t:
        return "A person compares two options briefly."
    if "frequency" in t or "habits" in t:
        return "Someone talks about how often they do something."
    if "permission" in t or "ability" in t:
        return "A person asks for permission or says they can/can't."
    if "cause" in t or "reason" in t:
        return "Someone gives a short reason for something."
    if "condition" in t or "advice" in t:
        return "A person gives a simple if-advice."
    if "small talk" in t:
        return "Two people start light small talk."

    # デフォルト文脈
    return "A simple everyday situation with polite, practical language."

# ========== 言語別 POS プール ==========
def _pos_pool_for_lang(audio_lang: str):
    """
    言語ごとに使いやすいPOS候補を返す。
    VOCAB_POS 指定があれば最優先（例: "verb,noun"）
    """
    env = _parse_csv_env("VOCAB_POS")
    if env:
        return [env]  # 既に配列想定のためラップして返す（下でchoice）

    code = (audio_lang or "").lower()

    # 共通：句や慣用表現も扱えるようphrase/expression/idiomを用意
    common = [
        ["noun"], ["verb"], ["adjective"], ["adverb"],
        ["phrase"], ["expression"], ["idiom"],
        []  # 指定なし（自由）
    ]

    if code in ("en", "fr", "es", "pt", "id"):
        # 英語系：前置詞も有効
        return common + [["preposition"]]

    if code == "ja":
        # 日本語：助詞/連語/慣用句を表現しやすい粒度
        return common + [["particle"]]  # モデルへのヒントに使うだけ（生成側は自由）

    if code in ("zh", "cmn", "zh-cn", "zh-tw"):
        # 中国語：量詞や成語を意識
        return common + [["classifier"], ["chengyu"]]  # 提示用ラベル

    if code in ("ko", "kr"):
        # 韓国語：助詞（조사）や慣用表現
        return common + [["particle"]]

    # デフォルト
    return common

def _random_pos(audio_lang: str):
    pool = _pos_pool_for_lang(audio_lang)
    return _rng().choice(pool)

# ========== 難易度・パターン・関係モード ==========
def _random_difficulty():
    """
    ENV未指定なら A2/B1/B2 からランダム。
    少し背伸びを混ぜるためA1はデフォ抽選から外す（ENV指定でA1固定可）。
    """
    env = os.getenv("CEFR_LEVEL", "").strip().upper()
    if env in ("A1", "A2", "B1", "B2"):
        return env
    return _rng().choice(["A2", "B1", "B2"])

def _random_pattern_hint():
    env = os.getenv("PATTERN_HINT", "").strip()
    if env:
        return env
    pool = [
        "",  # 指定なし
        "polite_request",      # please / could / would
        "ask_permission",      # may I / can I
        "ask_availability",    # available / time / slot
        "confirm_detail",      # confirm / double-check
        "make_suggestion",     # would like / maybe / how about
        "give_advice",         # should / recommend / better to
        "express_opinion",     # I think / I prefer
        "express_consequence", # therefore / so / accordingly
    ]
    return _rng().choice(pool)

def _relation_mode_of_day(audio_lang: str) -> str:
    env = os.getenv("RELATION_MODE", "").strip().lower()
    if env:
        return env
    # synonym / antonym / collocation / pattern / ""
    return _rng().choice(["synonym", "collocation", "pattern", "antonym", ""])

# ========== 機能→シーン 二段階ピック ==========
def _weighted_functional_pool():
    # CEFRによる“比率”は従来通り維持（テーマの重複による重み）
    level = os.getenv("CEFR_LEVEL", "A2").upper()
    if level == "A1":
        pool = (VOCAB_THEMES_FUNCTIONAL * 7) + (VOCAB_THEMES_SCENE * 3)
    elif level == "B1":
        pool = (VOCAB_THEMES_FUNCTIONAL * 4) + (VOCAB_THEMES_SCENE * 6)
    elif level == "B2":
        pool = (VOCAB_THEMES_FUNCTIONAL * 6) + (VOCAB_THEMES_SCENE * 4)
    else:  # 既定 A2
        pool = (VOCAB_THEMES_FUNCTIONAL * 5) + (VOCAB_THEMES_SCENE * 5)
    # 機能系のみを抽出（比率はそのまま）
    return [t for t in pool if t in VOCAB_THEMES_FUNCTIONAL]

def _pick_functional_then_scene():
    # テーマ固定（デバッグ用）
    override = os.getenv("THEME_OVERRIDE", "").strip()
    if override:
        func = override
    else:
        func = _rng().choice(_weighted_functional_pool())

    # 対応シーンの候補
    scene_candidates = FUNCTIONAL_TO_SCENE_MAP.get(func, [])
    if not scene_candidates:
        scene = _rng().choice(VOCAB_THEMES_SCENE)  # Fallback：全体から
    else:
        scene = _rng().choice(scene_candidates)

    return func, scene

# ========== 返却インタフェース ==========
def _build_spec(functional_theme: str, scene_theme: str, audio_lang: str) -> dict:
    """
    specを構築（ENV最優先・未指定はランダム）
    - pos: 言語別プールから1つ選択（ENV: VOCAB_POS 指定で上書き可）
    - difficulty: A1〜B2（ENVあれば固定）
    - pattern_hint: ランダム（ENVあれば固定）
    """
    return {
        "theme": f"{functional_theme} + {scene_theme}",
        "functional_theme": functional_theme,
        "scene_theme": scene_theme,
        "context": _context_for_theme(scene_theme or functional_theme),
        "count": int(os.getenv("VOCAB_WORDS", "6")),
        "pos": _random_pos(audio_lang),
        "relation_mode": _relation_mode_of_day(audio_lang),
        "difficulty": _random_difficulty(),
        "pattern_hint": _random_pattern_hint(),
        "morphology": _parse_csv_env("MORPHOLOGY"),
    }

def pick_by_content_type(content_type: str, audio_lang: str, return_context: bool = False):
    """
    vocab の場合に、学習本質に沿ったテーマと spec を返す（二段階ピック）。
    - CEFR_LEVEL で機能/シーンの比率は維持しつつ、まずFunctionalを選び、そのFunctionalに合うSceneを選ぶ
    - pos/difficulty/pattern はENV未指定ならランダム
    - THEME_OVERRIDE があればそれを functional として採用
    """
    ct = (content_type or "vocab").lower()
    if ct != "vocab":
        if return_context:
            # 従来互換のフォールバック
            theme = "general vocabulary"
            return {
                "theme": theme,
                "functional_theme": "general functional",
                "scene_theme": "general scene",
                "context": _context_for_theme(theme),
                "count": int(os.getenv("VOCAB_WORDS", "6")),
                "pos": _random_pos(audio_lang),
                "relation_mode": _relation_mode_of_day(audio_lang),
                "difficulty": _random_difficulty(),
                "pattern_hint": _random_pattern_hint(),
                "morphology": [],
            }
        return "general vocabulary"

    functional, scene = _pick_functional_then_scene()

    if not return_context:
        # 従来互換：テーマ名だけ（見栄え調整）
        return f"{functional} + {scene}"

    # 拡張：spec を返す
    return _build_spec(functional, scene, audio_lang)


# ローカルテスト
if __name__ == "__main__":
    print(pick_by_content_type("vocab", "en"))
    print(pick_by_content_type("vocab", "en", return_context=True))
    print(pick_by_content_type("vocab", "ja", return_context=True))
    print(pick_by_content_type("vocab", "ko", return_context=True))