from dataclasses import dataclass
from pathlib import Path
import os


@dataclass(frozen=True)
class Settings:
    control: Path
    artifacts: Path
    checkpoints: Path
    staging: Path
    migration_failed: bool = False
    secrets: Path | None = None
    inspections: Path | None = None
    inspections_host: str | None = None

    @property
    def database(self) -> Path:
        return self.control / "control.sqlite3"

    @classmethod
    def from_environment(cls):
        # Explicit allowlist: no model keys or arbitrary Docker arguments are read.
        return cls(*(Path(os.environ.get(f"RESEARCH_{key.upper()}_DIR", f"/data/{key}"))
                     for key in ("control", "artifacts", "checkpoints", "staging")),
                   migration_failed=os.environ.get("RESEARCH_MIGRATION_FAILED") == "1",
                   secrets=Path(os.environ.get('RESEARCH_SECRETS_DIR', '/data/secrets')),
                   inspections=Path(os.environ['RESEARCH_INSPECTIONS_DIR']) if os.environ.get('RESEARCH_INSPECTIONS_DIR') else None,
                   inspections_host=os.environ.get('RESEARCH_INSPECTIONS_HOST'))
