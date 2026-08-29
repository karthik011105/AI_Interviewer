"""Text-to-speech using Piper locally or ElevenLabs as an optional provider.

Public API
----------
synthesize(text, *, voice=None) -> bytes
    Returns WAV audio bytes for the given text.
    Falls back to a silent WAV if the selected provider is unavailable.

synthesize_chunks(text, *, voice=None, chunk_ms=100, sample_rate=22050)
    Returns raw mono PCM chunks and the effective sample rate for progressive playback.

tts_health() -> dict
    Returns the active/fallback provider status for the TTS subsystem.

Configuration (environment variables)
-------------------------------------
TTS_PROVIDER               : "piper" (default) or "elevenlabs"

PIPER_EXECUTABLE           : path to the piper binary (default: auto-discovered)
PIPER_VOICE                : voice model name without extension
PIPER_MODELS_DIR           : directory containing .onnx and .json voice files
PIPER_TIMEOUT_SECONDS      : maximum Piper synthesis time per request (default: 45)

ELEVENLABS_API_KEY         : ElevenLabs API key
ELEVENLABS_VOICE_ID        : default ElevenLabs voice id
ELEVENLABS_MODEL_ID        : default ElevenLabs model id
ELEVENLABS_OUTPUT_FORMAT   : PCM/WAV output format, default "pcm_22050"
ELEVENLABS_TIMEOUT_SECONDS : maximum ElevenLabs request time (default: 15)
"""

from __future__ import annotations

import io
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import wave
from dataclasses import dataclass
from pathlib import Path
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request

# This module reads its configuration straight from os.environ at import time,
# so the project .env has to be loaded first. Delegate to backend.config so
# there is exactly one place that decides .env-vs-real-environment precedence
# (real environment wins unless DOTENV_OVERRIDE is set). Loading it here with
# override=True, as this module used to, would silently undo that policy for
# any process that imports the voice stack.
from backend.config import load_project_dotenv as _load_project_dotenv

_load_project_dotenv()


class TTSUnavailableError(RuntimeError):
    """Raised when the selected TTS provider is unavailable."""


class TTSSynthesisError(RuntimeError):
    """Raised when a configured TTS provider fails to synthesize audio."""


@dataclass(frozen=True, slots=True)
class TTSAudioResult:
    provider: str
    sample_rate: int
    pcm_bytes: bytes


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_DEFAULT_VOICE = "en_US-lessac-medium"


def _read_float_env(name: str, default: float, *, minimum: float | None = None) -> float:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        value = default
    else:
        try:
            value = float(raw)
        except ValueError:
            value = default
    if minimum is not None:
        value = max(minimum, value)
    return value


def _read_optional_float_env(name: str) -> float | None:
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def _read_optional_bool_env(name: str) -> bool | None:
    raw = (os.environ.get(name) or "").strip().casefold()
    if not raw:
        return None
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return None


def _normalize_provider(value: str | None) -> str:
    normalized = str(value or "piper").strip().casefold()
    if normalized in {"eleven", "elevenlabs", "11labs"}:
        return "elevenlabs"
    return "piper"

def _resolve_piper_executable() -> str | None:
    """Resolve Piper from env, the active venv Scripts dir, or PATH."""
    explicit = (os.environ.get("PIPER_EXECUTABLE") or "").strip()
    if explicit:
        return explicit

    python_dir = Path(sys.executable).resolve().parent
    for candidate in (python_dir / "piper.exe", python_dir / "piper"):
        if candidate.exists():
            return str(candidate)

    return shutil.which("piper") or shutil.which("piper-tts")


_PIPER_EXE: str | None = _resolve_piper_executable()
_TTS_PROVIDER: str = _normalize_provider(os.environ.get("TTS_PROVIDER"))
_PIPER_VOICE: str = os.environ.get("PIPER_VOICE", _DEFAULT_VOICE)
_PIPER_LENGTH_SCALE: float = _read_float_env("PIPER_LENGTH_SCALE", 1.12)
_PIPER_SENTENCE_SILENCE: float = _read_float_env("PIPER_SENTENCE_SILENCE", 0.35)
_PIPER_VOLUME: float = _read_float_env("PIPER_VOLUME", 1.0)
_PIPER_TIMEOUT_SECONDS: float = _read_float_env("PIPER_TIMEOUT_SECONDS", 45.0, minimum=1.0)
_DEFAULT_SAMPLE_RATE: int = 22050
_ELEVENLABS_API_BASE_URL: str = (
    (os.environ.get("ELEVENLABS_API_BASE_URL") or "https://api.elevenlabs.io")
    .strip()
    .rstrip("/")
)
_ELEVENLABS_API_KEY: str = (os.environ.get("ELEVENLABS_API_KEY") or "").strip()
_ELEVENLABS_VOICE_ID: str = (os.environ.get("ELEVENLABS_VOICE_ID") or "").strip()
_ELEVENLABS_MODEL_ID: str = (os.environ.get("ELEVENLABS_MODEL_ID") or "").strip()
_ELEVENLABS_LANGUAGE_CODE: str = (os.environ.get("ELEVENLABS_LANGUAGE_CODE") or "").strip()
_ELEVENLABS_OUTPUT_FORMAT: str = (os.environ.get("ELEVENLABS_OUTPUT_FORMAT") or "pcm_22050").strip()
_ELEVENLABS_TIMEOUT_SECONDS: float = _read_float_env("ELEVENLABS_TIMEOUT_SECONDS", 15.0, minimum=1.0)
_ELEVENLABS_ENABLE_LOGGING: bool = _read_optional_bool_env("ELEVENLABS_ENABLE_LOGGING") is not False
_ELEVENLABS_STABILITY: float | None = _read_optional_float_env("ELEVENLABS_STABILITY")
_ELEVENLABS_SIMILARITY_BOOST: float | None = _read_optional_float_env("ELEVENLABS_SIMILARITY_BOOST")
_ELEVENLABS_STYLE: float | None = _read_optional_float_env("ELEVENLABS_STYLE")
_ELEVENLABS_SPEED: float | None = _read_optional_float_env("ELEVENLABS_SPEED")
_ELEVENLABS_USE_SPEAKER_BOOST: bool | None = _read_optional_bool_env("ELEVENLABS_USE_SPEAKER_BOOST")

# Default models directory: <project_root>/backend/data/tts_models/
_THIS_FILE = Path(__file__).resolve()
_DEFAULT_MODELS_DIR = _THIS_FILE.parent.parent / "data" / "tts_models"
_PIPER_MODELS_DIR = Path(os.environ.get("PIPER_MODELS_DIR", str(_DEFAULT_MODELS_DIR)))


def _current_tts_provider() -> str:
    raw_provider = (os.environ.get("TTS_PROVIDER") or "").strip()
    if raw_provider:
        return _normalize_provider(raw_provider)
    return _TTS_PROVIDER


def _current_elevenlabs_config() -> dict[str, object]:
    stability = _read_optional_float_env("ELEVENLABS_STABILITY")
    similarity_boost = _read_optional_float_env("ELEVENLABS_SIMILARITY_BOOST")
    style = _read_optional_float_env("ELEVENLABS_STYLE")
    speed = _read_optional_float_env("ELEVENLABS_SPEED")
    use_speaker_boost = _read_optional_bool_env("ELEVENLABS_USE_SPEAKER_BOOST")
    enable_logging = _read_optional_bool_env("ELEVENLABS_ENABLE_LOGGING")

    return {
        "api_base_url": (
            (os.environ.get("ELEVENLABS_API_BASE_URL") or _ELEVENLABS_API_BASE_URL)
            .strip()
            .rstrip("/")
        ),
        "api_key": (os.environ.get("ELEVENLABS_API_KEY") or _ELEVENLABS_API_KEY).strip(),
        "voice_id": (os.environ.get("ELEVENLABS_VOICE_ID") or _ELEVENLABS_VOICE_ID).strip(),
        "model_id": (os.environ.get("ELEVENLABS_MODEL_ID") or _ELEVENLABS_MODEL_ID).strip(),
        "language_code": (os.environ.get("ELEVENLABS_LANGUAGE_CODE") or _ELEVENLABS_LANGUAGE_CODE).strip(),
        "output_format": (
            (os.environ.get("ELEVENLABS_OUTPUT_FORMAT") or _ELEVENLABS_OUTPUT_FORMAT or "pcm_22050")
            .strip()
        ),
        "timeout_seconds": _read_float_env(
            "ELEVENLABS_TIMEOUT_SECONDS",
            _ELEVENLABS_TIMEOUT_SECONDS,
            minimum=1.0,
        ),
        "enable_logging": _ELEVENLABS_ENABLE_LOGGING if enable_logging is None else enable_logging,
        "stability": _ELEVENLABS_STABILITY if stability is None else stability,
        "similarity_boost": (
            _ELEVENLABS_SIMILARITY_BOOST if similarity_boost is None else similarity_boost
        ),
        "style": _ELEVENLABS_STYLE if style is None else style,
        "speed": _ELEVENLABS_SPEED if speed is None else speed,
        "use_speaker_boost": (
            _ELEVENLABS_USE_SPEAKER_BOOST if use_speaker_boost is None else use_speaker_boost
        ),
    }


# ---------------------------------------------------------------------------
# Silent WAV fallback (0.1 s of silence at 22050 Hz mono 16-bit)
# ---------------------------------------------------------------------------

def _silent_wav(duration_seconds: float = 0.1, sample_rate: int = _DEFAULT_SAMPLE_RATE) -> bytes:
    """Generate a minimal silent WAV blob so the browser audio element never errors."""
    n_samples = int(sample_rate * duration_seconds)
    pcm = b"\x00\x00" * n_samples  # 16-bit PCM silence
    # Build WAV header
    data_size = len(pcm)
    header = struct.pack(
        "<4sI4s4sIHHIIHH4sI",
        b"RIFF",
        36 + data_size,
        b"WAVE",
        b"fmt ",
        16,            # chunk size
        1,             # PCM
        1,             # mono
        sample_rate,
        sample_rate * 2,  # byte rate
        2,             # block align
        16,            # bits per sample
        b"data",
        data_size,
    )
    return header + pcm


def _pcm16le_to_wav_bytes(pcm_bytes: bytes, *, sample_rate: int = _DEFAULT_SAMPLE_RATE) -> bytes:
    """Wrap raw mono 16-bit PCM in a minimal WAV container."""
    data_size = len(pcm_bytes)
    return b"".join(
        [
            struct.pack(
                "<4sI4s4sIHHIIHH4sI",
                b"RIFF",
                36 + data_size,
                b"WAVE",
                b"fmt ",
                16,
                1,
                1,
                sample_rate,
                sample_rate * 2,
                2,
                16,
                b"data",
                data_size,
            ),
            pcm_bytes,
        ]
    )


def _decode_wav_bytes(wav_bytes: bytes, *, provider: str) -> TTSAudioResult:
    try:
        with wave.open(io.BytesIO(wav_bytes), "rb") as reader:
            sample_rate = int(reader.getframerate())
            channels = int(reader.getnchannels())
            sample_width = int(reader.getsampwidth())
            pcm_bytes = reader.readframes(reader.getnframes())
    except Exception as exc:
        raise TTSSynthesisError(f"{provider} returned invalid WAV audio: {exc}") from exc

    if channels != 1 or sample_width != 2:
        raise TTSSynthesisError(
            f"{provider} returned unsupported WAV audio (channels={channels}, sample_width={sample_width})."
        )

    return TTSAudioResult(
        provider=provider,
        sample_rate=sample_rate or _DEFAULT_SAMPLE_RATE,
        pcm_bytes=pcm_bytes,
    )


def _parse_output_format_sample_rate(output_format: str, *, default: int = _DEFAULT_SAMPLE_RATE) -> int:
    for fragment in str(output_format or "").split("_")[1:]:
        if fragment.isdigit():
            return int(fragment)
    return default


# ---------------------------------------------------------------------------
# Piper model resolver
# ---------------------------------------------------------------------------

def _resolve_model_path(voice: str) -> tuple[Path, Path]:
    """Return (onnx_path, config_path) for the given voice name.

    Raises TTSUnavailableError if models are not found.
    """
    models_dir = _PIPER_MODELS_DIR
    onnx = models_dir / f"{voice}.onnx"
    cfg = models_dir / f"{voice}.onnx.json"

    if not onnx.exists():
        raise TTSUnavailableError(
            f"Piper voice model not found: {onnx}. "
            f"Download it from https://github.com/rhasspy/piper/releases "
            f"and place the .onnx and .onnx.json files in {models_dir}."
        )
    if not cfg.exists():
        raise TTSUnavailableError(
            f"Piper voice config not found: {cfg}. "
            f"The .onnx.json config file must accompany the .onnx model file."
        )
    return onnx, cfg


def _build_elevenlabs_voice_settings(config: dict[str, object] | None = None) -> dict[str, object]:
    resolved = config or _current_elevenlabs_config()
    settings: dict[str, object] = {}
    if resolved["stability"] is not None:
        settings["stability"] = resolved["stability"]
    if resolved["similarity_boost"] is not None:
        settings["similarity_boost"] = resolved["similarity_boost"]
    if resolved["style"] is not None:
        settings["style"] = resolved["style"]
    if resolved["speed"] is not None:
        settings["speed"] = resolved["speed"]
    if resolved["use_speaker_boost"] is not None:
        settings["use_speaker_boost"] = resolved["use_speaker_boost"]
    return settings


def _extract_http_error_message(exc: urllib_error.HTTPError) -> str:
    message = str(exc)
    try:
        body = exc.read().decode("utf-8", errors="replace")
    except Exception:
        body = ""

    if not body:
        return message

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body.strip() or message

    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("message") or payload.get("error")
        if isinstance(detail, dict):
            return json.dumps(detail, sort_keys=True)
        if isinstance(detail, list):
            return " ".join(str(item).strip() for item in detail if str(item).strip()) or message
        if detail:
            return str(detail).strip()
    return body.strip() or message


def _piper_health() -> dict[str, object]:
    piper_exe = _PIPER_EXE
    if not piper_exe:
        return {
            "status": "unavailable",
            "error": "Piper binary not found on PATH. Install piper-tts.",
        }

    effective_voice = _PIPER_VOICE
    try:
        onnx_path, _ = _resolve_model_path(effective_voice)
        return {
            "status": "ready",
            "provider": "piper",
            "piper_exe": piper_exe,
            "voice": effective_voice,
            "model_path": str(onnx_path),
            "sample_rate": _DEFAULT_SAMPLE_RATE,
            "length_scale": _PIPER_LENGTH_SCALE,
            "sentence_silence": _PIPER_SENTENCE_SILENCE,
            "timeout_seconds": _PIPER_TIMEOUT_SECONDS,
        }
    except TTSUnavailableError as exc:
        return {
            "status": "model_missing",
            "provider": "piper",
            "piper_exe": piper_exe,
            "voice": effective_voice,
            "error": str(exc),
        }


def _elevenlabs_health(config: dict[str, object] | None = None) -> dict[str, object]:
    resolved = config or _current_elevenlabs_config()
    api_key = str(resolved["api_key"])
    voice_id = str(resolved["voice_id"])
    output_format = str(resolved["output_format"])

    if not api_key:
        return {
            "status": "unavailable",
            "provider": "elevenlabs",
            "error": "ELEVENLABS_API_KEY is not configured.",
        }

    if not voice_id:
        return {
            "status": "unavailable",
            "provider": "elevenlabs",
            "error": "ELEVENLABS_VOICE_ID is not configured.",
        }

    if not output_format.startswith(("pcm_", "wav_")):
        return {
            "status": "unsupported_format",
            "provider": "elevenlabs",
            "error": "ELEVENLABS_OUTPUT_FORMAT must start with pcm_ or wav_.",
            "output_format": output_format,
        }

    return {
        "status": "ready",
        "provider": "elevenlabs",
        "api_base_url": str(resolved["api_base_url"]),
        "voice": voice_id,
        "model_id": str(resolved["model_id"]) or None,
        "output_format": output_format,
        "sample_rate": _parse_output_format_sample_rate(output_format),
        "timeout_seconds": float(resolved["timeout_seconds"]),
    }


def _synthesize_with_piper_audio(text: str, *, voice: str | None = None) -> TTSAudioResult:
    piper_exe = _PIPER_EXE
    effective_voice = (voice or _PIPER_VOICE).strip()

    if not piper_exe:
        raise TTSUnavailableError("Piper binary not found on PATH. Install piper-tts.")

    onnx_path, cfg_path = _resolve_model_path(effective_voice)

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name

        proc = subprocess.run(
            [
                piper_exe,
                "--model", str(onnx_path),
                "--config", str(cfg_path),
                "--length-scale", str(_PIPER_LENGTH_SCALE),
                "--sentence-silence", str(_PIPER_SENTENCE_SILENCE),
                "--volume", str(_PIPER_VOLUME),
                "--output_file", tmp_path,
            ],
            input=text.encode("utf-8"),
            capture_output=True,
            timeout=_PIPER_TIMEOUT_SECONDS,
        )

        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace").strip()
            raise TTSSynthesisError(f"Piper exited {proc.returncode}: {stderr}")

        with open(tmp_path, "rb") as handle:
            wav_bytes = handle.read()

        if not wav_bytes:
            return TTSAudioResult(provider="piper", sample_rate=_DEFAULT_SAMPLE_RATE, pcm_bytes=b"")
        return _decode_wav_bytes(wav_bytes, provider="piper")
    except subprocess.TimeoutExpired as exc:
        raise TTSSynthesisError(
            f"Piper timed out after {_PIPER_TIMEOUT_SECONDS:.1f} seconds. "
            f"Increase PIPER_TIMEOUT_SECONDS if this machine is under load."
        ) from exc
    except (OSError, TTSSynthesisError):
        raise
    except Exception as exc:
        raise TTSSynthesisError(f"TTS synthesis failed: {exc}") from exc
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass


def _synthesize_with_elevenlabs_audio(text: str, *, voice: str | None = None) -> TTSAudioResult:
    config = _current_elevenlabs_config()
    health = _elevenlabs_health(config)
    if health.get("status") != "ready":
        raise TTSUnavailableError(str(health.get("error") or "ElevenLabs is not configured."))

    effective_voice = (voice or str(config["voice_id"])).strip()
    if not effective_voice:
        raise TTSUnavailableError("ElevenLabs voice id is not configured.")

    query_parts = {
        "output_format": str(config["output_format"]),
    }
    if not bool(config["enable_logging"]):
        query_parts["enable_logging"] = "false"

    endpoint = (
        f"{str(config['api_base_url'])}/v1/text-to-speech/"
        f"{urllib_parse.quote(effective_voice)}?{urllib_parse.urlencode(query_parts)}"
    )

    payload: dict[str, object] = {"text": text}
    model_id = str(config["model_id"])
    if model_id:
        payload["model_id"] = model_id
    language_code = str(config["language_code"])
    if language_code:
        payload["language_code"] = language_code
    voice_settings = _build_elevenlabs_voice_settings(config)
    if voice_settings:
        payload["voice_settings"] = voice_settings

    request = urllib_request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "xi-api-key": str(config["api_key"]),
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib_request.urlopen(request, timeout=float(config["timeout_seconds"])) as response:
            audio_bytes = response.read()
    except urllib_error.HTTPError as exc:
        detail = _extract_http_error_message(exc)
        raise TTSSynthesisError(f"ElevenLabs request failed with {exc.code}: {detail}") from exc
    except urllib_error.URLError as exc:
        raise TTSUnavailableError(f"Could not reach ElevenLabs: {exc.reason}") from exc

    if not audio_bytes:
        raise TTSSynthesisError("ElevenLabs returned empty audio.")

    output_format = str(config["output_format"])

    if output_format.startswith("pcm_"):
        return TTSAudioResult(
            provider="elevenlabs",
            sample_rate=_parse_output_format_sample_rate(output_format),
            pcm_bytes=audio_bytes,
        )

    if output_format.startswith("wav_"):
        return _decode_wav_bytes(audio_bytes, provider="elevenlabs")

    raise TTSSynthesisError(
        "ElevenLabs output format must use PCM or WAV for this backend integration."
    )


def _synthesize_with_edge_audio(text: str, *, voice: str | None = None) -> TTSAudioResult:
    effective_voice = (voice or "en-US-JennyNeural").strip()
    try:
        import edge_tts
        import asyncio
        
        async def _run_edge():
            communicate = edge_tts.Communicate(text, effective_voice)
            audio_data = b""
            async for chunk in communicate.stream():
                if chunk["type"] == "audio":
                    audio_data += chunk["data"]
            return audio_data
            
        # Execute async code in a new event loop if necessary, but we are often in an async context already.
        # Wait, if we are in asyncio.to_thread, we don't have a running loop here.
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
        if loop.is_running():
            import threading
            def _thread_run():
                new_loop = asyncio.new_event_loop()
                asyncio.set_event_loop(new_loop)
                return new_loop.run_until_complete(_run_edge())
            
            thread_result = []
            def target():
                try:
                    thread_result.append(_thread_run())
                except Exception as e:
                    thread_result.append(e)
            t = threading.Thread(target=target)
            t.start()
            t.join()
            if isinstance(thread_result[0], Exception):
                raise thread_result[0]
            audio_bytes = thread_result[0]
        else:
            audio_bytes = loop.run_until_complete(_run_edge())
            
        if not audio_bytes:
            raise TTSSynthesisError("Edge-TTS returned empty audio.")
            
        return TTSAudioResult(
            provider="edge",
            sample_rate=24000,
            pcm_bytes=audio_bytes,
        )
    except ImportError:
        raise TTSUnavailableError("edge-tts library is not installed.")
    except Exception as exc:
        raise TTSSynthesisError(f"Edge-TTS synthesis failed: {exc}") from exc

def _provider_order() -> tuple[str, ...]:
    provider = _current_tts_provider()
    if provider == "edge":
        return ("edge", "elevenlabs", "piper")
    if provider == "elevenlabs":
        return ("elevenlabs", "edge", "piper")
    return ("piper", "edge")


def _generate_tts_audio(text: str, *, voice: str | None = None) -> TTSAudioResult | None:
    last_error: TTSSynthesisError | None = None

    for provider in _provider_order():
        try:
            if provider == "edge":
                return _synthesize_with_edge_audio(text, voice=voice)
            if provider == "elevenlabs":
                return _synthesize_with_elevenlabs_audio(text, voice=voice)
            return _synthesize_with_piper_audio(text, voice=voice)
        except TTSUnavailableError:
            continue
        except TTSSynthesisError as exc:
            last_error = exc
            continue

    if last_error is not None:
        raise last_error
    return None


# ---------------------------------------------------------------------------
# Public synthesis entry point
# ---------------------------------------------------------------------------

def synthesize(text: str, *, voice: str | None = None) -> bytes:
    """Synthesize text to WAV/MP3 audio bytes using the selected provider."""
    if not text or not text.strip():
        return _silent_wav()

    try:
        audio = _generate_tts_audio(text, voice=voice)
    except TTSUnavailableError:
        return _silent_wav()

    if audio is None or not audio.pcm_bytes:
        return _silent_wav(sample_rate=audio.sample_rate if audio is not None else _DEFAULT_SAMPLE_RATE)
        
    if audio.provider == "edge":
        return audio.pcm_bytes  # Returns MP3 directly for edge-tts
    return _pcm16le_to_wav_bytes(audio.pcm_bytes, sample_rate=audio.sample_rate)


def synthesize_chunks(
    text: str,
    *,
    voice: str | None = None,
    chunk_ms: int = 100,
    sample_rate: int = _DEFAULT_SAMPLE_RATE,
) -> tuple[int, list[bytes]]:
    """Synthesize text and return PCM chunks suitable for progressive playback."""
    chunk_ms = max(20, int(chunk_ms))
    if not text or not text.strip():
        return sample_rate, []

    audio = _generate_tts_audio(text, voice=voice)
    if audio is None:
        return sample_rate, []

    effective_sample_rate = audio.sample_rate or sample_rate
    chunk_size = max(2, effective_sample_rate * 2 * chunk_ms // 1000)
    chunks = [
        audio.pcm_bytes[index:index + chunk_size]
        for index in range(0, len(audio.pcm_bytes), chunk_size)
    ]
    return effective_sample_rate, chunks


def tts_health() -> dict[str, object]:
    """Return health/status dict for the TTS subsystem."""
    selected_provider = _current_tts_provider()
    provider_statuses = {
        "piper": _piper_health(),
        "elevenlabs": _elevenlabs_health(),
    }

    active_provider: str | None = None
    fallback_reason: str | None = None

    if selected_provider == "elevenlabs":
        if provider_statuses["elevenlabs"]["status"] == "ready":
            active_provider = "elevenlabs"
        elif provider_statuses["piper"]["status"] == "ready":
            active_provider = "piper"
            fallback_reason = str(provider_statuses["elevenlabs"].get("error") or "ElevenLabs is unavailable.")
    elif provider_statuses["piper"]["status"] == "ready":
        active_provider = "piper"

    response: dict[str, object] = {
        "status": "ready" if active_provider is not None else "unavailable",
        "selected_provider": selected_provider,
        "active_provider": active_provider,
        "providers": provider_statuses,
    }
    if fallback_reason:
        response["fallback_reason"] = fallback_reason

    if active_provider is not None:
        active_details = dict(provider_statuses[active_provider])
        for key, value in active_details.items():
            if key != "status":
                response.setdefault(key, value)

    return response
