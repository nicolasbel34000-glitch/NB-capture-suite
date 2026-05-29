from __future__ import annotations

import base64
import json
import re
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import requests

from .media import resolve_ffmpeg_bin


TranscriptionProvider = Literal["openai", "mistral", "local_whisper"]
ReviewProvider = Literal["openai", "mistral", "claude", "ollama"]


@dataclass(slots=True)
class SubtitleSegment:
    index: int
    start: float
    end: float
    text: str


@dataclass(frozen=True, slots=True)
class ProviderInfo:
    key: str
    label: str
    can_transcribe: bool
    can_review: bool
    requires_key: bool
    note: str


PROVIDERS: dict[str, ProviderInfo] = {
    "openai": ProviderInfo(
        "openai",
        "OpenAI",
        True,
        True,
        True,
        "Transcription audio et correction de sous-titres.",
    ),
    "mistral": ProviderInfo(
        "mistral",
        "Mistral Voxtral",
        True,
        True,
        True,
        "Transcription audio via Voxtral et correction de texte.",
    ),
    "google": ProviderInfo(
        "google",
        "Google Speech-to-Text",
        False,
        False,
        True,
        "Prevoyez une integration Google Cloud dediee: API key seule souvent insuffisante pour les longs fichiers.",
    ),
    "claude": ProviderInfo(
        "claude",
        "Claude",
        False,
        True,
        True,
        "Correction/formatage apres transcription; l'API Claude ne sert pas ici de moteur STT direct.",
    ),
    "ollama": ProviderInfo(
        "ollama",
        "Ollama local",
        False,
        True,
        False,
        "Correction locale apres transcription. Ne remplace pas Whisper pour ecouter l'audio.",
    ),
    "local_whisper": ProviderInfo(
        "local_whisper",
        "Whisper local",
        True,
        False,
        False,
        "Transcription locale optionnelle avec faster-whisper installe.",
    ),
}


def extract_audio(video_path: Path, destination: Path) -> Path:
    ffmpeg = resolve_ffmpeg_bin()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg introuvable. Installez ffmpeg ou placez-le dans bin/ffmpeg.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(destination),
    ]
    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
        check=False,
    )
    if completed.returncode != 0:
        details = "\n".join((completed.stdout or "").splitlines()[-20:])
        raise RuntimeError(f"Extraction audio echouee.\n{details}")
    return destination


def validate_audio_for_transcription(audio_path: Path) -> None:
    ffmpeg = resolve_ffmpeg_bin()
    if ffmpeg is None:
        return
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-i",
        str(audio_path),
        "-af",
        "volumedetect",
        "-f",
        "null",
        "-",
    ]
    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    output = completed.stdout or ""
    if "Audio:" not in output:
        raise RuntimeError("Aucune piste audio detectee dans cette video.")
    mean_match = re.search(r"mean_volume:\s*(-?inf|-?\d+(?:\.\d+)?)\s*dB", output)
    max_match = re.search(r"max_volume:\s*(-?inf|-?\d+(?:\.\d+)?)\s*dB", output)
    if not mean_match and not max_match:
        return
    mean_volume = _parse_db(mean_match.group(1)) if mean_match else None
    max_volume = _parse_db(max_match.group(1)) if max_match else None
    if max_volume is not None and max_volume <= -60:
        raise RuntimeError("Audio trop silencieux: aucune parole exploitable detectee.")
    if mean_volume is not None and mean_volume <= -55 and (max_volume is None or max_volume <= -35):
        raise RuntimeError("Audio trop faible: la transcription risquerait d'inventer du texte.")


def _parse_db(value: str) -> float:
    return -999.0 if value == "-inf" else float(value)


def burn_subtitles(video_path: Path, srt_path: Path, destination: Path) -> Path:
    ffmpeg = resolve_ffmpeg_bin()
    if ffmpeg is None:
        raise RuntimeError("ffmpeg introuvable. Installez ffmpeg ou placez-le dans bin/ffmpeg.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    subtitle_filter = f"subtitles={_ffmpeg_filter_path(srt_path)}"
    cmd = [
        ffmpeg,
        "-y",
        "-i",
        str(video_path),
        "-vf",
        subtitle_filter,
        "-c:a",
        "copy",
        str(destination),
    ]
    completed = subprocess.run(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
        check=False,
    )
    if completed.returncode != 0:
        details = "\n".join((completed.stdout or "").splitlines()[-20:])
        raise RuntimeError(f"Assemblage des sous-titres echoue.\n{details}")
    return destination


def _ffmpeg_filter_path(path: Path) -> str:
    escaped = path.resolve().as_posix().replace(":", r"\:").replace("'", r"\'")
    return f"'{escaped}'"


def transcribe_audio(
    audio_path: Path,
    *,
    provider: TranscriptionProvider,
    api_key: str = "",
    language: str = "fr",
    model: str = "",
) -> list[SubtitleSegment]:
    if provider == "openai":
        return _transcribe_openai(audio_path, api_key=api_key, language=language, model=model)
    if provider == "mistral":
        return _transcribe_mistral(audio_path, api_key=api_key, language=language, model=model)
    if provider == "local_whisper":
        return _transcribe_local_whisper(audio_path, language=language, model=model)
    raise ValueError(f"Fournisseur de transcription non supporte: {provider}")


def _transcribe_openai(audio_path: Path, *, api_key: str, language: str, model: str) -> list[SubtitleSegment]:
    if not api_key.strip():
        raise ValueError("Cle API OpenAI manquante.")
    selected_model = model.strip() or "whisper-1"
    if selected_model.startswith("gpt-4o"):
        data: list[tuple[str, str]] = [
            ("model", selected_model),
            ("language", language.strip() or "fr"),
            ("response_format", "json"),
        ]
    else:
        data = [
            ("model", selected_model),
            ("language", language.strip() or "fr"),
            ("response_format", "verbose_json"),
            ("timestamp_granularities[]", "segment"),
        ]
    with audio_path.open("rb") as handle:
        response = requests.post(
            "https://api.openai.com/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key.strip()}"},
            data=data,
            files={"file": (audio_path.name, handle, "audio/wav")},
            timeout=3600,
        )
    _raise_for_status(response)
    payload = response.json()
    return _segments_from_payload(payload)


def _transcribe_mistral(audio_path: Path, *, api_key: str, language: str, model: str) -> list[SubtitleSegment]:
    if not api_key.strip():
        raise ValueError("Cle API Mistral manquante.")
    selected_model = model.strip() or "voxtral-mini-latest"
    data: dict[str, Any] = {
        "model": selected_model,
        "timestamp_granularities": "segment",
    }
    if language.strip():
        data["language"] = language.strip()
    with audio_path.open("rb") as handle:
        response = requests.post(
            "https://api.mistral.ai/v1/audio/transcriptions",
            headers={"Authorization": f"Bearer {api_key.strip()}"},
            data=data,
            files={"file": (audio_path.name, handle, "audio/wav")},
            timeout=3600,
        )
    _raise_for_status(response)
    payload = response.json()
    return _segments_from_payload(payload)


def _transcribe_local_whisper(audio_path: Path, *, language: str, model: str) -> list[SubtitleSegment]:
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "faster-whisper n'est pas installe. Installez-le pour utiliser Whisper local."
        ) from exc
    selected_model = model.strip() or "base"
    whisper = WhisperModel(selected_model, device="cpu", compute_type="int8")
    segments, _info = whisper.transcribe(str(audio_path), language=language.strip() or None)
    return [
        SubtitleSegment(index=index, start=float(item.start), end=float(item.end), text=item.text.strip())
        for index, item in enumerate(segments, start=1)
    ]


def _segments_from_payload(payload: dict[str, Any]) -> list[SubtitleSegment]:
    raw_segments = payload.get("segments") or payload.get("chunks") or []
    segments: list[SubtitleSegment] = []
    for index, item in enumerate(raw_segments, start=1):
        start = item.get("start", item.get("timestamp", [0, 0])[0])
        end = item.get("end", item.get("timestamp", [0, 0])[1])
        text = str(item.get("text", "")).strip()
        if text and not _is_known_hallucination(text):
            segments.append(SubtitleSegment(index=index, start=float(start), end=float(end), text=text))
    if segments:
        return segments
    text = str(payload.get("text", "")).strip()
    if text and not _is_known_hallucination(text):
        return [SubtitleSegment(index=1, start=0.0, end=max(2.0, len(text) / 14), text=text)]
    raise RuntimeError(
        "Aucune parole fiable detectee. Le moteur a retourne seulement du texte parasite ou rien d'exploitable."
    )


def _is_known_hallucination(text: str) -> bool:
    cleaned = re.sub(r"\s+", " ", text.strip().lower())
    patterns = (
        "amara.org",
        "sous-titres realises",
        "sous-titres réalisés",
        "subtitles by",
        "captioning by",
        "voir une autre video",
        "voir une autre vidéo",
        "thanks for watching",
        "thank you for watching",
    )
    return any(pattern in cleaned for pattern in patterns)


def _raise_for_status(response: requests.Response) -> None:
    if response.ok:
        return
    message = response.text.strip()
    try:
        payload = response.json()
        error = payload.get("error", {})
        if isinstance(error, dict) and error.get("message"):
            message = str(error["message"])
    except ValueError:
        pass
    raise RuntimeError(f"Erreur API {response.status_code}: {message}")


def write_outputs(segments: list[SubtitleSegment], output_dir: Path, stem: str) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    paths = {
        "srt": output_dir / f"{stem}.srt",
        "txt": output_dir / f"{stem}.txt",
        "json": output_dir / f"{stem}.json",
    }
    paths["srt"].write_text(segments_to_srt(segments), encoding="utf-8")
    paths["txt"].write_text("\n".join(segment.text for segment in segments) + "\n", encoding="utf-8")
    paths["json"].write_text(
        json.dumps([asdict(segment) for segment in segments], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return paths


def segments_to_srt(segments: list[SubtitleSegment]) -> str:
    blocks: list[str] = []
    for index, segment in enumerate(segments, start=1):
        blocks.append(
            "\n".join(
                [
                    str(index),
                    f"{format_srt_time(segment.start)} --> {format_srt_time(segment.end)}",
                    segment.text.strip(),
                ]
            )
        )
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def parse_srt(text: str) -> list[SubtitleSegment]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    segments: list[SubtitleSegment] = []
    for block in normalized.split("\n\n"):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2 or "-->" not in lines[1]:
            continue
        start_text, end_text = [part.strip() for part in lines[1].split("-->", 1)]
        body = " ".join(lines[2:]).strip()
        segments.append(
            SubtitleSegment(
                index=len(segments) + 1,
                start=parse_srt_time(start_text),
                end=parse_srt_time(end_text),
                text=body,
            )
        )
    return segments


def format_srt_time(seconds: float) -> str:
    millis = max(0, int(round(seconds * 1000)))
    hours, remainder = divmod(millis, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def parse_srt_time(value: str) -> float:
    time_part, millis_part = value.split(",", 1)
    hours_text, minutes_text, seconds_text = time_part.split(":", 2)
    return (
        int(hours_text) * 3600
        + int(minutes_text) * 60
        + int(seconds_text)
        + int(millis_part[:3]) / 1000
    )


def review_srt_with_ollama(srt_text: str, *, model: str = "llama3.2:3b") -> str:
    prompt = (
        "Corrige uniquement l'orthographe et la ponctuation de ce fichier SRT. "
        "Garde exactement les index et les timestamps. Reponds seulement avec le SRT corrige.\n\n"
        f"{srt_text}"
    )
    response = requests.post(
        "http://localhost:11434/api/generate",
        json={"model": model, "prompt": prompt, "stream": False},
        timeout=3600,
    )
    response.raise_for_status()
    return str(response.json().get("response", "")).strip()


def encode_file_b64(path: Path) -> str:
    return base64.b64encode(path.read_bytes()).decode("ascii")
