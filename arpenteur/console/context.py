"""What a module's routes need from the application, as one object.

`create_app` builds a `Console` once and hands it to every route file under
`routes/`. The routes close over it rather than over `create_app`'s locals, so
a module's routes can live in their own file and still share the one runner,
the one registry and the one way of drawing a page.

`settings` is the live copy: `reload()` rebinds it after a save on the
Settings page, and every route reads `ctx.settings` at request time, so a
territory narrowed on that page is the territory the next run sweeps without a
restart. Nothing else here changes after startup.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ..config import Settings, load_settings
from ..kernel.db import Connection
from ..kernel.modules import Registry


class Console:
    def __init__(
        self,
        *,
        settings: Settings,
        runner: Any,
        registry: Registry,
        connect: Callable[[], Connection],
        at: Callable[..., Any],
        shell: Callable[..., Any],
        back: Callable[..., Any],
    ) -> None:
        self.settings = settings
        self.runner = runner
        self.registry = registry
        self.connect = connect
        self.at = at
        self.shell = shell
        self.back = back

    @property
    def enabled(self) -> set[str]:
        """The modules this deployment runs, by name."""
        return set(self.registry.by_name)

    def reload(self) -> Settings:
        """Settings re-read after a change, and made the console's own.

        The runner holds the same object, so rebinding both here is what
        makes a saved section reach the next run.
        """
        self.settings = load_settings(self.settings.root)
        self.runner.settings = self.settings
        return self.settings
