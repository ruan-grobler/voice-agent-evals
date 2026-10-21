"""Where things live on disk. Every command takes a project root (default: cwd)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Project:
    root: Path

    @property
    def scenarios_dir(self) -> Path:
        return self.root / "scenarios"

    @property
    def businesses_dir(self) -> Path:
        return self.root / "businesses"

    @property
    def agents_dir(self) -> Path:
        return self.root / "agents"

    @property
    def cassettes_dir(self) -> Path:
        return self.root / "cassettes"

    @property
    def handcrafted_dir(self) -> Path:
        return self.root / "cassettes" / "handcrafted-src"

    @property
    def baselines_dir(self) -> Path:
        return self.root / "baselines"

    @property
    def rubric_path(self) -> Path:
        return self.root / "rubric" / "judge-rubric.md"

    @property
    def calibration_dir(self) -> Path:
        return self.root / "calibration"

    @property
    def reports_dir(self) -> Path:
        return self.root / "reports"

    def relative(self, path: Path) -> str:
        """Path relative to the project root, for stable, machine independent output."""
        try:
            return path.resolve().relative_to(self.root.resolve()).as_posix()
        except ValueError:
            return path.as_posix()
