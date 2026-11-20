"""Speech provider adapters (server side).

The default is `none`: the browser's built-in Web Speech API handles speech-to-text and
text-to-speech on the client, so no audio leaves the user's machine for the server and
no key is needed. An OpenAI adapter is provided for higher-quality server-side speech;
keys stay on the backend. Raw audio is never persisted.
"""

from __future__ import annotations

from typing import Protocol

import httpx

from backend.app.config import Settings


class VoiceError(Exception):
    def __init__(self, message: str, status: int = 502):
        super().__init__(message)
        self.status = status


class VoiceProvider(Protocol):
    name: str

    def transcribe(self, audio: bytes, mime: str) -> str: ...

    def speech(self, text: str, voice: str | None, speed: float) -> tuple[bytes, str]: ...


class NoVoiceProvider:
    name = "none"

    def transcribe(self, audio: bytes, mime: str) -> str:
        raise VoiceError("Server-side transcription is not configured; the browser's speech recognition is used instead.", 501)

    def speech(self, text: str, voice: str | None, speed: float) -> tuple[bytes, str]:
        raise VoiceError("Server-side speech is not configured; the browser's speech synthesis is used instead.", 501)


class OpenAIVoiceProvider:
    name = "openai"
    MAX_AUDIO_BYTES = 20 * 1024 * 1024

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        if settings.openai_api_key is None:
            raise VoiceError("OPENAI_API_KEY not configured", 501)
        self.base = settings.openai_base_url.rstrip("/")
        self.key = settings.openai_api_key.get_secret_value()
        self.client = httpx.Client(timeout=60, transport=transport)

    def transcribe(self, audio: bytes, mime: str) -> str:
        if len(audio) > self.MAX_AUDIO_BYTES:
            raise VoiceError("Audio too large (max 20 MB)", 413)
        ext = {"audio/webm": "webm", "audio/ogg": "ogg", "audio/wav": "wav", "audio/mpeg": "mp3", "audio/mp4": "m4a"}.get(
            mime.split(";")[0], "webm")
        try:
            r = self.client.post(f"{self.base}/audio/transcriptions", headers={"Authorization": f"Bearer {self.key}"},
                                 files={"file": (f"audio.{ext}", audio, mime)}, data={"model": "whisper-1"})
        except httpx.HTTPError as exc:
            raise VoiceError(f"transcription provider unreachable: {exc}") from exc
        if r.status_code != 200:
            raise VoiceError(f"transcription failed: HTTP {r.status_code}")
        return r.json().get("text", "")

    def speech(self, text: str, voice: str | None, speed: float) -> tuple[bytes, str]:
        try:
            r = self.client.post(f"{self.base}/audio/speech", headers={"Authorization": f"Bearer {self.key}"},
                                 json={"model": "tts-1", "voice": voice or "alloy", "input": text[:4000],
                                       "speed": max(0.5, min(2.0, speed)), "response_format": "mp3"})
        except httpx.HTTPError as exc:
            raise VoiceError(f"speech provider unreachable: {exc}") from exc
        if r.status_code != 200:
            raise VoiceError(f"speech generation failed: HTTP {r.status_code}")
        return r.content, "audio/mpeg"


def build_voice(settings: Settings) -> VoiceProvider:
    if settings.voice_provider == "openai" and settings.openai_api_key is not None:
        return OpenAIVoiceProvider(settings)
    return NoVoiceProvider()
