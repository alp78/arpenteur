"""The console: a browser over the survey.

Kept in its own package so the CLI and the stages never import FastAPI, and a
deployment that only runs the pipeline on a timer needs none of it.
"""

from .app import create_app

__all__ = ["create_app"]
