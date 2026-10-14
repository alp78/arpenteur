"""What the application needs before it is usable, as a list a page can draw.

Each check answers one question with one of three states: `ok`, `warn` or
`missing`, a sentence saying what was found, and the page where it is fixed.
The landing page draws the list; `arpenteur doctor` prints it. One function
feeds both, so the console and the terminal never disagree about what is
missing.

Two depths. The quick checks read settings and files and reach nothing: they
run on every Home render. The live checks sign in to the mailbox and open a
session with the mail server, sending nothing; they take seconds and run when
asked, from the doctor or from the page that offers it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import llm
from .kernel import db as kernel_db
from .kernel import modules as module_registry
from .kernel.db import Connection

STATES = ("ok", "warn", "missing")


@dataclass(frozen=True)
class Check:
    name: str
    state: str
    detail: str
    href: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "ok"


def _database(settings: Any, conn: Connection) -> list[Check]:
    enabled = [
        str(name) for name in (getattr(settings, "modules", None) or module_registry.DEFAULT)
    ]
    versions = kernel_db.versions(conn)
    behind = [name for name in enabled if name not in versions]
    return [
        Check("Database", "ok",
              f"Reached, with {len(versions)} module schema(s) in place.", "/runs"),
        Check(
            "Modules",
            "missing" if behind else "ok",
            (f"{len(behind)} enabled module(s) have no schema yet: "
             f"{', '.join(behind)}. Open any page or run any command to migrate.")
            if behind else f"{len(enabled)} enabled: {', '.join(enabled)}.",
            "/settings",
        ),
    ]


def _territory(settings: Any) -> Check:
    territory = getattr(settings, "territory", None)
    cantons = tuple(getattr(territory, "cantons", ()) or ())
    forms = tuple(getattr(territory, "legal_forms", ()) or ())
    if cantons and forms:
        return Check("Territory", "ok",
                     f"{', '.join(cantons)}, {len(forms)} legal form(s).", "/settings#territory")
    if cantons:
        return Check("Territory", "missing",
                     "Cantons are set but no legal form is: a sweep would find nothing.",
                     "/settings#territory")
    return Check("Territory", "missing",
                 "No canton is set: a sweep would run over the whole country.",
                 "/settings#territory")


def _register(settings: Any) -> Check:
    zefix = getattr(settings, "zefix", None)
    if zefix is not None and zefix.has_credentials:
        return Check("Register", "ok", "Credentials are set.", "/settings#secrets")
    return Check("Register", "missing",
                 "ZEFIX_USER and ZEFIX_PASSWORD are not set: the sweep and the "
                 "detail fetch refuse without them. They are free, by email to "
                 "zefix@bj.admin.ch.", "/settings#secrets")


def _model(settings: Any) -> Check:
    config = getattr(settings, "llm", None)
    if config is None:
        return Check("Model access", "missing", "No model settings.", "/settings#models")
    cli = llm._claude_cli()
    signed_in = bool(cli) and llm._has_local_profile()
    key = bool(getattr(config, "api_key", None))
    mode = getattr(config, "auth_mode", "subscription")
    if mode == "subscription":
        if signed_in:
            return Check("Model access", "ok", "The claude command is installed and "
                         "signed in; runs draw on the subscription.", "/settings#models")
        return Check("Model access", "missing",
                     "No Claude sign-in. Install the claude command and run it once "
                     "to sign in, or set an API key and choose it under Model access."
                     if not cli else
                     "The claude command is installed but not signed in. Run it "
                     "once and sign in.", "/settings#models")
    if mode == "api_key":
        if key:
            return Check("Model access", "ok", "ANTHROPIC_API_KEY is set; runs are "
                         "billed per call.", "/settings#models")
        return Check("Model access", "missing", "Model access is set to the API key and "
                     "ANTHROPIC_API_KEY is not set.", "/settings#models")
    if signed_in:
        return Check("Model access", "ok", "The subscription is signed in and is tried "
                     "first.", "/settings#models")
    if key:
        return Check("Model access", "ok", "No sign-in; ANTHROPIC_API_KEY is set and "
                     "will be used.", "/settings#models")
    return Check("Model access", "missing", "Neither a Claude sign-in nor an API key.",
                 "/settings#models")


def _brief(settings: Any) -> Check:
    from .stages import match

    try:
        text = match.load_brief(settings)
    except (RuntimeError, AttributeError, OSError):
        return Check("Scoring brief", "missing",
                     "No scoring brief: nothing can be scored until it is written.",
                     "/settings#scoring-brief")
    return Check("Scoring brief", "ok", f"{len(text.split())} words.",
                 "/settings#scoring-brief")


def _mailbox(settings: Any, *, live: bool) -> Check:
    mail = getattr(settings, "mail", None)
    if mail is None or not mail.configured:
        return Check("Mailbox", "warn",
                     "No mailbox: replies and bounces cannot be read, and drafts "
                     "refuse without an opt-out address. Set MAIL_IMAP_SERVER, "
                     "MAIL_USERNAME and MAIL_PASSWORD.", "/settings#secrets")
    if not live:
        return Check("Mailbox", "ok", f"{mail.user} at {mail.host}, not yet tried.",
                     "/settings#mailbox")
    from .sources import mail as mail_source

    try:
        with mail_source.Mailbox(mail.host, mail.port, user=mail.user,
                                 password=mail.password, timeout=15):
            pass
    except Exception as exc:  # noqa: BLE001 - every failure is a sentence here
        return Check("Mailbox", "missing", f"{mail.host} refused: {exc}", "/settings#secrets")
    return Check("Mailbox", "ok", f"Signed in as {mail.user} at {mail.host}.",
                 "/settings#mailbox")


def _sending(settings: Any, *, live: bool) -> Check:
    smtp = getattr(settings, "smtp", None)
    if smtp is None or smtp.missing():
        gaps = ", ".join(smtp.missing()) if smtp is not None else "every setting"
        return Check("Sending", "warn", f"A campaign cannot send: {gaps}.",
                     "/settings#secrets")
    if not live:
        return Check("Sending", "ok", f"From {smtp.sender} through {smtp.host}, "
                     "not yet tried.", "/settings#sending")
    from .sources import smtp as smtp_source

    try:
        with smtp_source.Sender(smtp.host, smtp.port, user=smtp.user,
                                password=smtp.password, security=smtp.security,
                                timeout=15) as session:
            answered = session.alive()
    except Exception as exc:  # noqa: BLE001
        return Check("Sending", "missing", f"{smtp.host} refused: {exc}", "/settings#secrets")
    if not answered:
        return Check("Sending", "missing", f"{smtp.host} opened a session and did not "
                     "answer a NOOP.", "/settings#secrets")
    return Check("Sending", "ok", f"Session opened with {smtp.host}; nothing was sent.",
                 "/settings#sending")


def _identity(settings: Any) -> Check:
    from .modules.sales import money

    issuer = getattr(settings, "issuer", None)
    if issuer is None or not issuer.name:
        return Check("Identity", "missing", "No name bills: an invoice cannot be issued.",
                     "/settings#identity")
    if not issuer.iban or not money.iban_is_valid(issuer.iban):
        return Check("Identity", "missing", f"{issuer.name} has no valid IBAN: an "
                     "invoice cannot carry a payment part.", "/settings#identity")
    return Check("Identity", "ok", f"{issuer.name}, with a valid IBAN.", "/settings#identity")


def _signature(settings: Any) -> Check:
    from .stages import send

    try:
        html = send.signature_html(settings)
    except Exception:  # noqa: BLE001
        html = ""
    if html.strip():
        return Check("Signature", "ok", "Written; appended to every message.", "/signature")
    return Check("Signature", "warn", "No signature is written; messages would go "
                 "out without one.", "/signature")


def _drafting(settings: Any) -> list[Check]:
    from .stages import draft

    enrol = getattr(settings, "enrol", None)
    if enrol is None:
        return []
    out: list[Check] = []
    try:
        text = draft.brief_text(settings)
        out.append(Check("Message brief", "ok", f"{len(text.split())} words.",
                         "/settings#enrolment"))
    except (RuntimeError, AttributeError, OSError) as exc:
        out.append(Check("Message brief", "warn", str(exc), "/settings#enrolment"))
    if enrol.include_in_draft and enrol.programme:
        absent: list[str] = []
        for language in ("fr", "de", "it"):
            try:
                draft.invitation(settings, language)
            except Exception as exc:  # noqa: BLE001
                absent.append(f"{language}: {exc}")
        if absent:
            out.append(Check("Invitations", "warn", " ".join(absent), "/settings#enrolment"))
        else:
            out.append(Check("Invitations", "ok", "One file per language, each readable.",
                             "/settings#enrolment"))
    return out


def _campaign_templates(settings: Any, conn: Connection) -> list[Check]:
    from pathlib import Path

    from .modules.clients import campaigns as campaign_store
    from .sources import template
    from .stages import campaign as campaign_stage

    out: list[Check] = []
    for row in campaign_store.campaigns(conn):
        if row["closed_at"]:
            continue
        try:
            folder = campaign_stage._templates_dir(settings, row["templates"])
            found = template.load(folder, root=Path(settings.root))
        except (ValueError, template.TemplateError) as exc:
            out.append(Check(f"Templates of {row['code']}", "missing", str(exc),
                             f"/campaign/{row['id']}"))
            continue
        if not found:
            out.append(Check(f"Templates of {row['code']}", "missing",
                             f"{row['templates']} holds no message.", f"/campaign/{row['id']}"))
        else:
            out.append(Check(f"Templates of {row['code']}", "ok",
                             f"{len(found)} message(s) in {row['templates']}.",
                             f"/campaign/{row['id']}"))
    return out


def checks(settings: Any, conn: Connection, *, live: bool = False) -> list[Check]:
    """The checklist, in the order a person sets things up."""
    enabled = {
        str(name) for name in (getattr(settings, "modules", None) or module_registry.DEFAULT)
    }
    out = _database(settings, conn)
    out.append(_territory(settings))
    out.append(_register(settings))
    out.append(_model(settings))
    out.append(_brief(settings))
    out.append(_mailbox(settings, live=live))
    out.append(_sending(settings, live=live))
    if "sales" in enabled:
        out.append(_identity(settings))
    out.append(_signature(settings))
    out.extend(_drafting(settings))
    if "clients" in enabled:
        out.extend(_campaign_templates(settings, conn))
    return out


def problems(found: list[Check]) -> list[Check]:
    return [check for check in found if not check.ok]


def report(found: list[Check]) -> str:
    """The checklist as lines for a terminal."""
    marks = {"ok": "ok ", "warn": "!  ", "missing": "!! "}
    lines = [
        f"{marks[check.state]} {check.name}: {check.detail}"
        + (f"  [{check.href}]" if check.href and not check.ok else "")
        for check in found
    ]
    short = [check for check in found if check.state == "missing"]
    lines.append(
        "Everything the first run needs is in place."
        if not short else f"{len(short)} thing(s) to set before the first run."
    )
    return "\n".join(lines)
