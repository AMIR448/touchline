"""The commentator's voice: verified commentary lines spoken by Azure AI Speech.

Only lines the crew already wrote and fact-checked are ever spoken (the server looks them up by
match, moment and viewer profile), so the endpoint can't be used to read out arbitrary text.

- One native HD voice per language. Dragon HD voices pick up the emotion of the text on their
  own, so a goal sounds like a goal without hand-tuned styles.
- Same keyless auth as the agents (az login locally, managed identity in Azure), against the
  Foundry resource's own Speech endpoint: no separate Speech resource, no keys.
- Audio is cached in memory and on disk: a line is synthesized once, however many fans hear it.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from pathlib import Path
from urllib.parse import urlparse
from xml.sax.saxutils import escape

import httpx

VOICES = {  # language -> (locale, voice)
    "en": ("en-GB", "en-GB-Ryan:DragonHDLatestNeural"),
    "es": ("es-ES", "es-ES-Tristan:DragonHDLatestNeural"),
    "fr": ("fr-FR", "fr-FR-Remy:DragonHDLatestNeural"),
    "pt": ("pt-BR", "pt-BR-Macerio:DragonHDLatestNeural"),
}
SCOPE = "https://cognitiveservices.azure.com/.default"
MAX_CHARS = 400


def speech_endpoint() -> str | None:
    """SPEECH_ENDPOINT, or the Foundry resource's own endpoint derived from FOUNDRY_PROJECT_ENDPOINT."""
    if os.getenv("SPEECH_ENDPOINT"):
        return os.environ["SPEECH_ENDPOINT"].rstrip("/")
    project = os.getenv("FOUNDRY_PROJECT_ENDPOINT")
    if not project:
        return None
    resource = urlparse(project).hostname.split(".")[0]  # touchline-ai-xxxx.services.ai.azure.com
    return f"https://{resource}.cognitiveservices.azure.com"


def ssml(text: str, language: str, excited: bool = False) -> str:
    locale, voice = VOICES.get(language, VOICES["en"])
    body = escape(text[:MAX_CHARS])
    if excited:  # big moments: a touch quicker, like a commentator rising with the play
        body = f"<prosody rate='+8%'>{body}</prosody>"
    return f"<speak version='1.0' xml:lang='{locale}'><voice name='{voice}'>{body}</voice></speak>"


class VoiceService:
    def __init__(self, endpoint: str, credential, cache_dir: str | Path | None = None):
        self.url = f"{endpoint}/tts/cognitiveservices/v1"
        self.credential = credential
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.memory: dict[str, bytes] = {}
        self.inflight: dict[str, asyncio.Task] = {}
        self.token: tuple[str, float] | None = None
        self.http = httpx.AsyncClient(timeout=30)

    async def _bearer(self) -> str:
        if not self.token or self.token[1] - time.time() < 120:
            t = await self.credential.get_token(SCOPE)
            self.token = (t.token, t.expires_on)
        return self.token[0]

    async def speak(self, text: str, language: str, excited: bool = False) -> bytes:
        doc = ssml(text, language, excited)
        key = hashlib.sha1(doc.encode()).hexdigest()
        if key in self.memory:
            return self.memory[key]
        path = self.cache_dir / f"{key}.mp3" if self.cache_dir else None
        if path and path.exists():
            self.memory[key] = path.read_bytes()
            return self.memory[key]
        if key not in self.inflight:  # two fans asking for the same line share one synthesis
            self.inflight[key] = asyncio.create_task(self._synthesize(doc))
        try:
            audio = await asyncio.shield(self.inflight[key])  # one listener leaving must not cancel it for the rest
        finally:
            self.inflight.pop(key, None)
        self.memory[key] = audio
        if path:
            path.write_bytes(audio)
        return audio

    async def _synthesize(self, doc: str) -> bytes:
        r = await self.http.post(self.url, content=doc.encode(), headers={
            "Authorization": f"Bearer {await self._bearer()}", "Content-Type": "application/ssml+xml",
            "X-Microsoft-OutputFormat": "audio-24khz-48kbitrate-mono-mp3", "User-Agent": "touchline"})
        r.raise_for_status()
        return r.content
