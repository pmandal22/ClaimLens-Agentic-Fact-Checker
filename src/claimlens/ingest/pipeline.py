"""Orchestrate extraction, ASR/OCR (or Gemini video analysis), claims and verification.

The pipeline stores artifacts via the Storage interface under these keys:
- transcripts/{job_id}.txt
- ocr/{job_id}.txt  (de-duplicated on-screen text)
- claims/{job_id}.json
- evidence/{job_id}.json  (claim id -> evidence list)
- verdicts/{job_id}.json
- keyframes/{job_id}/frame_0001.jpg, ...

INGEST_PATH picks how text is read from the video:
- asr_ocr (default): faster-whisper on the audio, easyocr on sampled keyframes
- video_llm: one Gemini call returns the transcript and on-screen text (Gemini models only)
"""

import json
import logging
import tempfile
from collections.abc import Sequence
from pathlib import Path

from claimlens.config.settings import get_settings
from claimlens.domain.schemas import Claims, Verdict
from claimlens.graph.nodes.extract_claims import extract_claims
from claimlens.graph.verify.subgraph import verify_claim
from claimlens.ingest import asr, audio, keyframes, ocr, video_llm
from claimlens.services.storage import Storage

logger = logging.getLogger(__name__)


def _put_text(storage: Storage, tmp: Path, name: str, key: str, text: str) -> str:
    path = tmp / name
    path.write_text(text, encoding="utf-8")
    storage.put_file(path, key)
    return key


def process_video(
    job_id: str, local_video_path: Path, storage: Storage
) -> dict[str, Sequence[str]]:
    """Run the local pipeline and upload artifacts to storage.

    Returns a mapping of artifact types to storage keys written.
    """
    settings = get_settings()
    artifacts: dict[str, Sequence[str]] = {}
    with tempfile.TemporaryDirectory(prefix=f"claimlens-pipeline-{job_id}-") as tmpdir:
        tmp = Path(tmpdir)
        frames_dir = tmp / "frames"
        frames: Sequence[Path] = []

        if settings.ingest_path == "video_llm":
            # 1-2) Transcript and on-screen text from one Gemini call.
            video_text = video_llm.analyze_video(local_video_path)
            transcript, ocr_text = video_text.transcript, video_text.on_screen_text
            frames = keyframes.extract_keyframes(local_video_path, frames_dir)
        else:
            # 1) Extract audio, 2) transcribe
            audio_path = tmp / "audio.wav"
            audio.extract_audio(local_video_path, audio_path)
            transcript = asr.transcribe(audio_path)

            # 2b) OCR sampled keyframes. A failed OCR must not lose the spoken claims.
            frames = keyframes.extract_keyframes(local_video_path, frames_dir)
            try:
                ocr_text = ocr.ocr_frames(
                    frames,
                    settings.ocr_max_frames,
                    tuple(code.strip() for code in settings.ocr_languages.split(",")),
                )
            except Exception:
                logger.exception("job=%s OCR failed; continuing without on-screen text", job_id)
                ocr_text = ""

        artifacts["transcript"] = [
            _put_text(storage, tmp, "transcript.txt", f"transcripts/{job_id}.txt", transcript)
        ]
        artifacts["ocr"] = [_put_text(storage, tmp, "ocr.txt", f"ocr/{job_id}.txt", ocr_text)]

        # 3) Claims
        claims = Claims(claims=extract_claims(transcript, ocr_text=ocr_text))
        claims_file = tmp / "claims.json"
        claims_file.write_text(claims.model_dump_json(indent=2), encoding="utf-8")
        claims_key = f"claims/{job_id}.json"
        storage.put_file(claims_file, claims_key)
        artifacts["claims"] = [claims_key]

        # 4) Retrieve, rank, and judge evidence for each claim.
        evidence: dict[str, list[dict[str, object]]] = {}
        verdicts: list[Verdict] = []
        for claim in claims.claims:
            result = verify_claim.invoke({"claim": claim})
            evidence[claim.id] = [
                item.model_dump() for item in result.get("evidence", [])
            ]
            verdicts.extend(result.get("verdicts", []))
        evidence_file = tmp / "evidence.json"
        evidence_file.write_text(json.dumps(evidence, indent=2), encoding="utf-8")
        evidence_key = f"evidence/{job_id}.json"
        storage.put_file(evidence_file, evidence_key)
        artifacts["evidence"] = [evidence_key]

        verdicts_file = tmp / "verdicts.json"
        verdicts_file.write_text(
            json.dumps([verdict.model_dump() for verdict in verdicts], indent=2),
            encoding="utf-8",
        )
        verdicts_key = f"verdicts/{job_id}.json"
        storage.put_file(verdicts_file, verdicts_key)
        artifacts["verdicts"] = [verdicts_key]

        # 5) Upload keyframes
        keys = []
        for p in frames:
            key = f"keyframes/{job_id}/{p.name}"
            storage.put_file(p, key)
            keys.append(key)
        artifacts["keyframes"] = keys

    return artifacts
