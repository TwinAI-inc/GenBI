"""Project save / load service.

All operations require an authenticated user and enforce tenant isolation:
projects are only visible / mutable by their owner_id. Cross-user access
returns 404, never 403, so we don't leak the existence of other users'
projects.

Size limits and per-user project caps are enforced here, not in the route
handler, so any future caller (cron, admin tool) inherits the same rules.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from extensions import db
from auth.models import Project

# 8 MB per project payload. Rough headroom for ~50k rows of moderate width
# at typical CSV densities. Above this, the user is encouraged to filter or
# aggregate before saving.
MAX_PAYLOAD_BYTES = 8 * 1024 * 1024

# Per-user project cap. Keeps the workspace navigable and bounds storage
# growth; lift on plan upgrade later if needed.
MAX_PROJECTS_PER_USER = 50


class ProjectError(Exception):
    """Raised on validation / quota / not-found failures.

    The route layer maps this to a 400/404 response without leaking any
    internal detail beyond the human-readable message.
    """

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def _payload_size(dataset, charts) -> int:
    """Approximate JSON byte size of the combined payload."""
    return len(json.dumps(dataset or {}, default=str)) + \
           len(json.dumps(charts or {}, default=str))


def _validate_payload(dataset, charts) -> int:
    if not isinstance(dataset, dict):
        raise ProjectError('dataset must be an object', 400)
    if not isinstance(charts, dict):
        raise ProjectError('charts must be an object', 400)
    size = _payload_size(dataset, charts)
    if size > MAX_PAYLOAD_BYTES:
        raise ProjectError(
            f'Project too large ({size:,} bytes). Limit is {MAX_PAYLOAD_BYTES:,} '
            f'bytes — try filtering or aggregating before saving.', 413)
    return size


def list_projects(user_id: str):
    """Return summary list for one user, newest first."""
    if not user_id:
        raise ProjectError('Authentication required', 401)
    rows = (Project.query
                   .filter(Project.owner_id == user_id)
                   .order_by(Project.updated_at.desc())
                   .limit(MAX_PROJECTS_PER_USER)
                   .all())
    return [p.to_summary() for p in rows]


def get_project(user_id: str, project_id: str):
    """Return one project. 404 on not-found OR not-owned (no enumeration)."""
    if not user_id or not project_id:
        raise ProjectError('Project not found', 404)
    p = Project.query.filter(
        Project.id == project_id,
        Project.owner_id == user_id,
    ).first()
    if not p:
        raise ProjectError('Project not found', 404)
    return p.to_full()


def create_project(user_id: str, name: str, dataset: dict, charts: dict):
    if not user_id:
        raise ProjectError('Authentication required', 401)
    name = (name or '').strip()
    if not name:
        raise ProjectError('Project name is required', 400)
    if len(name) > 200:
        raise ProjectError('Project name too long (max 200 chars)', 400)
    size = _validate_payload(dataset, charts)

    # Race protection: two concurrent POSTs from the same user could each
    # observe count < MAX_PROJECTS_PER_USER and both commit, exceeding the
    # cap. Take a row-level lock on the user record for the duration of
    # the count+insert so the second request blocks until the first
    # finishes. Cheap on Postgres; on SQLite the lock is process-wide
    # (single writer), which is also fine.
    from auth.models import User
    locked_user = (User.query
                       .filter(User.id == user_id)
                       .with_for_update()
                       .first())
    if not locked_user:
        # Authenticated session pointed at a now-deleted user — extremely
        # unlikely but worth a defensive 401 rather than a 500.
        db.session.rollback()
        raise ProjectError('Authentication required', 401)

    try:
        count = Project.query.filter(Project.owner_id == user_id).count()
        if count >= MAX_PROJECTS_PER_USER:
            db.session.rollback()
            raise ProjectError(
                f'Project limit reached ({MAX_PROJECTS_PER_USER}). '
                f'Delete an existing project before saving a new one.', 409)
        p = Project(owner_id=user_id, name=name,
                    dataset_json=dataset, charts_json=charts,
                    size_bytes=size)
        db.session.add(p)
        db.session.commit()
        return p.to_full()
    except ProjectError:
        raise
    except Exception:
        db.session.rollback()
        raise


def update_project(user_id: str, project_id: str, name=None,
                   dataset=None, charts=None, expected_updated_at=None):
    """Update a project. Optionally pass ``expected_updated_at`` (the
    ``updated_at`` value the client last read) for optimistic concurrency
    control: if it doesn't match the current row, return 409 Conflict
    instead of overwriting.

    This is the ETag/If-Match pattern adapted to a JSON API. Without it,
    two tabs editing the same project race and the slower request silently
    clobbers the faster one — a real data-loss path, especially given the
    feature is explicitly cross-device.
    """
    p = Project.query.filter(
        Project.id == project_id,
        Project.owner_id == user_id,
    ).first()
    if not p:
        raise ProjectError('Project not found', 404)
    if expected_updated_at:
        # Compare on second precision to tolerate timezone-format round-trip
        # noise; if the client and server disagree on the timestamp, the
        # client is operating on stale state.
        try:
            from datetime import datetime as _dt
            server_iso = p.updated_at.isoformat() if p.updated_at else ''
            # Allow exact match OR equality up to the seconds field, since
            # JSON / Python timestamp round-trips can drop microseconds.
            if expected_updated_at != server_iso and \
               expected_updated_at[:19] != server_iso[:19]:
                raise ProjectError(
                    'Project was modified by another session — reload before saving.',
                    409)
        except ProjectError:
            raise
        except Exception:
            # Malformed timestamp from client — be permissive but log.
            pass
    if name is not None:
        name = name.strip()
        if not name:
            raise ProjectError('Project name cannot be empty', 400)
        if len(name) > 200:
            raise ProjectError('Project name too long (max 200 chars)', 400)
        p.name = name
    if dataset is not None or charts is not None:
        new_dataset = dataset if dataset is not None else (p.dataset_json or {})
        new_charts = charts if charts is not None else (p.charts_json or {})
        size = _validate_payload(new_dataset, new_charts)
        p.dataset_json = new_dataset
        p.charts_json = new_charts
        p.size_bytes = size
    p.updated_at = datetime.now(timezone.utc)
    db.session.commit()
    return p.to_full()


def delete_project(user_id: str, project_id: str):
    p = Project.query.filter(
        Project.id == project_id,
        Project.owner_id == user_id,
    ).first()
    if not p:
        raise ProjectError('Project not found', 404)
    db.session.delete(p)
    db.session.commit()
    return True
