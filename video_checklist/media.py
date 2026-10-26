import json
from pathlib import Path
import subprocess

from .config import MAX_BYTES, MAX_DURATION_MS


def inspect_video(path: Path, filename: str) -> dict:
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("El vídeo supera 25 MiB. Recorta o comprime el archivo.")
    if Path(filename).suffix.lower() != ".mp4":
        raise ValueError("Selecciona un archivo MP4 con vídeo H.264.")
    try:
        result = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], capture_output=True, timeout=20)
        if result.returncode:
            raise ValueError("Archivo corrupto o no compatible. Exporta de nuevo como MP4 H.264.")
        info = json.loads(result.stdout)
        stream = next((s for s in info["streams"] if s.get("codec_type") == "video"), None)
        if not stream or stream.get("codec_name") != "h264" or "mp4" not in info["format"].get("format_name", ""):
            raise ValueError("El archivo debe contener vídeo H.264 en MP4.")
        duration_ms = round(float(info["format"]["duration"]) * 1000)
        if duration_ms <= 0 or duration_ms > MAX_DURATION_MS:
            raise ValueError("El vídeo debe durar entre 0 y 90 segundos. Recorta el archivo.")
        # Decode the complete short clip; a valid header alone does not prove valid media.
        decoded = subprocess.run(["ffmpeg", "-v", "error", "-xerror", "-i", str(path), "-map", "0:v:0", "-f", "null", "-"], capture_output=True, timeout=45)
        if decoded.returncode:
            raise ValueError("El vídeo contiene fotogramas corruptos. Exporta de nuevo el archivo.")
        return {"duration_ms": duration_ms, "size_bytes": path.stat().st_size, "codec": "h264"}
    except FileNotFoundError:
        raise ValueError("Falta ffmpeg/ffprobe en el backend. Instálalo siguiendo README.") from None
    except (subprocess.TimeoutExpired, KeyError, StopIteration, json.JSONDecodeError):
        raise ValueError("No se pudo validar el vídeo. Prueba un MP4 H.264 más corto.") from None
