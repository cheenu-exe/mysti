from __future__ import annotations

import asyncio
import json
import os
from collections import Counter
from pathlib import Path
from threading import Lock
from time import monotonic
from typing import Any

from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

ROOT_DIR = Path(__file__).resolve().parent.parent
INDEX_FILE = ROOT_DIR / "index.html"
IGNORED_DIRS = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache"}

VALID_MODES = ("assistant", "dev mode", "cyber mode", "focus mode")

MODE_SUGGESTIONS = {
    "assistant": [
        "analyze code",
        "scan system",
        "summarize this",
        "optimize workflow",
        "show status",
    ],
    "dev mode": [
        "analyze code",
        "review architecture",
        "find weak points",
        "draft api plan",
        "show status",
    ],
    "cyber mode": [
        "scan system",
        "search exploitdb",
        "map attack surface",
        "check dependencies",
        "show status",
    ],
    "focus mode": [
        "next best step",
        "simplify plan",
        "summarize this",
        "clear distractions",
        "show status",
    ],
}

MODE_LINES = {
    "assistant": [
        {"type": "info", "text": "[OK] assistant profile online"},
        {"type": "out", "text": "friendly help, summaries, and guided actions enabled"},
    ],
    "dev mode": [
        {"type": "info", "text": "[OK] dev tooling profile engaged"},
        {"type": "out", "text": "engineering analysis, architecture hints, and debug flows armed"},
    ],
    "cyber mode": [
        {"type": "warn", "text": "[!] cyber profile active"},
        {"type": "out", "text": "defensive reconnaissance mode only; no live exploitation is performed"},
    ],
    "focus mode": [
        {"type": "info", "text": "[OK] focus profile engaged"},
        {"type": "out", "text": "noise reduced; responses will bias toward the single next action"},
    ],
}

MODE_CONFIDENCE = {
    "assistant": 78,
    "dev mode": 86,
    "cyber mode": 72,
    "focus mode": 91,
}


def line(text: str, kind: str = "out") -> dict[str, str]:
    return {"type": kind, "text": text}


class ModePayload(BaseModel):
    mode: str = Field(..., min_length=1)


class QuickOpPayload(BaseModel):
    op: str = Field(..., min_length=1)


class ChatPayload(BaseModel):
    message: str = Field(..., min_length=1)
    mode: str = "assistant"


class VoicePayload(BaseModel):
    transcript: str = Field(..., min_length=1)
    mode: str = "assistant"


class ConsoleState:
    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root = workspace_root
        self._lock = Lock()
        self.reset()

    def reset(self) -> None:
        with self._lock:
            now = monotonic()
            self.started_at = now
            self.current_mode = "assistant"
            self.command_count = 0
            self.last_command = ""
            self.current_task = "idle"
            self.confidence = MODE_CONFIDENCE["assistant"]
            self.train_mode = False
            self.input_until = now + 2.0
            self.voice_until = 0.0
            self.exploit_until = 0.0
            self.analysis_until = 0.0

    def set_mode(self, mode: str) -> None:
        with self._lock:
            now = monotonic()
            self.current_mode = mode
            self.current_task = mode
            self.confidence = MODE_CONFIDENCE[mode]
            self.input_until = now + 2.5

    def begin_command(self, command: str, *, voice: bool = False) -> None:
        with self._lock:
            now = monotonic()
            self.command_count += 1
            self.last_command = command
            self.current_task = command[:32]
            self.input_until = now + 3.5
            if voice:
                self.voice_until = now + 4.5

    def finish_command(self) -> None:
        with self._lock:
            self.current_task = "idle"

    def set_confidence(self, value: int) -> None:
        with self._lock:
            self.confidence = max(35, min(99, value))

    def nudge_confidence(self, delta: int) -> None:
        with self._lock:
            self.confidence = max(35, min(99, self.confidence + delta))

    def set_train_mode(self, enabled: bool) -> None:
        with self._lock:
            self.train_mode = enabled

    def activate_analysis(self, duration: float = 6.0) -> None:
        with self._lock:
            self.analysis_until = monotonic() + duration

    def activate_exploit_scan(self, duration: float = 6.0) -> None:
        with self._lock:
            self.exploit_until = monotonic() + duration

    def activate_voice(self, duration: float = 4.5) -> None:
        with self._lock:
            self.voice_until = monotonic() + duration

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = monotonic()
            uptime = int(now - self.started_at)
            processes = [
                {"name": "neural.core", "status": "run"},
                {"name": "input.parser", "status": "run" if self.input_until > now else "idle"},
                {"name": "voice.module", "status": "run" if self.voice_until > now else "idle"},
                {"name": "exploit.scan", "status": "run" if self.exploit_until > now else "wait"},
                {"name": "code.analyzer", "status": "run" if self.analysis_until > now else "wait"},
            ]
            return {
                "mode": self.current_mode,
                "cmdCount": self.command_count,
                "confidence": self.confidence,
                "uptimeSec": uptime,
                "currentTask": self.current_task,
                "lastCommand": self.last_command,
                "trainMode": self.train_mode,
                "processes": processes,
            }


def normalize_mode(mode: str) -> str:
    clean = " ".join(mode.strip().lower().split())
    return clean if clean in VALID_MODES else "assistant"


def normalize_text(text: str) -> str:
    return " ".join(text.strip().lower().split())


def workspace_snapshot(root: Path) -> dict[str, Any]:
    file_count = 0
    dir_count = 0
    total_bytes = 0
    extensions: Counter[str] = Counter()

    for current_root, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in IGNORED_DIRS]
        dir_count += len(dirnames)
        for filename in filenames:
            path = Path(current_root) / filename
            file_count += 1
            suffix = path.suffix.lower() or "[no ext]"
            extensions[suffix] += 1
            try:
                total_bytes += path.stat().st_size
            except OSError:
                continue

    top_extensions = ", ".join(f"{ext} x{count}" for ext, count in extensions.most_common(4)) or "none"

    return {
        "file_count": file_count,
        "dir_count": dir_count,
        "total_bytes": total_bytes,
        "top_extensions": top_extensions,
    }


class ConsoleEngine:
    def __init__(self, state: ConsoleState) -> None:
        self.state = state

    def mode_lines(self, mode: str) -> list[dict[str, str]]:
        normalized = normalize_mode(mode)
        self.state.set_mode(normalized)
        lines = list(MODE_LINES[normalized])
        if self.state.snapshot()["trainMode"]:
            lines.append(line("training overlays are enabled for this session", "warn"))
        lines.append(line(f"suggestion profile swapped to {normalized}", "sys"))
        self.state.finish_command()
        return lines

    def suggestion_payload(self, mode: str) -> dict[str, list[str]]:
        normalized = normalize_mode(mode)
        chips = MODE_SUGGESTIONS.get(normalized, MODE_SUGGESTIONS["assistant"])
        return {"chips": chips}

    def quick_op_lines(self, op: str) -> dict[str, Any]:
        command = op.strip()
        normalized = normalize_text(command)
        self.state.begin_command(command)

        if normalized == "scan system":
            snapshot = workspace_snapshot(self.state.workspace_root)
            self.state.activate_analysis(5.0)
            self.state.nudge_confidence(4)
            lines = [
                line("[scan] workspace probe started", "info"),
                line(f"files indexed: {snapshot['file_count']}"),
                line(f"directories indexed: {snapshot['dir_count']}"),
                line(f"dominant extensions: {snapshot['top_extensions']}"),
                line(f"payload size: {snapshot['total_bytes']} bytes"),
            ]
        elif normalized == "clear logs":
            self.state.nudge_confidence(1)
            lines = [
                line("[OK] console log reset requested", "info"),
                line("frontend buffer cleared; session state preserved"),
            ]
            self.state.finish_command()
            return {"lines": lines, "resetLogs": True}
        elif normalized == "train mode on":
            self.state.set_train_mode(True)
            self.state.nudge_confidence(2)
            lines = [
                line("[OK] training overlays enabled", "info"),
                line("responses will include extra hints and next-step nudges"),
            ]
        else:
            self.state.nudge_confidence(-6)
            lines = [
                line("[warn] unknown quick op", "warn"),
                line('available quick ops: "scan system", "clear logs", "train mode on"'),
            ]

        self.state.finish_command()
        return {"lines": lines}

    def chat_lines(self, message: str, mode: str, *, source: str) -> list[dict[str, str]]:
        normalized_mode = normalize_mode(mode)
        cleaned_message = message.strip()
        normalized = normalize_text(cleaned_message)

        self.state.set_mode(normalized_mode)
        self.state.begin_command(cleaned_message, voice=source == "voice")

        if source == "voice":
            self.state.activate_voice()

        lines = self._dispatch_command(normalized, normalized_mode)
        if self.state.snapshot()["trainMode"]:
            lines.append(line("trainer hint: use short verb-first commands for sharper parsing", "warn"))

        self.state.finish_command()
        return lines

    def _dispatch_command(self, normalized: str, mode: str) -> list[dict[str, str]]:
        if any(token in normalized for token in ("help", "commands", "what can you do")):
            self.state.nudge_confidence(3)
            return [
                line("[help] available command lanes", "info"),
                line("analyze code  |  scan system  |  optimize workflow"),
                line("search exploitdb  |  summarize this  |  show status"),
            ]

        if "show status" in normalized or normalized == "status":
            self.state.nudge_confidence(2)
            return self._status_lines()

        if "scan system" in normalized:
            payload = self.quick_op_lines("scan system")
            return payload["lines"]

        if "analyze" in normalized and "code" in normalized:
            return self._analyze_code_lines(mode)

        if "architecture" in normalized or "api plan" in normalized:
            return self._architecture_lines()

        if "workflow" in normalized or "optimize" in normalized:
            return self._workflow_lines()

        if "summarize" in normalized:
            self.state.nudge_confidence(1)
            return [
                line("[sum] ready for source material", "info"),
                line("paste text or point to the exact thing you want condensed"),
                line("current backend supports summaries, but it needs the content to summarize"),
            ]

        if "exploitdb" in normalized or "attack surface" in normalized or "dependencies" in normalized:
            return self._cyber_lines(normalized)

        if "open chrome" in normalized or "open browser" in normalized:
            self.state.nudge_confidence(-3)
            return [
                line("[warn] browser launch is not wired into this backend", "warn"),
                line("the API stays read-only and simulation-safe by design"),
            ]

        if "next best step" in normalized or "simplify plan" in normalized or "clear distractions" in normalized:
            self.state.nudge_confidence(4)
            return [
                line("[focus] immediate next step", "info"),
                line("1. start the backend"),
                line("2. verify the state stream is live"),
                line("3. iterate on command handlers only after the UI is talking to the server"),
            ]

        self.state.nudge_confidence(-4)
        if mode == "dev mode":
            return [
                line("[dev] command parsed but no direct handler matched", "warn"),
                line("try: analyze code, review architecture, optimize workflow, or show status"),
            ]
        if mode == "cyber mode":
            return [
                line("[cyber] no offline signature matched", "warn"),
                line("try: scan system, search exploitdb, map attack surface, or check dependencies"),
            ]
        if mode == "focus mode":
            return [
                line("[focus] command too broad", "warn"),
                line("ask for one concrete step, such as 'next best step' or 'simplify plan'"),
            ]
        return [
            line("[assistant] command accepted but ambiguous", "warn"),
            line("try: analyze code, summarize this, optimize workflow, or show status"),
        ]

    def _status_lines(self) -> list[dict[str, str]]:
        snapshot = self.state.snapshot()
        self.state.nudge_confidence(2)
        return [
            line("[status] backend live", "info"),
            line(f"mode: {snapshot['mode']}"),
            line(f"commands seen: {snapshot['cmdCount']}"),
            line(f"confidence: {snapshot['confidence']}%"),
            line(f"train mode: {'on' if snapshot['trainMode'] else 'off'}"),
        ]

    def _analyze_code_lines(self, mode: str) -> list[dict[str, str]]:
        snapshot = workspace_snapshot(self.state.workspace_root)
        self.state.activate_analysis(7.0)
        self.state.nudge_confidence(6)
        lines = [
            line("[analysis] workspace scan complete", "info"),
            line(f"project size: {snapshot['file_count']} files across {snapshot['dir_count']} directories"),
            line(f"top file types: {snapshot['top_extensions']}"),
        ]
        if snapshot["file_count"] <= 8:
            lines.append(line("shape looks early-stage; this is a good time to lock in clean API boundaries"))
        if mode == "dev mode":
            lines.append(line("dev note: backend and UI are now separated cleanly, which will make expansion easier"))
        else:
            lines.append(line("next move: run a few sample commands and watch the right panel update in real time"))
        return lines

    def _architecture_lines(self) -> list[dict[str, str]]:
        self.state.activate_analysis(6.0)
        self.state.nudge_confidence(5)
        return [
            line("[arch] current backend shape", "info"),
            line("transport layer: FastAPI + JSON endpoints + SSE state feed"),
            line("state layer: in-memory session tracker for mode, confidence, counts, and process flags"),
            line("logic layer: rule-based command router that keeps the console responsive without extra services"),
        ]

    def _workflow_lines(self) -> list[dict[str, str]]:
        self.state.activate_analysis(4.0)
        self.state.nudge_confidence(4)
        return [
            line("[flow] workflow optimization suggestions", "info"),
            line("serve the UI and API from the same process during local development"),
            line("add command-specific tests as new handlers are introduced"),
            line("keep browser-side actions explicit instead of hiding side effects inside chat responses"),
        ]

    def _cyber_lines(self, normalized: str) -> list[dict[str, str]]:
        self.state.activate_exploit_scan(7.0)
        self.state.nudge_confidence(3)
        if "exploitdb" in normalized:
            return [
                line("[cyber] external exploit search is disabled offline", "warn"),
                line("use this mode for local triage and defensive inventory, not network lookups"),
                line("next step: wire a reviewed external search service if you want live intelligence later"),
            ]
        if "dependencies" in normalized:
            return [
                line("[cyber] dependency audit lane selected", "info"),
                line("current backend does not shell out to package scanners"),
                line("safe upgrade path: add an explicit audit endpoint instead of piggybacking on chat"),
            ]
        return [
            line("[cyber] attack surface map", "info"),
            line("exposed interfaces: REST endpoints under /api plus the SSE state stream"),
            line("current posture: local-only, no auth, intended for development use"),
        ]


app = FastAPI(title="Mystiv Command Console Backend", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.state.console = ConsoleState(ROOT_DIR)
engine = ConsoleEngine(app.state.console)


@app.get("/", response_model=None)
async def serve_index() -> Any:
    if INDEX_FILE.exists():
        return FileResponse(INDEX_FILE)
    return {"message": "frontend not found"}


@app.get("/api")
async def api_root() -> dict[str, Any]:
    return {
        "name": "Mystiv Command Console Backend",
        "version": app.version,
        "modes": list(VALID_MODES),
    }


@app.get("/api/health")
async def health() -> dict[str, Any]:
    return {"ok": True, "state": app.state.console.snapshot()}


@app.post("/api/mode")
async def set_mode(payload: ModePayload) -> dict[str, Any]:
    normalized = normalize_mode(payload.mode)
    return {"mode": normalized, "lines": engine.mode_lines(normalized)}


@app.get("/api/suggestions")
async def suggestions(mode: str = Query("assistant")) -> dict[str, list[str]]:
    return engine.suggestion_payload(mode)


@app.post("/api/quickops")
async def quick_ops(payload: QuickOpPayload) -> dict[str, Any]:
    return engine.quick_op_lines(payload.op)


@app.post("/api/chat")
async def chat(payload: ChatPayload) -> dict[str, Any]:
    return {"lines": engine.chat_lines(payload.message, payload.mode, source="chat")}


@app.post("/api/voice")
async def voice(payload: VoicePayload) -> dict[str, Any]:
    return {"lines": engine.chat_lines(payload.transcript, payload.mode, source="voice")}


@app.get("/api/state")
async def state_stream(request: Request) -> StreamingResponse:
    async def event_stream() -> Any:
        while True:
            if await request.is_disconnected():
                break
            payload = json.dumps(app.state.console.snapshot())
            yield f"data: {payload}\n\n"
            await asyncio.sleep(1)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
