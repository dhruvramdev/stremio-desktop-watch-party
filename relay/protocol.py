"""
Protocol models, message schemas, and enums for Stremio Desktop Watch Party.
"""

from enum import Enum
import time
from typing import Optional, Any
from pydantic import BaseModel, Field, ConfigDict


class Role(str, Enum):
    HOST = "host"
    GUEST = "guest"


class MessageType(str, Enum):
    HELLO = "hello"
    WELCOME = "welcome"
    JOIN = "join"
    LEAVE = "leave"
    PLAY = "play"
    PAUSE = "pause"
    SEEK = "seek"
    STATE = "state"
    PING = "ping"
    PONG = "pong"
    ERROR = "error"


class TunnelProvider(str, Enum):
    AUTO = "auto"
    CLOUDFLARED = "cloudflared"
    TAILSCALE = "tailscale"


class BaseMessage(BaseModel):
    model_config = ConfigDict(extra="allow", use_enum_values=True)
    type: MessageType


class HelloMessage(BaseMessage):
    type: MessageType = MessageType.HELLO
    room: str
    sender: str
    secret: str = ""
    role: Role = Role.GUEST
    version: int = 1


class WelcomeMessage(BaseMessage):
    type: MessageType = MessageType.WELCOME
    room: str
    role: Role
    clients: list[str] = Field(default_factory=list)


class PeerEventMessage(BaseMessage):
    type: MessageType  # JOIN or LEAVE
    room: str
    sender: str
    role: Optional[Role] = None


class ControlMessage(BaseMessage):
    type: MessageType  # PLAY, PAUSE, SEEK, STATE
    room: str
    sender: str
    seq: int = 0
    sent_at_ms: int = Field(default_factory=lambda: int(time.time() * 1000))
    time_s: float = 0.0
    paused: bool = False
    duration_s: float = 0.0
    status: Optional[str] = None


class ErrorMessage(BaseMessage):
    type: MessageType = MessageType.ERROR
    message: str


class PlaybackState(BaseModel):
    model_config = ConfigDict(extra="allow")
    paused: bool = False
    time_s: float = 0.0
    duration_s: float = 0.0
    seeking: bool = False
    buffering: bool = False
    loaded: bool = False
    media_title: str = ""
    drift_s: float = 0.0


class RecentEvent(BaseModel):
    time: float = Field(default_factory=time.time)
    text: str


class SessionStatus(BaseModel):
    model_config = ConfigDict(extra="allow", use_enum_values=True)
    updated_at: float = Field(default_factory=time.time)
    role: Role = Role.GUEST
    room: str = "default-room"
    relay_url: str = ""
    relay_connected: bool = False
    mpv_connected: bool = False
    clients: list[str] = Field(default_factory=list)
    state: PlaybackState = Field(default_factory=PlaybackState)
    recent_events: list[RecentEvent] = Field(default_factory=list)

