#!/usr/bin/env python3
"""Prepare the frozen, licensed local video dataset. Never calls Azure.

Requires ffmpeg/ffprobe with libx264. Media must stay outside the checkout.
The frozen visual gold labels live in data/gold.json and are never generated here.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request

LAB_ID = "2026-10-15-content-understanding-video-checklist"
REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DIR = Path("/private/tmp/mvp-labs") / LAB_ID / "media"
SOURCE_URL = "https://upload.wikimedia.org/wikipedia/commons/c/c3/Tying_off_onto_the_middle_of_a_rope.webm"
SOURCE_SHA256 = "94fa9fff509b1d7c535309e57118a1c60ba52ab43baed63253c32d3a724d028f"
SOURCE_SIZE = 190825781
CLIPS = {
    "base": [(0, 37000)],
    "omitted": [(13000, 37000)],
    "reordered": [(13000, 15000), (0, 13000), (15000, 37000)],
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def command(original: Path, output: Path, segments: list[tuple[int, int]]) -> list[str]:
    filters = []
    for index, (start, end) in enumerate(segments):
        filters.append(
            f"[0:v]trim=start={start / 1000:g}:end={end / 1000:g},"
            f"setpts=PTS-STARTPTS[v{index}]"
        )
    inputs = "".join(f"[v{i}]" for i in range(len(segments)))
    filters.append(
        f"{inputs}concat=n={len(segments)}:v=1:a=0,"
        "scale=1280:720,fps=25[v]"
    )
    return [
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(original),
        "-filter_complex", ";".join(filters), "-map", "[v]", "-an",
        "-c:v", "libx264", "-preset", "medium", "-crf", "23",
        "-pix_fmt", "yuv420p", "-map_metadata", "-1",
        "-fflags", "+bitexact", "-flags:v", "+bitexact",
        "-movflags", "+faststart", str(output),
    ]


def probe(path: Path) -> dict:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration,size:stream=codec_name,codec_type,width,height,r_frame_rate",
         "-of", "json", str(path)], check=True, capture_output=True, text=True,
    )
    raw = json.loads(result.stdout)
    video = next(s for s in raw["streams"] if s["codec_type"] == "video")
    duration_ms = round(float(raw["format"]["duration"]) * 1000)
    size_bytes = int(raw["format"]["size"])
    if video["codec_name"] != "h264" or duration_ms > 90000 or size_bytes > 25 * 1024**2:
        raise RuntimeError("Prepared clip violates the frozen MVP input limits")
    return {
        "duration_ms": duration_ms, "size_bytes": size_bytes,
        "sha256": sha256(path), "codec": video["codec_name"],
        "width": video["width"], "height": video["height"],
        "frame_rate": video["r_frame_rate"],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DIR)
    parser.add_argument("--inspect", action="store_true", help="Also write 2 FPS contact sheets")
    args = parser.parse_args()
    output_dir = args.output_dir.expanduser().resolve()
    if output_dir == REPO_ROOT or REPO_ROOT in output_dir.parents or "botti-oro" in output_dir.parts:
        parser.error("Media must be outside the Git checkout and vault; choose a private temporary directory")
    for binary in ("ffmpeg", "ffprobe"):
        if shutil.which(binary) is None:
            parser.error(f"Missing {binary}; install ffmpeg with libx264 first")
    manifest = json.loads((REPO_ROOT / "data" / "manifest.json").read_text())
    output_dir.mkdir(parents=True, exist_ok=True)
    os.chmod(output_dir, 0o700)
    original = output_dir / "original.webm"
    if not original.exists():
        pending = output_dir / "original.webm.part"
        request = urllib.request.Request(SOURCE_URL, headers={"User-Agent": "MVP-Labs-local-data/1.0"})
        with urllib.request.urlopen(request, timeout=90) as source, pending.open("wb") as target:
            shutil.copyfileobj(source, target, length=1024 * 1024)
        pending.replace(original)
    if original.stat().st_size != SOURCE_SIZE or sha256(original) != SOURCE_SHA256:
        raise RuntimeError("Original SHA-256/size differs from the frozen licensed source; do not analyse")
    for clip_id, segments in CLIPS.items():
        output = output_dir / f"{clip_id}.mp4"
        expected = next(c for c in manifest["clips"] if c["clip_id"] == clip_id)
        if not output.exists() or sha256(output) != expected["sha256"]:
            pending = output_dir / f"{clip_id}.pending.mp4"
            subprocess.run(command(original, pending, segments), check=True)
            actual = probe(pending)
            if actual["sha256"] != expected["sha256"]:
                raise RuntimeError(
                    f"{clip_id}: encoder output differs from frozen bytes. "
                    f"Expected {expected['sha256']}, observed {actual['sha256']}. "
                    "Use the documented encoder version or reconcile the dataset before QA; "
                    "do not change gold to fit an AI result."
                )
            pending.replace(output)
        actual = probe(output)
        for field in ("sha256", "duration_ms", "size_bytes"):
            if actual[field] != expected[field]:
                raise RuntimeError(f"{clip_id}: manifest mismatch in {field}")
        if args.inspect:
            frames = output_dir / "frames"
            frames.mkdir(exist_ok=True)
            count = actual["duration_ms"] // 500
            rows = (count + 4) // 5
            subprocess.run([
                "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(output),
                "-vf", f"fps=2,scale=320:-1,tile=5x{rows}",
                "-frames:v", "1", "-update", "1", str(frames / f"{clip_id}-2fps.jpg"),
            ], check=True)
        print(json.dumps({"clip_id": clip_id, "path": str(output), **actual}))
    print("Frozen dataset verified. No Azure requests; visual gold remains unchanged.")


if __name__ == "__main__":
    main()
