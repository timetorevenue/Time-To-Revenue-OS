"""Local speech-to-text for the Website Manager's voice fallback (browsers without speech recognition).

Run with the dedicated speech venv (never the backend venv):
    ~/.local/share/ttros/stt-venv/bin/python tools/studio_transcribe.py <audio file> <model dir>
Prints one JSON object {text, language, duration, engine}. faster-whisper on CPU (int8), English,
no network (the model is a local folder), no provider, no cost. The audio file is read only.
Revisit: when the model or the speech venv changes. Last touched: 2026-11-23.
"""
import json
import os
import sys

os.environ.setdefault('HF_HUB_OFFLINE', '1')


def main() -> int:
    audio, model_dir = sys.argv[1], sys.argv[2]
    from faster_whisper import WhisperModel

    model = WhisperModel(model_dir, device='cpu', compute_type='int8', cpu_threads=4, local_files_only=True)
    segments, info = model.transcribe(audio, language='en', beam_size=1, vad_filter=True, condition_on_previous_text=False)
    text = ' '.join(s.text.strip() for s in segments).strip()
    print(json.dumps({'text': text, 'language': info.language, 'duration': round(info.duration, 2),
                      'engine': f'faster-whisper {os.path.basename(model_dir.rstrip("/"))} (local CPU)'}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
