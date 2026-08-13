"""Configuration: arpenteur.toml for what varies, .env for what is secret.

Two files rather than one because they have different lifetimes and different
audiences. arpenteur.toml is committed, reviewed, and describes the survey;
.env holds credentials and is never committed. Nothing in this module ever
prints a secret, including in exception messages.
"""

from __future__ import annotations

import json
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
class MailConfig:
    """Where correspondence is read from. There is no send half, deliberately.

    The credentials are per device rather than per address at most Swiss
    hosts: Infomaniak issues one password per application, refuses the account
    password over IMAP, and never shows the generated one twice. That is a
    better fit for this than it first looks, since the credential Arpenteur
    holds can be revoked on its own without logging every other client out of
    the mailbox.
    """

    host: str = ""
    port: int = 993
    user: str = ""
    password: str = ""
    # Domains belonging to the operator, never matched on. Without this every
    # message matches whoever owns the mailbox: a website contact form sends
    # from the site's own domain, so the sender of an enquiry about a client is
    # frequently the operator themselves.
    own_domains: tuple[str, ...] = ()

    @property
    def configured(self) -> bool:
        return bool(self.host and self.user and self.password)

    @property
    def domains(self) -> frozenset[str]:
        """Own domains, including the one the mailbox address itself implies."""
        found = {d.strip().lower().lstrip("@") for d in self.own_domains if d.strip()}
        _, _, mine = self.user.rpartition("@")
        if mine:
            found.add(mine.lower())
        return frozenset(found)


@dataclass(frozen=True)
class LlmConfig:
    provider: str
    classify_model: str
    draft_model: str
    batch_size: int
    enrich_model: str = ""
    enrich_max_searches: int = 4
    enrich_max_tokens: int = 8000
    match_model: str = ""
    match_max_tokens: int = 4000
    # The advise stage returns a whole briefing rather than one judgement:
    # discussion points, services, prices and open questions. A truncated
    # structured reply is a hard failure rather than a short answer, and 8000
    # was measured overrunning on a full dossier with one document attached.
    # Thinking tokens count against the same ceiling, so raising effort means
    # raising this too.
    advise_model: str = ""
    advise_effort: str = ""
    advise_max_tokens: int = 32000
    # The prep stage extracts every term, acronym, technology and topic in a
    # client's file and writes the material to revise before the meeting. It is
    # recall and organisation rather than judgement, which is a different job
    # from arbitration and deserves a different model.
    prep_model: str = ""
    prep_effort: str = ""
    prep_max_tokens: int = 48000
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
    statuses: tuple[str, ...]
    segments: dict[str, str]
    signal_brief: str
    zefix: ZefixConfig
    llm: LlmConfig
    # What the work costs, in the operator's own words. Read by the advise
    # stage, which is forbidden to price anything this does not cover: a model
    # asked for a Swiss consulting day rate will produce a confident number
    # from nowhere, and the first time that number is repeated to a client it
    # becomes a commitment. Empty is a legitimate state, and the advice then
    # comes back with the work identified and no prices on it.
    rate_card: str = ""
    # Which modules this deployment runs, in the order they were listed. The
    # registry resolves dependencies; this is only what was asked for.
    modules: tuple[str, ...] = ()
    mail: MailConfig = field(default_factory=MailConfig)
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

    def status_clause(self, column: str = "c.status") -> tuple[str, list[str]]:
        """A SQL fragment restricting to workable companies, and its parameters.

        Shared rather than repeated so `profile` and `enrich` cannot disagree
        about what is worth spending on, which is the kind of drift nobody
        notices until a draft goes to a company in liquidation. An empty
        `statuses` means no restriction, for a survey that genuinely wants the
        whole register.
        """
        if not self.statuses:
            return "", []
        marks = ",".join(["%s"] * len(self.statuses))
        return f"{column} IN ({marks})", list(self.statuses)

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
    def database_url(self) -> str:
        """Where the system of record lives.

        DATABASE_URL wins, so a deployment names its own server without
        editing a committed file; otherwise the local development container
        from docker-compose.yml. Never a path: the store is PostgreSQL, and a
        setting called db_path outlived the file it used to point at.
        """
        return os.environ.get("DATABASE_URL") or (
            "postgresql://arpenteur:arpenteur@127.0.0.1:5433/arpenteur"
        )

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "http-cache"


# Register states a company may be in and still be worth working on. This is
# not a question of territory: a company that has been struck off stays in the
# database, because its departure is a fact and deleting the row would take
# its notices and any notes with it. It is a question of what the working
# stages should spend on. Measured on 11.08.2026: 11'121 of 183'392 companies
# were in liquidation or cancelled, 6% of the survey, and classifying them is
# cheap while researching one costs real money and writing to one would be
# embarrassing.
DEFAULT_STATUSES = ("ACTIVE",)

AUTH_MODES = ("auto", "api_key", "subscription")

# Used when enrich.signal_brief is absent. Deliberately generic: the tool has
# no idea what campaign it is serving, and a default that guessed would be
# silently wrong for every survey but one.
DEFAULT_SIGNAL_BRIEF = (
    "Anything on the site bearing on whether this company is worth "
    "approaching: what it is investing in, what it is hiring for, what it "
    "says it struggles with, and how it has changed recently."
)


EFFORTS = ("low", "medium", "high", "xhigh", "max")


def _effort(value: object) -> str:
    """Validate an effort level at load time, not at the first model call.

    Empty means the model's own default and is legitimate. A typo is not: a
    stage silently running at the default when the file says `xhigh` is exactly
    the kind of difference nobody notices until the answers are worse and the
    bill is smaller.
    """
    level = str(value or "").strip().lower()
    if level and level not in EFFORTS:
        raise ValueError(
            f"Unknown effort {level!r}. Expected one of: {', '.join(EFFORTS)}, "
            "or empty for the model's default."
        )
    return level


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


def _overlay(url: str) -> dict[str, Any]:
    """Operator settings, or nothing at all if the database is not reachable.

    Deliberately silent on every failure. Configuration must not become the
    reason the tool will not start: no server running, a database from before
    the table existed, or a half-written value should all leave the shipped
    defaults in place rather than raise. This is called at startup by every
    command, including on a machine where the container is simply not up.

    Its own short-lived connection rather than one borrowed from the store,
    because it runs before the store has been set up and must not depend on it.
    """
    try:
        import psycopg

        with psycopg.connect(url, connect_timeout=3) as conn:
            rows = conn.execute("SELECT key, value_json FROM intel.setting").fetchall()
    except Exception:  # noqa: BLE001 - see the docstring
        return {}
    # jsonb comes back already decoded, so there is nothing to parse. Trying to
    # anyway is worse than useless: `json.loads` on the decoded string value of
    # a JSON string raises, and a guard that skipped the key on failure made
    # every text setting — the signal brief, the rate card — silently vanish
    # while the numbers and the lists went through.
    return dict(rows)


def load_settings(root: Path | None = None) -> Settings:
    """The file, with whatever the operator changed in the console over it.

    arpenteur.toml keeps its comments and its defaults and stays the reference
    for anyone reading the repository. The overlay is this deployment's
    decisions, stored in the database because the most consequential choices
    in the tool should not be the ones you have to leave the application to
    make. See migrations/0013_settings.sql.

    Read directly rather than through db.open_db: this is called before the
    console exists and must not depend on it, and a database that is not there
    yet simply contributes nothing.
    """
    base = root or project_root()
    load_env(base / ".env")

    with (base / "arpenteur.toml").open("rb") as handle:
        raw = tomllib.load(handle)

    survey = dict(raw.get("survey", {}))
    overlay = _overlay(
        os.environ.get("DATABASE_URL")
        or "postgresql://arpenteur:arpenteur@127.0.0.1:5433/arpenteur"
    )
    for key in ("cantons", "legal_forms", "statuses", "admit_new"):
        if key in overlay:
            survey[key] = overlay[key]
            # A canton list chosen in the console is the whole answer, so a
            # region name left in the file must not narrow it afterwards.
            if key == "cantons":
                survey["region"] = None
    if "segments" in overlay:
        raw["segments"] = overlay["segments"]
    if "signal_brief" in overlay:
        raw.setdefault("enrich", {})["signal_brief"] = overlay["signal_brief"]
    if "rate_card" in overlay:
        raw.setdefault("rates", {})["card"] = overlay["rate_card"]
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
        statuses=tuple(
            str(v).strip().upper()
            for v in survey.get("statuses", DEFAULT_STATUSES)
            if str(v).strip()
        ),
        segments=dict(raw.get("segments", {})),
        # Collapsed to one line: it goes into a JSON Schema description, where
        # the newlines of a TOML block string are noise.
        signal_brief=" ".join(
            str((raw.get("enrich") or {}).get("signal_brief", DEFAULT_SIGNAL_BRIEF)).split()
        ),
        # Not collapsed, unlike the signal brief: this is a rate card and its
        # line structure is the whole of its meaning.
        rate_card=str((raw.get("rates") or {}).get("card", "")).strip(),
        modules=tuple(
            str(name).strip()
            for name in (raw.get("modules") or {}).get(
                "enabled", ("scout", "geo", "clients", "sales")
            )
            if str(name).strip()
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
            match_model=llm_raw.get("match_model", ""),
            match_max_tokens=int(llm_raw.get("match_max_tokens", 4000)),
            advise_model=llm_raw.get("advise_model", ""),
            advise_effort=_effort(llm_raw.get("advise_effort", "")),
            advise_max_tokens=int(llm_raw.get("advise_max_tokens", 32000)),
            prep_model=llm_raw.get("prep_model", ""),
            prep_effort=_effort(llm_raw.get("prep_effort", "")),
            prep_max_tokens=int(llm_raw.get("prep_max_tokens", 48000)),
            auth_mode=_auth_mode(llm_raw.get("auth_mode", "auto")),
            api_key=os.environ.get("ANTHROPIC_API_KEY") or None,
        ),
        mail=_mail(raw.get("mail") or {}),
    )


def _env(*names: str) -> str:
    """The first of several environment names that carries a value.

    Mail settings were first written into .env under the host's name, because
    that is how the credential is generated and where it is revoked. The
    generic names are what a second deployment on another host would use, and
    both are read so nobody has to rewrite a working file to upgrade.
    """
    for name in names:
        value = os.environ.get(name)
        if value:
            return value.strip()
    return ""


def _mail(raw: dict) -> MailConfig:
    return MailConfig(
        host=_env("MAIL_IMAP_SERVER", "INFOMANIAK_IMAP_SERVER"),
        port=int(_env("MAIL_IMAP_PORT", "INFOMANIAK_IMAP_PORT") or 993),
        user=_env("MAIL_USERNAME", "INFOMANIAK_USERNAME"),
        password=_env("MAIL_PASSWORD", "INFOMANIAK_PASSWORD"),
        own_domains=tuple(
            str(d).strip().lower() for d in (raw.get("own_domains") or []) if str(d).strip()
        ),
    )
