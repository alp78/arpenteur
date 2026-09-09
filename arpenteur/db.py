"""The storage façade: one import for callers, module stores underneath.

The SQL now lives with the module that owns the schema —
`modules/scout/store.py` for `core` and `intel`, `modules/clients/store.py` for
`crm` — because a product whose data layer is one file cannot have a removable
sales module. This re-exports both under the name the rest of the codebase has
always used, so the stages, the console and the tests kept working through the
port instead of moving in one commit with everything else.

That makes this a transitional file, and it should shrink. New code imports the
module store it means; nothing new should be added here.

Two names are aliased rather than renamed at the call sites. The table is
`crm.interaction` now, because those rows are correspondence, calls, meetings
and decisions read three ways, and "event" reads as though only the diary
entries belonged. `record_event` and friends still point at it.
"""

from __future__ import annotations

from typing import Any

from .kernel import db as _kernel
from .kernel.db import Connection, Row, json_param, migrate, now, versions
from .modules.clients import store as _clients
from .modules.scout import store as _scout

# --- connecting ---------------------------------------------------------------


def connect(url: str | None = None) -> Connection:
    return _kernel.connect(url)


# Databases this process has already brought up to date. Migration is
# idempotent, but checking costs a query per module per call and `open_db` is
# called once per request and once per test; a set is cheaper than being
# thorough about something that cannot have changed underneath us.
_MIGRATED: set[str] = set()


def open_db(url: str | None = None) -> Connection:
    """A connection to a database that is migrated and ready.

    Migrations run here, as they did before, so no command has to remember to
    apply them. Which modules are enabled decides which schemas exist; the
    registry resolves that, and the kernel applies each module's files in
    dependency order.
    """
    from .kernel import modules

    conn = _kernel.connect(url)
    key = _kernel.dsn(url)
    if key not in _MIGRATED:
        _kernel.ensure_extensions(conn)
        _kernel.migrate(conn, modules.load(modules.DEFAULT).migration_dirs())
        _MIGRATED.add(key)
    return conn


# --- the scout ----------------------------------------------------------------

HASHED_FIELDS = _scout.HASHED_FIELDS
SITE_JUDGED_FIELDS = _scout.SITE_JUDGED_FIELDS
RESTRICTED_SITE_COLUMNS = _scout.RESTRICTED_SITE_COLUMNS
PROSPECT_STATES = _scout.PROSPECT_STATES
SETTABLE = _scout.SETTABLE
CATALOGUE_KEY = _scout.CATALOGUE_KEY

company_content_hash = _scout.company_content_hash
site_digest = _scout.site_digest
upsert_company = _scout.upsert_company
company = _scout.company
company_by_uid = _scout.company_by_uid
mark_stale = _scout.mark_stale
clear_stale = _scout.clear_stale
synced_days = _scout.synced_days
last_synced_day = _scout.last_synced_day
record_sync_day = _scout.record_sync_day
record_sweep = _scout.record_sweep
completed_prefixes = _scout.completed_prefixes
save_segments = _scout.save_segments
set_segment_manually = _scout.set_segment_manually
segments_for = _scout.segments_for
save_sector = _scout.save_sector
set_sector_manually = _scout.set_sector_manually
sector_for = _scout.sector_for
sector_counts = _scout.sector_counts
save_fit = _scout.save_fit
save_source_document = _scout.save_source_document
observe = _scout.observe
supersede = _scout.supersede
observations_for = _scout.observations_for
disagreements = _scout.disagreements
save_weight_set = _scout.save_weight_set
activate_weight_set = _scout.activate_weight_set
active_weights = _scout.active_weights
weight_sets = _scout.weight_sets
save_score_run = _scout.save_score_run
score_run = _scout.score_run
latest_score = _scout.latest_score
score_history = _scout.score_history
components_of = _scout.components_of
evidence_of = _scout.evidence_of
score_with_parts = _scout.score_with_parts
record_selection = _scout.record_selection
board_overlap = _scout.board_overlap
set_fit_manually = _scout.set_fit_manually
fit_for = _scout.fit_for
add_note = _scout.add_note
notes_for = _scout.notes_for
set_prospect = _scout.set_prospect
record_state_change = _scout.record_state_change
state_history = _scout.state_history
prospect_for = _scout.prospect_for
save_publications = _scout.save_publications
publications_for = _scout.publications_for
save_people = _scout.save_people
prune_people = _scout.prune_people
people_for = _scout.people_for
save_site = _scout.save_site
site_for = _scout.site_for
site_for_draft = _scout.site_for_draft
save_contact = _scout.save_contact
contacts_for = _scout.contacts_for
contact = _scout.contact
toggle_suppressed = _scout.toggle_suppressed
mark_reached = _scout.mark_reached
start_run = _scout.start_run
append_line = _scout.append_line
lines_after = _scout.lines_after
finish_run = _scout.finish_run
runs = _scout.runs
run = _scout.run
close_orphans = _scout.close_orphans
settings_overlay = _scout.settings_overlay
save_setting = _scout.save_setting
setting_history = _scout.setting_history
save_catalogue = _scout.save_catalogue
catalogue = _scout.catalogue

# --- clients ------------------------------------------------------------------

CLIENT_STATES = _clients.CLIENT_STATES
PROJECT_STATES = _clients.PROJECT_STATES
EVENT_KINDS = _clients.INTERACTION_KINDS
EVENT_STATES = _clients.INTERACTION_STATES
APPOINTMENT_KINDS = _clients.APPOINTMENT_KINDS

make_code = _clients.make_code
promote_to_client = _clients.promote_to_client
update_client = _clients.update_client
client = _clients.client
client_for_company = _clients.client_for_company
clients = _clients.clients
add_project = _clients.add_project
update_project = _clients.update_project
project = _clients.project
projects_for = _clients.projects_for
record_event = _clients.record_interaction
set_event_state = _clients.set_interaction_state
event = _clients.interaction
delete_event = _clients.delete_interaction
delete_project = _clients.delete_project
delete_attachment = _clients.delete_attachment
sha_is_orphaned = _clients.sha_is_orphaned
timeline = _clients.timeline
diary = _clients.diary
upcoming = _clients.upcoming
save_dossier_section = _clients.save_dossier_section
dossier_for = _clients.dossier_for
save_approach = _clients.save_approach
approaches_for = _clients.approaches_for
accept_approach = _clients.accept_approach
add_attachment = _clients.add_attachment
attachments_for = _clients.attachments_for
attachment = _clients.attachment
add_folder = _clients.add_folder
folders_for = _clients.folders_for
folder = _clients.folder
rename_folder = _clients.rename_folder
file_attachment = _clients.file_attachment
delete_folder = _clients.delete_folder
save_briefing = _clients.save_briefing
briefings_for = _clients.briefings_for
save_terms = _clients.save_terms
glossary_for = _clients.glossary_for
known_terms = _clients.known_terms
set_term_known = _clients.set_term_known
term_owner = _clients.term_owner


def delete_term(conn: Connection, term_id: int) -> int | None:
    """Remove a term. No tombstone: a glossary is a study aid rather than a
    record of what happened, and "removed: MX" would be noise in a timeline."""
    owner = _clients.term_owner(conn, term_id)
    if owner is None:
        return None
    conn.execute("DELETE FROM crm.glossary WHERE id = %s", (term_id,))
    return owner


__all__ = [name for name in dir() if not name.startswith("_")]
