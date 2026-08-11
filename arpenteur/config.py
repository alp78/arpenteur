"""Configuration: arpenteur.toml for what varies, .env for what is secret.

Two files rather than one because they have different lifetimes and different
audiences. arpenteur.toml is committed, reviewed, and describes the survey;
.env holds credentials and is never committed. Nothing in this module ever
prints a secret, including in exception messages.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path


def project_root() -> Path:
    """The repo root, found from this file rather than the cwd.

    The CLI has to work the same whether it is run from the repo, from a
    systemd unit with no working directory, or from a test.
    """
    return Path(__file__).resolve().parent.parent


def load_env(path: Path | None = None) -> dict[str, str]:
    """Read .env into a dict, and into os.environ if not already set.

    Deliberately minimal instead of pulling in python-dotenv: KEY=value,
    '#' comments, blank lines. Values already present in the real environment
    win, so a systemd unit or a CI secret overrides the file.
    """
    env_path = path or project_root() / ".env"
    values: dict[str, str] = {}
    if not env_path.is_file():
        return values
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        if not key:
            continue
        values[key] = value
        os.environ.setdefault(key, value)
    return values


@dataclass(frozen=True)
class ZefixConfig:
    base_url: str
    rate_limit: float
    max_retries: int
    contact: str
    user: str | None = None
    password: str | None = None

    @property
    def has_credentials(self) -> bool:
        return bool(self.user and self.password)


@dataclass(frozen=True)
class LlmConfig:
    provider: str
    classify_model: str
    draft_model: str
    batch_size: int
    enrich_model: str = ""
    enrich_max_searches: int = 4
    enrich_max_tokens: int = 8000
    # How the model is paid for. Declared rather than inferred: the two modes
    # draw on different accounts, and a deployment that meant "my Claude
    # subscription" must not quietly fall through to billing an API key that
    # happens to be in the environment.
    auth_mode: str = "auto"
    api_key: str | None = None

    @property
    def uses_api_key(self) -> bool:
        return self.auth_mode in ("api_key", "auto")

    @property
    def uses_subscription(self) -> bool:
        """True when a local `ant auth login` profile may be used.

        The SDK finds that profile itself, from ~/.config/anthropic. Nothing
        here reads it: the credential never passes through this process.
        """
        return self.auth_mode in ("subscription", "auto")


@dataclass(frozen=True)
class Settings:
    root: Path
    cantons: list[str]
    legal_forms: list[str]
    segments: dict[str, str]
    zefix: ZefixConfig
    llm: LlmConfig
    data_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        # frozen dataclass: the derived path is set through object.__setattr__
        object.__setattr__(self, "data_dir", self.root / "data")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "arpenteur.sqlite3"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "http-cache"


AUTH_MODES = ("auto", "api_key", "subscription")


def _auth_mode(value: object) -> str:
    """Validate llm.auth_mode at load time rather than at first model call.

    A typo here would otherwise read as "neither mode is available" and
    surface as a credential error hours into a run, pointing at the wrong
    thing entirely.
    """
    mode = str(value).strip().lower()
    if mode not in AUTH_MODES:
        raise RuntimeError(
            f"Unknown llm.auth_mode {value!r} in arpenteur.toml. "
            f"Expected one of: {', '.join(AUTH_MODES)}."
        )
    return mode


def load_settings(root: Path | None = None) -> Settings:
    base = root or project_root()
    load_env(base / ".env")

    with (base / "arpenteur.toml").open("rb") as handle:
        raw = tomllib.load(handle)

    survey = raw.get("survey", {})
    zefix_raw = raw.get("zefix", {})
    llm_raw = raw.get("llm", {})

    return Settings(
        root=base,
        cantons=list(survey.get("cantons", [])),
        legal_forms=list(survey.get("legal_forms", [])),
        segments=dict(raw.get("segments", {})),
        zefix=ZefixConfig(
            base_url=zefix_raw.get("base_url", ""),
            rate_limit=float(zefix_raw.get("rate_limit", 2.0)),
            max_retries=int(zefix_raw.get("max_retries", 4)),
            contact=zefix_raw.get("contact", "arpenteur"),
            user=os.environ.get("ZEFIX_USER") or None,
            password=os.environ.get("ZEFIX_PASSWORD") or None,
        ),
        llm=LlmConfig(
            provider=llm_raw.get("provider", "anthropic"),
            classify_model=llm_raw.get("classify_model", ""),
            draft_model=llm_raw.get("draft_model", ""),
            batch_size=int(llm_raw.get("batch_size", 20)),
            enrich_model=llm_raw.get("enrich_model", ""),
            enrich_max_searches=int(llm_raw.get("enrich_max_searches", 4)),
            enrich_max_tokens=int(llm_raw.get("enrich_max_tokens", 8000)),
            auth_mode=_auth_mode(llm_raw.get("auth_mode", "auto")),
            api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        ),
    )
