"""Delete session-scoped data older than a retention window.

Why this exists
---------------
A single completed interview session measures roughly **176 KB** across the
collections below (measured, not estimated — see §27 of
PRODUCTION_DEPLOYMENT_PLAN.md). The deployment target is a MongoDB Atlas M0
cluster capped at **512 MB**, so the runway is around **3,000 sessions** and
then writes start failing. Nothing currently deletes any of it.

Why it is a script rather than a TTL index
------------------------------------------
MongoDB TTL indexes only act on BSON *date* fields, and every ``created_at`` in
this schema is an ISO **string**. Making TTL work would mean adding a date field
to every session-scoped write path. More importantly, how long a candidate's
interview history should be kept is a product and privacy decision, not a
technical default — so this tool applies a window you choose instead of one
chosen for you.

Safety
------
Dry run is the DEFAULT. Nothing is deleted without ``--apply``. ``users`` is
never touched: accounts must outlive their interview data.

Usage
-----
    # See what a 90-day window would remove, change nothing
    python scripts/prune_old_sessions.py --days 90

    # Actually remove it
    python scripts/prune_old_sessions.py --days 90 --apply

    # Only one user's sessions (e.g. honouring a deletion request)
    python scripts/prune_old_sessions.py --days 0 --user-id <id> --apply
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend.config import get_settings  # noqa: E402
from backend.database.mongo_client import get_repository  # noqa: E402

# Collections keyed by session_id. `users` is deliberately absent.
SESSION_SCOPED_COLLECTIONS = (
    "resume_data",
    "role_matches",
    "interview_round_contexts",
    "interview_responses",
    "assessment_sessions",
    "dsa_sessions",
    "final_reports",
    "interview_round_sessions",
)


def _parse_created_at(value: object) -> datetime | None:
    """Best-effort parse of the ISO strings this schema stores.

    Returns None for anything unparseable, and the caller treats that as "do
    not touch" — a row whose age cannot be established is never deleted.
    """

    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--days",
        type=int,
        required=True,
        help="Delete sessions created more than this many days ago. 0 means all.",
    )
    parser.add_argument(
        "--user-id",
        default=None,
        help="Restrict to one user's sessions, e.g. for a deletion request.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete. Without this the script only reports.",
    )
    args = parser.parse_args()

    if args.days < 0:
        parser.error("--days cannot be negative")

    repo = get_repository()
    settings = get_settings()
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.days)

    query: dict[str, object] = {}
    if args.user_id:
        query["user_id"] = args.user_id

    candidates: list[str] = []
    skipped_unparseable = 0
    for session in repo.db.sessions.find(query, {"_id": 1, "created_at": 1}):
        created_at = _parse_created_at(session.get("created_at"))
        if created_at is None:
            skipped_unparseable += 1
            continue
        if created_at < cutoff:
            candidates.append(str(session["_id"]))

    mode = "APPLYING" if args.apply else "DRY RUN (nothing will be deleted)"
    print(f"Database : {settings.mongo.database_name}")
    print(f"Mode     : {mode}")
    print(f"Cutoff   : sessions created before {cutoff.isoformat()}")
    if args.user_id:
        print(f"User     : {args.user_id}")
    print(f"Matched  : {len(candidates)} session(s)")
    if skipped_unparseable:
        print(
            f"Skipped  : {skipped_unparseable} session(s) with an unreadable "
            "created_at — never deleted, since their age cannot be established"
        )
    if not candidates:
        print("\nNothing to do.")
        return 0

    print()
    print(f"{'collection':<28} {'documents':>10}")
    total = 0
    for collection in SESSION_SCOPED_COLLECTIONS:
        count = repo.db[collection].count_documents(
            {"session_id": {"$in": candidates}}
        )
        total += count
        print(f"{collection:<28} {count:>10,}")
    print(f"{'sessions':<28} {len(candidates):>10,}")
    print(f"{'TOTAL':<28} {total + len(candidates):>10,}")

    if not args.apply:
        print("\nRe-run with --apply to delete the above.")
        return 0

    print("\nDeleting...")
    for collection in SESSION_SCOPED_COLLECTIONS:
        result = repo.db[collection].delete_many(
            {"session_id": {"$in": candidates}}
        )
        print(f"  {collection:<26} {result.deleted_count:>10,} removed")
    # Sessions last, so an interruption leaves orphaned child rows that a re-run
    # still finds, rather than child rows whose session is gone and which no
    # longer match any query.
    result = repo.db.sessions.delete_many({"_id": {"$in": candidates}})
    print(f"  {'sessions':<26} {result.deleted_count:>10,} removed")
    print("\nDone.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
