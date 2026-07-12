from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.console import Console


@dataclass(slots=True)
class CliState:
    api_key: str | None = None
    key_name: str | None = None
    output_dir: Path | None = None
    json_output: bool = False
    verbose: bool = False
    site: str = "cn"
    console: Console | None = None

    @property
    def out(self) -> Console:
        if self.console is None:
            self.console = Console()
        return self.console
