"""Configuration: arpenteur.toml for what varies, .env for what is secret.

Two files rather than one because they have different lifetimes and different
audiences. arpenteur.toml is committed, reviewed, and describes the survey;
.env holds credentials and is never committed. Nothing in this module ever
prints a secret, including in exception messages.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, replace
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
class Territory:
    """Where the survey applies: a set of cantons and a set of legal forms.

    One object rather than two loose lists because every stage asks the same
    question of it, and they must all get the same answer. The sweep asks what
    to fetch; the sync stage asks whether a company appearing in the national
    feed belongs here. If those two drifted apart, a canton would be swept and
    then never kept current, or kept current and never swept.
    """

    cantons: tuple[str, ...]
    legal_forms: tuple[str, ...]
    name: str = "custom"

    def contains(self, canton: str | None, legal_form_uid: str | None) -> bool:
        """Is this company inside the survey?

        An empty list means no restriction on that axis, so a territory of all
        Switzerland needs no canton list and a survey interested in every form
        needs no form list.
        """
        if self.cantons and (canton or "").upper() not in self.cantons:
            return False
        if self.legal_forms and (legal_form_uid or "") not in self.legal_forms:
            return False
        return True

    def describe(self) -> str:
        where = ", ".join(self.cantons) if self.cantons else "all cantons"
        return f"{self.name} ({where})"


def resolve_territory(
    *,
    regions: dict[str, list[str]],
    region: str | None,
    cantons: list[str] | None,
    legal_forms: list[str],
) -> Territory:
    """Turn config and command line into one territory.

    An unknown region name raises rather than resolving to nothing: a typo
    would otherwise produce an empty canton list, which `contains` reads as
    "no restriction" and would quietly widen the survey to all of Switzerland.
    """
    chosen = [c.strip().upper() for c in (cantons or []) if c.strip()]
    name = "custom"
    if not chosen and region:
        key = region.strip().lower()
        if key not in regions:
            raise RuntimeError(
                f"Unknown region {region!r}. Defined in arpenteur.toml: "
                f"{', '.join(sorted(regions)) or '(none)'}."
            )
        chosen = [c.strip().upper() for c in regions[key]]
        name = key
    elif chosen:
        # Name it after a region whose canton set matches exactly, so logs
        # read "romandie" rather than a list of five.
        for candidate, members in regions.items():
            if sorted(c.upper() for c in members) == sorted(chosen):
                name = candidate
                break
    return Territory(
        cantons=tuple(chosen),
        legal_forms=tuple(str(f).strip() for f in legal_forms if str(f).strip()),
        name=name,
    )


@dataclass(frozen=True)
class Settings:
    root: Path
    territory: Territory
    regions: dict[str, list[str]]
    admit_new: bool
    segments: dict[str, str]
    signal_brief: str
    zefix: ZefixConfig
    llm: LlmConfig
    data_dir: Path = field(init=False)

    def __post_init__(self) -> None:
        # frozen dataclass: the derived path is set through object.__setattr__
        object.__setattr__(self, "data_dir", self.root / "data")

    # Kept so the stages can go on asking for a plain list where that is all
    # they need, without every call site reaching through to .territory.
    @property
    def cantons(self) -> list[str]:
        return list(self.territory.cantons)

    @property
    def legal_forms(self) -> list[str]:
        return list(self.territory.legal_forms)

    def scoped(
        self, *, region: str | None = None, cantons: list[str] | None = None,
        legal_forms: list[str] | None = None,
    ) -> "Settings":
        """The same settings over a different territory.

        How a --region or --canton flag takes effect: one object is narrowed
        once, at the edge, and every stage below sees a consistent territory
        rather than each deciding for itself what the flags meant.
        """
        if region is None and not cantons and not legal_forms:
            return self
        narrowed = resolve_territory(
            regions=self.regions,
            region=region,
            cantons=cantons,
            legal_forms=legal_forms or list(self.territory.legal_forms),
        )
        if region is None and not cantons:
            # Only the forms were narrowed; keep the territory's own cantons
            # rather than letting an empty list read as "all of Switzerland".
            narrowed = Territory(
                cantons=self.territory.cantons,
                legal_forms=narrowed.legal_forms,
                name=self.territory.name,
            )
        return replace(self, territory=narrowed)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "arpenteur.sqlite3"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "http-cache"


AUTH_MODES = ("auto", "api_key", "subscription")

# Used when enrich.signal_brief is absent. Deliberately generic: the tool has
# no idea what campaign it is serving, and a default that guessed would be
# silently wrong for every survey but one.
DEFAULT_SIGNAL_BRIEF = (
    "Anything on the site bearing on whether this company is worth "
    "approaching: what it is investing in, what it is hiring for, what it "
    "says it struggles with, and how it has changed recently."
)


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
    regions = {
        str(name).strip().lower(): [str(c).strip().upper() for c in members]
        for name, members in (raw.get("regions") or {}).items()
    }

    return Settings(
        root=base,
        territory=resolve_territory(
            regions=regions,
            region=survey.get("region"),
            cantons=list(survey.get("cantons", [])),
            legal_forms=list(survey.get("legal_forms", [])),
        ),
        regions=regions,
        admit_new=bool(survey.get("admit_new", True)),
        segments=dict(raw.get("segments", {})),
        # Collapsed to one line: it goes into a JSON Schema description, where
        # the newlines of a TOML block string are noise.
        signal_brief=" ".join(
            str((raw.get("enrich") or {}).get("signal_brief", DEFAULT_SIGNAL_BRIEF)).split()
        ),
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
