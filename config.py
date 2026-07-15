from __future__ import annotations

from pydantic import BaseModel, Field, HttpUrl


class LLMConfig(BaseModel):
    base_url: str
    api_key: str
    model: str
    temperature: float | None = None
    max_completion_tokens: int | None = None


class AliyunSTTConfig(BaseModel):
    base_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    api_key: str
    model: str = "qwen3-asr-flash-realtime"
    language: str = "zh"


class CartesiaTTSConfig(BaseModel):
    base_url: str = "https://api.cartesia.ai"
    api_key: str
    model: str = "sonic-3.5"
    voice_id: str
    language: str = "zh"
    speed: float = 1.0
    volume: float = 1.0


class HistoryMessage(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(max_length=32_000)


class ToolFunction(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=8_000)
    parameters: dict = Field(default_factory=dict)


class ToolDefinition(BaseModel):
    type: str = Field(pattern="^function$")
    function: ToolFunction


class SessionConfig(BaseModel):
    identity: str = Field(min_length=1, max_length=128)
    display_name: str = Field(default="YSClaude 用户", max_length=128)
    system_prompt: str = Field(default="You are a helpful assistant.", max_length=100_000)
    opening_instruction: str | None = Field(default=None, max_length=4_000)
    conversation_id: str | None = Field(default=None, max_length=128)
    visual_mode: str = Field(default="voice", pattern="^(voice|video|screen)$")
    history_messages: list[HistoryMessage] = Field(default_factory=list, max_length=200)
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=200)
    llm: LLMConfig
    stt: AliyunSTTConfig
    tts: CartesiaTTSConfig


class ConnectionDetails(BaseModel):
    server_url: str
    room_name: str
    participant_token: str
