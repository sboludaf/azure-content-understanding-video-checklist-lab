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
    # Explicit construction keeps the historical test harness isolated. Normal
    # startup always selects the Azure deployment profile below.
    profile: str = "legacy"

    def __post_init__(self):
        if self.mode != "azure":
            raise ValueError("La aplicación sólo admite análisis reales en Azure.")

    @classmethod
    def from_env(cls):
        mode = os.getenv("MVP_MODE", "azure")
        if mode != "azure":
            raise ValueError("La aplicación sólo admite análisis reales en Azure (MVP_MODE=azure).")
        profile = os.getenv("MVP_RUNTIME_PROFILE", "azd")
        if profile not in {"azd", "legacy"}:
            raise ValueError("MVP_RUNTIME_PROFILE debe ser azd o legacy")
        runtime = Path(os.getenv("MVP_RUNTIME_DIR", str(Path.home() / ".local/share/video-checklist"))).expanduser().resolve()
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
        return cls(mode, runtime, Path(sample).expanduser().resolve() if sample else None, role, provider, profile)
