from __future__ import annotations

import asyncio
import base64
import json
import time
import uuid
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import websockets
from livekit.agents import APIConnectOptions, DEFAULT_API_CONNECT_OPTIONS, stt
from livekit.agents.types import NOT_GIVEN, NotGivenOr
from livekit.agents.utils import is_given


class AliyunSTT(stt.STT):
    """Streaming LiveKit STT adapter for DashScope Qwen realtime ASR."""

    def __init__(self, *, api_key: str, base_url: str, model: str, language: str = "zh") -> None:
        super().__init__(capabilities=stt.STTCapabilities(streaming=True, interim_results=True))
        self._api_key = api_key
        self._base_url = base_url
        self._model = model
        self._language = language

    @property
    def model(self) -> str:
        return self._model

    @property
    def provider(self) -> str:
        return "aliyun"

    async def _recognize_impl(self, *args: Any, **kwargs: Any) -> stt.SpeechEvent:
        raise NotImplementedError("AliyunSTT only supports realtime streaming")

    def stream(
        self,
        *,
        language: NotGivenOr[str] = NOT_GIVEN,
        conn_options: APIConnectOptions = DEFAULT_API_CONNECT_OPTIONS,
    ) -> stt.RecognizeStream:
        return AliyunRecognizeStream(
            owner=self,
            conn_options=conn_options,
            language=language if is_given(language) and language else self._language,
        )


class AliyunRecognizeStream(stt.RecognizeStream):
    def __init__(self, *, owner: AliyunSTT, conn_options: APIConnectOptions, language: str) -> None:
        super().__init__(stt=owner, conn_options=conn_options, sample_rate=16_000)
        self._owner = owner
        self._language = language
        self._speech_active = False
        self._last_final = ""

    async def _run(self) -> None:
        headers = {
            "Authorization": f"Bearer {self._owner._api_key}",
            "OpenAI-Beta": "realtime=v1",
        }
        async with websockets.connect(
            _build_url(self._owner._base_url, self._owner._model),
            additional_headers=headers,
            ping_interval=20,
            ping_timeout=20,
            close_timeout=5,
            max_size=4 * 1024 * 1024,
        ) as ws:
            await ws.send(json.dumps(_session_update(self._owner._model, self._language)))
            sender = asyncio.create_task(self._send_audio(ws), name="aliyun-stt-send")
            receiver = asyncio.create_task(self._receive_events(ws), name="aliyun-stt-receive")
            done, pending = await asyncio.wait(
                {sender, receiver}, return_when=asyncio.FIRST_EXCEPTION
            )
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for task in done:
                task.result()

    async def _send_audio(self, ws: Any) -> None:
        async for item in self._input_ch:
            if isinstance(item, self._FlushSentinel):
                continue
            pcm = bytes(item.data)
            if not pcm:
                continue
            await ws.send(json.dumps({
                "event_id": _event_id("audio"),
                "type": "input_audio_buffer.append",
                "audio": base64.b64encode(pcm).decode("ascii"),
            }))
        await ws.send(json.dumps({"event_id": _event_id("finish"), "type": "session.finish"}))

    async def _receive_events(self, ws: Any) -> None:
        async for raw in ws:
            event = json.loads(raw)
            event_type = str(event.get("type", ""))
            if event_type == "error":
                message = event.get("message") or event.get("error", {}).get("message") or str(event)
                raise RuntimeError(f"Aliyun STT: {message}")

            if "speech_started" in event_type:
                if not self._speech_active:
                    self._speech_active = True
                    self._event_ch.send_nowait(stt.SpeechEvent(type=stt.SpeechEventType.START_OF_SPEECH))
                continue

            text = _extract_text(event).strip()
            if text:
                final = _is_final(event_type)
                if not final or text != self._last_final:
                    self._event_ch.send_nowait(stt.SpeechEvent(
                        type=stt.SpeechEventType.FINAL_TRANSCRIPT if final else stt.SpeechEventType.INTERIM_TRANSCRIPT,
                        request_id=str(event.get("event_id", "")),
                        alternatives=[stt.SpeechData(language=self._language, text=text)],
                    ))
                if final:
                    self._last_final = text

            if "speech_stopped" in event_type:
                if self._speech_active:
                    self._speech_active = False
                    self._event_ch.send_nowait(stt.SpeechEvent(type=stt.SpeechEventType.END_OF_SPEECH))
                continue

            if event_type == "session.finished":
                return


def _build_url(base_url: str, model: str) -> str:
    parsed = urlparse(base_url.strip())
    scheme = "wss" if parsed.scheme in {"https", "wss"} else "ws"
    query = dict(parse_qsl(parsed.query))
    query["model"] = model
    return urlunparse((scheme, parsed.netloc, parsed.path, parsed.params, urlencode(query), parsed.fragment))


def _session_update(model: str, language: str) -> dict[str, Any]:
    return {
        "event_id": _event_id("session"),
        "type": "session.update",
        "session": {
            "modalities": ["text"],
            "input_audio_format": "pcm",
            "sample_rate": 16_000,
            "input_audio_transcription": {"model": model, "language": language},
            "turn_detection": {
                "type": "server_vad",
                "threshold": 0.2,
                "prefix_padding_ms": 300,
                "silence_duration_ms": 700,
            },
        },
    }


def _extract_text(event: dict[str, Any]) -> str:
    for value in (
        event.get("text"),
        event.get("transcript"),
        event.get("delta"),
        event.get("item", {}).get("content", [{}])[0].get("transcript")
        if isinstance(event.get("item", {}).get("content"), list) else None,
    ):
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _is_final(event_type: str) -> bool:
    return "completed" in event_type or event_type.endswith(".done") or event_type.endswith(".final")


def _event_id(prefix: str) -> str:
    return f"event_{prefix}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
