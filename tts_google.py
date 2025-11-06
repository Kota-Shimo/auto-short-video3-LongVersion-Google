# tts_google.py – Google Cloud Text-to-Speech 対応版
from google.cloud import texttospeech
from pathlib import Path

# 各言語のデフォルト音声をマッピング
VOICE_MAP = {
    "en": "en-US-Neural2-D",
    "ja": "ja-JP-Neural2-C",
    "ko": "ko-KR-Neural2-B",
    "pt": "pt-BR-Neural2-B",
    "id": "id-ID-Neural2-A",
    # 必要に応じて他言語も追加（例：es, fr, zh）
}

def speak(lang: str, speaker: str, text: str, out_path: Path, style: str = "neutral"):
    """
    Google Cloud Text-to-Speech を使用して音声を生成。
    main.py の tts_openai.speak() と同じ引数構成。
    """
    client = texttospeech.TextToSpeechClient()
    voice_name = VOICE_MAP.get(lang, "en-US-Neural2-D")

    # ✅ 言語コードを "en" → "en-US" のように統一
    lang_code = "-".join(voice_name.split("-")[:2])

    synthesis_input = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(
        language_code=lang_code,
        name=voice_name
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,  # WAV形式
        sample_rate_hertz=24000
    )

    response = client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as out:
        out.write(response.audio_content)
