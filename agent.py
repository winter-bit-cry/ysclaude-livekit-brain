from __future__ import annotations

import logging
import os
import asyncio
import json

from dotenv import load_dotenv
from livekit.agents import Agent, AgentServer, AgentSession, JobContext, TurnHandlingOptions, cli, inference
from livekit.plugins import cartesia, openai, silero

from aliyun_stt import AliyunSTT
from config import SessionConfig
from session_crypto import decrypt_session_config


load_dotenv(".env.local")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
server = AgentServer()


@server.rtc_session(agent_name=os.getenv("LIVEKIT_AGENT_NAME", "ysclaude-voice"))
async def ysclaude_voice(ctx: JobContext) -> None:
    config = SessionConfig.model_validate_json(decrypt_session_config(ctx.job.metadata))
    vad = silero.VAD.load(min_silence_duration=0.35, min_speech_duration=0.08)
    session = AgentSession(
        vad=vad,
        stt=AliyunSTT(
            api_key=config.stt.api_key,
            base_url=config.stt.base_url,
            model=config.stt.model,
            language=config.stt.language,
        ),
        llm=openai.LLM(
            api_key=config.llm.api_key,
            base_url=config.llm.base_url.rstrip("/"),
            model=config.llm.model,
            temperature=config.llm.temperature,
            max_completion_tokens=config.llm.max_completion_tokens,
        ),
        tts=cartesia.TTS(
            api_key=config.tts.api_key,
            base_url=config.tts.base_url.rstrip("/"),
            model=config.tts.model,
            voice=config.tts.voice_id,
            language=config.tts.language,
            speed=config.tts.speed,
            volume=config.tts.volume,
            word_timestamps=False,
        ),
        turn_handling=TurnHandlingOptions(
            turn_detection=inference.TurnDetector(),
            min_endpointing_delay=0.3,
            max_endpointing_delay=2.0,
            allow_interruptions=True,
            min_interruption_duration=0.2,
            resume_false_interruption=True,
        ),
        preemptive_generation=True,
    )
    agent = Agent(instructions=_voice_instructions(config.system_prompt))
    await session.start(room=ctx.room, agent=agent)

    @ctx.room.on("data_received")
    def on_data_received(packet) -> None:
        if packet.topic != "ysclaude.command":
            return
        try:
            command = json.loads(packet.data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return
        if command.get("type") == "generate_reply" and str(command.get("instructions", "")).strip():
            asyncio.create_task(session.generate_reply(
                instructions=str(command["instructions"]).strip()
            ))

    if config.opening_instruction:
        await session.generate_reply(instructions=config.opening_instruction)


def _voice_instructions(system_prompt: str) -> str:
    return (
        f"{system_prompt.strip()}\n\n"
        "当前是实时语音通话。回复要简洁、自然、适合直接朗读；避免 Markdown 表格、"
        "长列表和复杂符号。先给结论，再在必要时补充。用户插话时立即停止当前回答。"
    )


if __name__ == "__main__":
    cli.run_app(server)
