
from fastapi import FastAPI
from sqlalchemy import text

from app.services.handoff_bootstrap import warn_if_multi_process
from app.api import (
    admin_deletion,
    auth,
    bookings,
    dashboard_access,
    defect_types,
    equipment,
    internal,
    locomotives,
    section_dashboard,
    sections,
    shed_visits,
    pending_requirements,
    visit_history,
    workflow,
)
from app.clients.loco_master import LocoMasterUnavailableError
from app.core.config import get_settings
from app.core.logging_config import configure_logging
from app.db.session import SessionLocal
from app.services.loco_master_client import get_loco_master_client

# Application INFO; SQLAlchemy pinned to WARNING so routine SQL can never flood the journal.
configure_logging()

app = FastAPI(title="Operations Dashboard API")

app.include_router(auth.router)
app.include_router(sections.router)
app.include_router(dashboard_access.router)
app.include_router(equipment.router)
app.include_router(locomotives.router)
app.include_router(defect_types.router)
app.include_router(shed_visits.router)
app.include_router(section_dashboard.section_router)
app.include_router(section_dashboard.assignment_router)
app.include_router(section_dashboard.admin_assignment_router)
app.include_router(bookings.router)
app.include_router(bookings.admin_router)
app.include_router(workflow.router)
app.include_router(pending_requirements.router)
app.include_router(visit_history.router)
app.include_router(admin_deletion.router)
app.include_router(internal.router)
app.include_router(internal.internal_api_router)

# Fail fast on startup if required configuration is missing, rather than on
# the first request.
get_settings()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/health/dependencies")
def health_dependencies():
    result = {"rdcms": "unknown", "loco_master": "unknown"}

    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        result["rdcms"] = "ok"
    except Exception:
        result["rdcms"] = "unreachable"
    finally:
        db.close()

    try:
        get_loco_master_client().get_equipment_families()
        result["loco_master"] = "ok"
    except LocoMasterUnavailableError:
        result["loco_master"] = "unreachable"
    except Exception:
        result["loco_master"] = "unreachable"

    return result


# In-memory handoff bootstrap state is per-process (see services/handoff_bootstrap.py).
warn_if_multi_process()
