from dataclasses import dataclass
import os
from pathlib import Path

LAB_ID = "2026-10-15-content-understanding-video-checklist"
PROPOSAL_HASH = "0f751b92278e7bb3994fcdfadf6761c100037f839bb1daf9641b5e7c12ee30b7"
ROOT = Path(__file__).resolve().parent.parent
MAX_BYTES = 25 * 1024 * 1024
MAX_DURATION_MS = 90_000


@dataclass
class Settings:
    mode: str
    runtime: Path
    sample: Path | None
    role: str
    provider: str = "auto"

    @classmethod
    def from_env(cls):
        mode = os.getenv("MVP_MODE", "fixture")
        if mode not in {"fixture", "azure"}:
            raise ValueError("MVP_MODE debe ser fixture o azure")
        runtime = Path(os.getenv("MVP_RUNTIME_DIR", str(Path.home() / "Library/Application Support/mvp-labs" / LAB_ID))).expanduser().resolve()
        if runtime == ROOT or ROOT in runtime.parents or "botti-oro" in runtime.parts:
            raise ValueError("MVP_RUNTIME_DIR debe estar fuera de Git y del vault")
        runtime.mkdir(parents=True, exist_ok=True, mode=0o700)
        runtime.chmod(0o700)
        sample = os.getenv("MVP_SAMPLE_PATH")
        role = os.getenv("MVP_ROLE", "implementer")
        if role not in {"implementer", "tester"}:
            raise ValueError("MVP_ROLE inválido")
        provider = os.getenv("MVP_AZURE_PROVIDER", "auto")
        if provider not in {"auto", "azure_content_understanding", "azure_foundry_responses"}:
            raise ValueError("MVP_AZURE_PROVIDER debe ser auto, azure_content_understanding o azure_foundry_responses")
        return cls(mode, runtime, Path(sample).expanduser().resolve() if sample else None, role, provider)
