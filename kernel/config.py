"""Environment-driven settings. Everything swappable stays env-driven (architecture principle 6)."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Principal:
    name: str
    key: str
    roles: tuple[str, ...] = ()
    trust: float = 1.0


@dataclass(frozen=True)
class Settings:
    # OpenAI-compatible LLM backend (interpreter, LLM decider)
    llmc_base_url: str = ""
    llmc_api_key: str = ""
    llmc_model: str = ""
    llmc_small_model: str = ""
    # Jev-compatible decision models (Typesafe AI System One)
    typesafe_api_key: str = ""
    typesafe_jev_model: str = "jev-latest"
    # Storage
    database_url: str = ""
    audit_backend: str = ""  # "" auto, "postgres", "memory"
    # Paths
    contracts_dir: Path = REPO_ROOT / "contracts"
    repo_root: Path = REPO_ROOT
    principals: tuple[Principal, ...] = field(default_factory=tuple)

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> Settings:
        env = dict(os.environ if env is None else env)
        principals_file = Path(env.get("LLMC_PRINCIPALS_FILE", REPO_ROOT / "kernel" / "principals.json"))
        principals: tuple[Principal, ...] = ()
        if principals_file.exists():
            raw = json.loads(principals_file.read_text())
            principals = tuple(
                Principal(
                    name=name,
                    key=str(p.get("key", "")),
                    roles=tuple(p.get("roles", ())),
                    trust=float(p.get("trust", 1.0)),
                )
                for name, p in raw.items()
            )
        return cls(
            llmc_base_url=env.get("LLMC_BASE_URL", ""),
            llmc_api_key=env.get("LLMC_API_KEY", ""),
            llmc_model=env.get("LLMC_MODEL", ""),
            llmc_small_model=env.get("LLMC_SMALL_MODEL", ""),
            typesafe_api_key=env.get("TYPESAFE_API_KEY", ""),
            typesafe_jev_model=env.get("TYPESAFE_JEV_MODEL", "jev-latest"),
            database_url=env.get("DATABASE_URL", ""),
            audit_backend=env.get("LLMC_AUDIT_BACKEND", ""),
            contracts_dir=Path(env.get("LLMC_CONTRACTS_DIR", REPO_ROOT / "contracts")),
            principals=principals,
        )

    @property
    def jev_enabled(self) -> bool:
        return bool(self.typesafe_api_key)

    @property
    def llm_enabled(self) -> bool:
        return bool(self.llmc_api_key and self.llmc_base_url)

    def principal_for_key(self, key: str) -> Principal | None:
        for p in self.principals:
            if p.key == key:
                return p
        return None
