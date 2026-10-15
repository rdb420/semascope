"""Settings for the unit tests: SQLite in memory, debug on, no external services."""

import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["SEMASCOPE_DEBUG"] = "true"

from oss_server.settings import *  # noqa: E402, F403
