"""Local speech processing shared by David's Dashboard and Telegram transports."""

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import wave
from pathlib import Path

MAX_BYTES = 10 * 1024 * 1024
MAX_SECONDS = 120
MAX_SPOKEN_CHARS = 900
MODELS = Path.home() / ".local/share/ttros/models"
SPEECH_BIN = MODELS.parent / "stt-venv/bin"
STT_SCRIPT = Path(__file__).with_name("studio_transcribe.py")
VOICE = MODELS / "piper/en_US-norman-medium.onnx"
FORMATS = {
    ".ogg": "ogg", ".oga": "ogg", ".opus": "ogg", ".mp3": "mp3", ".wav": "wav",
    ".flac": "flac", ".m4a": "mov", ".mp4": "mov", ".webm": "matroska,webm", ".aac": "aac",
}
MIME_SUFFIX = {
    "audio/ogg": ".ogg", "application/ogg": ".ogg", "audio/opus": ".opus",
    "audio/mpeg": ".mp3", "audio/mp3": ".mp3", "audio/wav": ".wav", "audio/x-wav": ".wav",
    "audio/flac": ".flac", "audio/x-flac": ".flac", "audio/mp4": ".m4a",
    "audio/x-m4a": ".m4a", "audio/m4a": ".m4a", "audio/webm": ".webm", "audio/aac": ".aac",
}


class SpeechError(RuntimeError):
    """A safe operator-facing speech error."""


def telegram_audio(message):
    """Return bounded metadata, including a replyable error for unsupported audio."""
    document = message.get("document") or {}
    voice = message.get("voice")
    mime = str(document.get("mime_type") or "").lower().split(";")[0].strip()
    ext = Path(str(document.get("file_name") or "")).suffix.lower()
    if voice or message.get("audio"):
        media = voice or message["audio"]
        mime = str(media.get("mime_type") or "").lower().split(";")[0].strip()
        suffix = MIME_SUFFIX.get(mime) or (".ogg" if voice and not mime else "")
        if not suffix and not mime:
            suffix = Path(str(media.get("file_name") or "")).suffix.lower()
    elif mime.startswith("audio/") or mime == "application/ogg" or ext in (FORMATS.keys() - {".mp4"}):
        media = document
        suffix = MIME_SUFFIX.get(mime) or (ext if mime in {"", "application/octet-stream"} else "")
    else:
        return None
    if suffix not in FORMATS:
        return {"error": "Unsupported audio. Send a voice note or an Ogg, MP3, WAV, FLAC, M4A, WebM or AAC file."}
    size, duration = media.get("file_size", 0), media.get("duration", 0)
    if (type(size) is not int or type(duration) is not int
            or not 0 <= size <= MAX_BYTES or not 0 <= duration <= MAX_SECONDS):
        return {"error": "Audio must be at most two minutes and 10 MB."}
    file_id = media.get("file_id")
    if not isinstance(file_id, str) or not file_id or len(file_id) > 1024:
        return {"error": "Telegram did not supply a usable audio attachment."}
    return {"file_id": file_id, "suffix": suffix}


def _run(argv, directory, *, timeout, failure, stdin=None):
    try:
        return subprocess.run(
            argv, cwd=directory, input=stdin if stdin is not None else "", capture_output=True,
            text=True, encoding="utf-8", timeout=timeout, check=True,
            env={"PATH": os.defpath, "HOME": str(directory), "HF_HUB_OFFLINE": "1",
                 "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "1"},
        ).stdout
    except subprocess.TimeoutExpired:
        raise SpeechError(f"{failure} The local process timed out.") from None
    except (OSError, subprocess.CalledProcessError, UnicodeError):
        raise SpeechError(failure) from None


def available():
    ffmpeg = shutil.which("ffmpeg", path=os.defpath)
    python, piper = SPEECH_BIN / "python", SPEECH_BIN / "piper"
    return {
        "stt": bool(ffmpeg and os.access(python, os.X_OK) and STT_SCRIPT.is_file()
                    and (MODELS / "whisper/base.en/model.bin").is_file()),
        "tts": bool(ffmpeg and os.access(piper, os.X_OK) and VOICE.is_file()
                    and VOICE.with_suffix(".onnx.json").is_file()),
    }


def transcribe(data, suffix):
    started = time.monotonic()
    if suffix not in FORMATS or not data or len(data) > MAX_BYTES:
        raise SpeechError("The audio is empty, unsupported or exceeds 10 MB.")
    if not available()["stt"]:
        raise SpeechError("Local English transcription is unavailable.")
    with tempfile.TemporaryDirectory(prefix="david-stt-") as directory:
        source = Path(directory) / ("input" + suffix)
        pcm = Path(directory) / "speech.wav"
        source.write_bytes(data)
        _run([shutil.which("ffmpeg", path=os.defpath), "-nostdin", "-v", "error",
              "-protocol_whitelist", "file", "-format_whitelist", FORMATS[suffix],
              "-threads", "2", "-i", str(source), "-map", "0:a:0", "-vn", "-sn", "-dn",
              "-t", str(MAX_SECONDS + 1), "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(pcm)],
             directory, timeout=20, failure="The audio could not be decoded.")
        try:
            with wave.open(str(pcm)) as wav:
                if wav.getnframes() > MAX_SECONDS * wav.getframerate():
                    raise SpeechError("Audio must be at most two minutes.")
        except (OSError, EOFError, wave.Error):
            raise SpeechError("The audio could not be decoded.") from None
        output = _run([str(SPEECH_BIN / "python"), "-I", str(STT_SCRIPT), str(pcm),
                       str(MODELS / "whisper/base.en")], directory, timeout=90,
                      failure="Local English transcription failed.")
        try:
            result = json.loads(output)
            text = result["text"].strip()
            duration = round(float(result["duration"]), 2)
        except (ValueError, KeyError, TypeError, AttributeError):
            raise SpeechError("The transcription engine returned an invalid result.") from None
    if not text:
        raise SpeechError("I couldn’t detect speech in that recording.")
    if len(text.encode("utf-8")) > 8000:
        raise SpeechError("The transcription is too long. Please split the request across recordings.")
    return text, {"engine": "faster-whisper base.en CPU int8", "audio_seconds": duration,
                  "seconds": round(time.monotonic() - started, 3)}


def spoken_text(text):
    """Speak a bounded excerpt and retain written details where needed."""
    plain = re.sub(r"\[([^\]\n]+)\]\((?:https?://|/api/david/artifacts/)[^\s)]+\)", r"\1", text)
    plain = re.sub(r"https?://\S+|/api/david/artifacts/\S+|business_brain:[^\n\x60<>\[\]()]"
                   r"*?\.(?:md|json|yaml|yml|txt|csv|pdf|docx|html)\b", "", plain)
    keep_text = plain != text
    plain = re.sub(r"^\s*(?:[#>]+|[-*])\s+", "", plain, flags=re.M)
    plain = re.sub(r"[\x60*_]", "", plain)
    # Piper uses double square brackets for raw phonemes.
    plain = " ".join(plain.replace("[", "").replace("]", "").split())
    if len(plain) > MAX_SPOKEN_CHARS:
        start = plain[:MAX_SPOKEN_CHARS]
        end = max(start.rfind(". "), start.rfind("! "), start.rfind("? "))
        plain = start[:end + 1] if end >= MAX_SPOKEN_CHARS // 2 else start.rsplit(" ", 1)[0] + "."
        keep_text = True
    if keep_text and plain:
        plain += " See the written reply for details."
    return plain, keep_text


def synthesize(text, output):
    started = time.monotonic()
    if not available()["tts"]:
        raise SpeechError("Local speech synthesis is unavailable.")
    wav = output.with_suffix(".wav")
    _run([str(SPEECH_BIN / "piper"), "-m", str(VOICE), "-f", str(wav)], output.parent,
         stdin=text, timeout=25, failure="Local speech synthesis failed.")
    if output != wav:
        _run([shutil.which("ffmpeg", path=os.defpath), "-nostdin", "-v", "error", "-i", str(wav),
              "-c:a", "libopus", "-b:a", "32k", "-f", "ogg", str(output)],
             output.parent, timeout=10, failure="The spoken reply could not be encoded.")
    if not 0 < output.stat().st_size <= MAX_BYTES:
        raise SpeechError("The spoken reply has an invalid size.")
    return {"engine": "Piper Norman medium CPU", "seconds": round(time.monotonic() - started, 3)}
