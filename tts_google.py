# tts_google.py
from google.cloud import texttospeech
import base64, os
from pathlib import Path

VOICE_MAP = {
    "en": "en-US-Neural2-D",
    "ja": "ja-JP-Neural2-C",
    "ko": "ko-KR-Neural2-B",
    "pt": "pt-BR-Neural2-B",
    "id": "id-ID-Neural2-A",
    # 必要に応じて他言語も追加（例：es, fr, zh）
}

def speak(lang: str, speaker: str, text: str, out_path: Path, style="neutral"):
    client = texttospeech.TextToSpeechClient()
    voice_name = VOICE_MAP.get(lang, "en-US-Neural2-D")

    synthesis_input = texttospeech.SynthesisInput(text=text)
    voice = texttospeech.VoiceSelectionParams(
        language_code=voice_name.split("-")[0],
        name=voice_name
    )
    audio_config = texttospeech.AudioConfig(audio_encoding=texttospeech.AudioEncoding.LINEAR16)

    response = client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "wb") as out:
        out.write(response.audio_content)
