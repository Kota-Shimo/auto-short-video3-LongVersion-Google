# tts_google.py – Google Cloud Text-to-Speech（TTS）対応版
# - 既定ボイスは VOICE_MAP を使用（安全フォールバック）
# - ランダム化：環境変数でプールを指定すると、その中から抽選して使用
#     * 言語別: GOOGLE_VOICE_POOL_JA="ja-JP-Neural2-B,ja-JP-Neural2-C"
#     * 全体用: GOOGLE_VOICE_POOL="en-US-Neural2-D,ja-JP-Neural2-C,ko-KR-Neural2-B"
#   優先順位: 言語別プール > 全体プール > VOICE_MAP 既定
# - スタイル（speed/pitch/volume）は軽いプリセット＋ENVで強制上書き可
#     * GOOGLE_TTS_RATE / GOOGLE_TTS_PITCH / GOOGLE_TTS_VOL_DB
from google.cloud import texttospeech
from pathlib import Path
import os, random

# 各言語のデフォルト音声（安全フォールバック）
VOICE_MAP = {
    "en": "en-US-Neural2-D",
    "ja": "ja-JP-Neural2-C",
    "ko": "ko-KR-Neural2-B",
    "pt": "pt-BR-Neural2-B",
    "id": "id-ID-Neural2-A",
    # "es": "es-ES-Neural2-B",
    # "fr": "fr-FR-Neural2-B",
    # "zh": "cmn-CN-Wavenet-A",  # 例：地域差あり。必要に応じて調整
}

# スタイルの軽量プリセット（必要最低限。未指定なら neutral）
_STYLE_PRESET = {
    "neutral":  dict(speaking_rate=1.00, pitch=0.0,  volume_gain_db=0.0),
    "calm":     dict(speaking_rate=0.95, pitch=-1.0, volume_gain_db=0.0),
    "energetic":dict(speaking_rate=1.08, pitch=+2.0, volume_gain_db=0.0),
    "serious":  dict(speaking_rate=0.98, pitch=-0.5, volume_gain_db=0.0),
}

def _parse_pool(env_val: str) -> list[str]:
    if not env_val:
        return []
    return [x.strip() for x in env_val.split(",") if x.strip()]

def _pick_voice_name(lang: str) -> str:
    """
    ランダム抽選のボイス名を決定。
    優先順位：
      1) GOOGLE_VOICE_POOL_{LANG}（例: JA, EN, KO）
      2) GOOGLE_VOICE_POOL（全体共通）
      3) VOICE_MAP の既定
    """
    # 言語別プール（例: GOOGLE_VOICE_POOL_JA）
    key_lang = (lang or "").strip().lower()
    lang_tag = key_lang.upper()
    pool_lang = _parse_pool(os.getenv(f"GOOGLE_VOICE_POOL_{lang_tag}", ""))

    # 全体プール
    pool_all = _parse_pool(os.getenv("GOOGLE_VOICE_POOL", ""))

    # 乱数シード（任意）
    seed = os.getenv("GOOGLE_VOICE_SEED", "").strip()
    if seed:
        try:
            random.seed(int(seed))
        except Exception:
            random.seed(seed)

    if pool_lang:
        return random.choice(pool_lang)
    if pool_all:
        return random.choice(pool_all)
    return VOICE_MAP.get(lang, "en-US-Neural2-D")

def _style_params(style: str) -> dict:
    base = _STYLE_PRESET.get((style or "neutral").lower(), _STYLE_PRESET["neutral"]).copy()
    # ENV で強制上書き（指定があれば使い、なければプリセット値を維持）
    try:
        if os.getenv("GOOGLE_TTS_RATE"):
            base["speaking_rate"] = float(os.getenv("GOOGLE_TTS_RATE"))
        if os.getenv("GOOGLE_TTS_PITCH"):
            base["pitch"] = float(os.getenv("GOOGLE_TTS_PITCH"))
        if os.getenv("GOOGLE_TTS_VOL_DB"):
            base["volume_gain_db"] = float(os.getenv("GOOGLE_TTS_VOL_DB"))
    except ValueError:
        # 数値変換に失敗してもプリセットのまま（安全側）
        pass
    return base

def speak(lang: str, speaker: str, text: str, out_path: Path, style: str = "neutral"):
    """
    Google Cloud Text-to-Speech を使用して音声を生成。
    main.py の tts_openai.speak() と同じ引数構成。
    - lang: "ja" などの2文字コード（VOICE_MAP で既定ボイスを解決）
    - speaker: "N" / "Alice" / "Bob" 等（GoogleTTSでは音色切替には使わないが互換のため保持）
    - text: 合成テキスト
    - out_path: 出力 wav パス
    - style: "neutral" / "calm" / "energetic" / "serious"（軽量プリセット）
    """
    client = texttospeech.TextToSpeechClient()

    # 1) ボイス名を決定（ランダム化 or 既定）
    voice_name = _pick_voice_name(lang)

    # 2) 言語コードは voice_name から推定（"en-US-Neural2-D" → "en-US"）
    parts = voice_name.split("-")
    lang_code = "-".join(parts[:2]) if len(parts) >= 2 else "en-US"

    # 3) 入力テキスト
    synthesis_input = texttospeech.SynthesisInput(text=text)

    # 4) 音声指定
    voice = texttospeech.VoiceSelectionParams(
        language_code=lang_code,
        name=voice_name
    )

    # 5) スタイル適用（ENV で上書き可）＋ サンプルレート固定（既存互換 24kHz）
    st = _style_params(style)
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,  # WAV
        sample_rate_hertz=int(os.getenv("GOOGLE_TTS_SAMPLE_RATE", "24000")),
        speaking_rate=st.get("speaking_rate", 1.0),
        pitch=st.get("pitch", 0.0),
        volume_gain_db=st.get("volume_gain_db", 0.0),
    )

    # 6) 合成
    response = client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config
    )

    # 7) 出力
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(response.audio_content)