"""Model settings: which provider serves each role, and in what order.

Agents never name a model. They ask for a role (story, image, tts, ...) and get
an ordered chain of providers from `config/providers.yaml`; the first one whose
credentials are present is used, and the next is tried if it fails. Swapping a
model — or adding a paid one later — is a config change, not a code change.

    for spec in chain("image"):
        ...try spec.provider with spec.model / spec.params...

Environment overrides:
    PROVIDERS_FILE=/path/to/providers.yaml   use a different config
    PROVIDER_IMAGE=pollinations              force one provider for a role
    LLM_PROVIDER=gemini|groq|...|mock        legacy: forces all LLM roles
"""
from __future__ import annotations
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from shared.constants import ROOT_DIR
from shared.utils.logging import get_logger

log = get_logger("providers")

DEFAULT_CONFIG_PATH = ROOT_DIR / "config" / "providers.yaml"
LLM_ROLES = ("story", "edit_intent", "translate")


@dataclass(frozen=True)
class ProviderSpec:
    """One entry in a role's chain."""
    role: str
    provider: str
    model: Optional[str] = None
    params: Dict[str, Any] = field(default_factory=dict)
    requires: tuple = ()
    concurrency: int = 1

    @property
    def missing_env(self) -> List[str]:
        """Required env vars that aren't set ("A|B" means either will do)."""
        missing = []
        for requirement in self.requires:
            if not any(os.getenv(name.strip()) for name in str(requirement).split("|")):
                missing.append(str(requirement))
        return missing

    @property
    def available(self) -> bool:
        return not self.missing_env

    def __str__(self) -> str:
        return f"{self.provider}" + (f" ({self.model})" if self.model else "")


class ProviderConfig:
    def __init__(self, data: Dict[str, Any], source: Path | str = "<memory>"):
        self.source = str(source)
        self._roles: Dict[str, List[ProviderSpec]] = {}
        for role, entries in (data.get("roles") or {}).items():
            specs = []
            for entry in entries or []:
                requires = entry.get("requires") or []
                specs.append(ProviderSpec(
                    role=role,
                    provider=entry["provider"],
                    model=entry.get("model"),
                    params=dict(entry.get("params") or {}),
                    requires=tuple(requires if isinstance(requires, list) else [requires]),
                    concurrency=int(entry.get("concurrency", 1)),
                ))
            self._roles[role] = specs

    def roles(self) -> List[str]:
        return list(self._roles)

    def specs(self, role: str) -> List[ProviderSpec]:
        """Everything configured for a role, regardless of credentials or overrides."""
        return list(self._roles.get(role, []))

    def chain(self, role: str, include_unavailable: bool = False) -> List[ProviderSpec]:
        """Providers to try for `role`, best first, skipping ones missing credentials."""
        specs = self._roles.get(role, [])
        forced = self._forced_provider(role)
        if forced:
            specs = [s for s in specs if s.provider == forced] or \
                [ProviderSpec(role=role, provider=forced)]
        return specs if include_unavailable else [s for s in specs if s.available]

    def active(self, role: str) -> Optional[ProviderSpec]:
        """The provider a role will actually use right now."""
        chain = self.chain(role)
        return chain[0] if chain else None

    def concurrency(self, role: str) -> int:
        spec = self.active(role)
        return max(1, spec.concurrency) if spec else 1

    @staticmethod
    def _forced_provider(role: str) -> Optional[str]:
        override = os.getenv(f"PROVIDER_{role.upper()}")
        if override:
            return override.strip()
        legacy = os.getenv("LLM_PROVIDER")
        if legacy and role in LLM_ROLES:
            # "mock" used to mean "no LLM"; the offline provider is named per role.
            return "mymemory" if (legacy == "mock" and role == "translate") else legacy.strip()
        return None


_lock = threading.Lock()
_config: Optional[ProviderConfig] = None
_loaded_from: Optional[str] = None


def load(path: Optional[Path | str] = None, force: bool = False) -> ProviderConfig:
    """Load (and cache) the provider config."""
    global _config, _loaded_from
    target = str(path or os.getenv("PROVIDERS_FILE") or DEFAULT_CONFIG_PATH)
    with _lock:
        if _config is None or force or target != _loaded_from:
            data = yaml.safe_load(Path(target).read_text(encoding="utf-8")) or {}
            _config = ProviderConfig(data, source=target)
            _loaded_from = target
            log.info("provider config loaded from %s (%d roles)", target, len(_config.roles()))
    return _config


def chain(role: str, include_unavailable: bool = False) -> List[ProviderSpec]:
    return load().chain(role, include_unavailable=include_unavailable)


def active(role: str) -> Optional[ProviderSpec]:
    return load().active(role)


def concurrency(role: str) -> int:
    return load().concurrency(role)


def describe() -> Dict[str, List[Dict[str, Any]]]:
    """Every configured provider per role, with why it is or isn't usable.

    Shows the full list even when an override pins one provider, so
    `main.py providers` always says what the alternatives are waiting for.
    """
    cfg = load()
    out: Dict[str, List[Dict[str, Any]]] = {}
    for role in cfg.roles():
        forced = cfg._forced_provider(role)
        rows = []
        for spec in cfg.specs(role):
            missing = spec.missing_env
            if forced and spec.provider != forced:
                missing = missing or [f"overridden by {forced}"]
            rows.append({
                "provider": spec.provider,
                "model": spec.model,
                "available": not missing,
                "missing": missing,
            })
        out[role] = rows
    return out
