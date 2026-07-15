from __future__ import annotations

import logging
import os
import asyncio
import json

from dotenv import load_dotenv
from livekit.agents import Agent, AgentServer, AgentSession, ChatContext, JobContext, TurnHandlingOptions, cli, function_tool, inference, llm
from livekit.agents.voice.room_io import RoomOptions
from livekit.plugins import cartesia, openai, silero

from aliyun_stt import AliyunSTT
from config import SessionConfig
from session_crypto import decrypt_session_config


load_dotenv(".env.local")
logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
# Personal deployment: do not prewarm the production default of 16 idle job
# processes. A job is created on demand when the single user starts a call.
server = AgentServer(
    num_idle_processes=0,
)


class _LatestVideoFrame:
    frame = None

    def sample(self, frame, _session) -> bool:
        self.frame = frame
        # The OpenAI-compatible chat LLM receives a still image at the end of
        # each spoken turn; it does not consume the realtime video channel.
        return False


class _VisualAgent(Agent):
    def __init__(self, *, latest_video: _LatestVideoFrame, **kwargs) -> None:
        super().__init__(**kwargs)
        self._latest_video = latest_video

    async def on_user_turn_completed(
        self, turn_ctx: llm.ChatContext, new_message: llm.ChatMessage
    ) -> None:
        # Images from earlier turns are not useful for a live camera/screen view
        # and can make the model answer from a stale frame. Remove them from the
        # temporary request context while retaining the text history.
        for item in turn_ctx.items:
            if isinstance(item, llm.ChatMessage):
                item.content = [
                    content
                    for content in item.content
                    if not isinstance(content, llm.ImageContent)
                ]

        if self._latest_video.frame is not None:
            # Add the current frame only to this turn's temporary context. Do
            # not append it to new_message: LiveKit persists new_message in the
            # agent history after scheduling the reply, which would resend the
            # frame on later turns.
            turn_ctx.add_message(
                role="user",
                content=[
                    "Current live camera or screen-share frame for this turn:",
                    llm.ImageContent(
                        image=self._latest_video.frame,
                        inference_detail="auto",
                    ),
                ],
            )


@server.rtc_session(agent_name=os.getenv("LIVEKIT_AGENT_NAME", "ysclaude-voice"))
async def ysclaude_voice(ctx: JobContext) -> None:
    config = SessionConfig.model_validate_json(decrypt_session_config(ctx.job.metadata))
    # RoomIO, video subscription and frontend RPC all require the agent's local
    # participant. Explicitly connect before constructing/starting AgentSession;
    # relying on implicit connection is version-dependent and can race startup.
    await ctx.connect()
    latest_video = _LatestVideoFrame()
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
            endpointing={
                "min_delay": 0.3,
                "max_delay": 2.0,
            },
            interruption={
                "enabled": True,
                "min_duration": 0.2,
                "resume_false_interruption": True,
            },
            # Visual frames are appended in on_user_turn_completed. If
            # generation starts early, the reply can miss the current frame.
            # Retain the latency optimization only for voice-only calls.
            preemptive_generation={
                "enabled": config.visual_mode == "voice",
            },
        ),
        video_sampler=latest_video.sample if config.visual_mode != "voice" else None,
    )
    chat_ctx = ChatContext.empty()
    for message in config.history_messages:
        chat_ctx.add_message(role=message.role, content=message.content)
    agent = _VisualAgent(
        latest_video=latest_video,
        instructions=_voice_instructions(config.system_prompt, config.visual_mode),
        chat_ctx=chat_ctx,
        tools=[_create_frontend_tool(ctx, config.identity, tool.model_dump()) for tool in config.tools],
    )
    await session.start(
        room=ctx.room,
        agent=agent,
        room_options=RoomOptions(
            video_input=config.visual_mode != "voice",
            participant_identity=config.identity,
        ),
    )

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


def _voice_instructions(system_prompt: str, visual_mode: str = "voice") -> str:
    call_instruction = "当前是实时语音通话，没有视觉画面。不要声称看到了用户或屏幕。"
    if visual_mode == "video":
        call_instruction = (
            "当前是实时视频通话。用户正在发送摄像头画面，每轮都可参考随用户消息"
            "附带的最新画面；无法看清时明确询问，不要臆测。"
        )
    elif visual_mode == "screen":
        call_instruction = (
            "当前是实时共享屏幕通话。每轮都可参考随用户消息附带的最新屏幕画面，"
            "重点关注界面、文字和用户操作；无法看清时明确询问，不要臆测。"
        )
    return (
        f"{system_prompt.strip()}\n\n"
        f"{call_instruction}回复要简洁、自然、适合直接朗读；避免 Markdown 表格、"
        "长列表和复杂符号。先给结论，再在必要时补充。用户插话时立即停止当前回答。"
    )


def _create_frontend_tool(ctx: JobContext, participant_identity: str, definition: dict):
    function = definition["function"]
    raw_schema = {
        "name": function["name"],
        "description": function.get("description", ""),
        "parameters": function.get("parameters") or {
            "type": "object",
            "properties": {},
            "required": [],
        },
    }

    async def handler(raw_arguments: dict[str, object]):
        response = await ctx.room.local_participant.perform_rpc(
            destination_identity=participant_identity,
            method="ysclaude.execute_tool",
            payload=json.dumps({
                "name": function["name"],
                "arguments": raw_arguments,
            }, ensure_ascii=False),
            response_timeout=60.0,
        )
        result = json.loads(response)
        return result.get("text", response)

    return function_tool(handler, raw_schema=raw_schema)


if __name__ == "__main__":
    cli.run_app(server)
