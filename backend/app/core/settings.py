from __future__ import annotations

import os
from pydantic import BaseModel


class Settings(BaseModel):
    """Runtime settings.

    Default is SQLite so the software can be tested locally without Docker.
    Production/staging should use PostgreSQL/PostGIS via DATABASE_URL.
    """

    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./nuvision_arraylab_dev.db")
    software_version: str = "0.5.3-phase005D-RUNFIX"
    phase: str = "NVA_005D_RUNFIX"
    truth_boundary: str = "design-assist only; manufacturer/engineer approval required for structural claims"


settings = Settings()
