from __future__ import annotations

import datetime
import json
import os
import secrets
import uuid

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException
from livekit import api

from config import ConnectionDetails, SessionConfig
from session_crypto import encrypt_session_config


load_dotenv(".env.local")
app = FastAPI(title="YSClaude LiveKit Brain", version="0.1.0")


@app.get("/health")
async def health() -> dict[str, str | bool]:
    return {
        "ok": True,
        "service": "ysclaude-livekit-brain",
        "agent": os.getenv("LIVEKIT_AGENT_NAME", "ysclaude-voice"),
    }


@app.post("/api/livekit/session", response_model=ConnectionDetails)
async def create_session(
    config: SessionConfig,
    authorization: str | None = Header(default=None),
) -> ConnectionDetails:
    _verify_shared_secret(authorization)
    livekit_url = _required("LIVEKIT_URL")
    room_name = f"ysclaude-{uuid.uuid4().hex}"
    api_key = _required("LIVEKIT_API_KEY")
    api_secret = _required("LIVEKIT_API_SECRET")
    livekit = api.LiveKitAPI(livekit_url, api_key, api_secret)
    try:
        await livekit.agent_dispatch.create_dispatch(api.CreateAgentDispatchRequest(
            agent_name=os.getenv("LIVEKIT_AGENT_NAME", "ysclaude-voice"),
            room=room_name,
            metadata=encrypt_session_config(config.model_dump_json()),
        ))
    finally:
        await livekit.aclose()
    token = (
        api.AccessToken(api_key, api_secret)
        .with_identity(config.identity)
        .with_name(config.display_name)
        .with_ttl(datetime.timedelta(minutes=10))
        .with_grants(api.VideoGrants(
            room_join=True,
            room=room_name,
            can_publish=True,
            can_subscribe=True,
            can_publish_data=True,
            can_publish_sources=["microphone"],
        ))
        .to_jwt()
    )
    return ConnectionDetails(
        server_url=livekit_url,
        room_name=room_name,
        participant_token=token,
    )


def _verify_shared_secret(authorization: str | None) -> None:
    expected = os.getenv("BRAIN_SHARED_SECRET", "").strip()
    if not expected:
        return
    supplied = authorization.removeprefix("Bearer ").strip() if authorization else ""
    if not secrets.compare_digest(supplied, expected):
        raise HTTPException(status_code=401, detail="Invalid Brain access token")


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise HTTPException(status_code=503, detail=f"Missing server setting: {name}")
    return value
