"""The queue position the API publishes for a queued job."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for extra in (ROOT / "services", ROOT / "services" / "api"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))


def test_a_queued_job_publishes_its_position_and_a_running_job_does_not(api_app):
    import app as app_module  # noqa: PLC0415
    import jobs  # noqa: PLC0415

    store = jobs.MemoryStore()
    app_module.STATE["jobs"] = store
    job = jobs.new_job(deployment_id="oi-test", subject="dana", actor_role="staff",
                       roots={"company"}, credential_sha256="a" * 64,
                       task="research_brief", input="x", attempt_budget=12, time_budget_s=600)
    store.put(job)
    payload = app_module._job_payload(job)
    assert payload["state"] == jobs.QUEUED
    assert payload["queue_position"] == 1
    jobs.transition(job, jobs.RUNNING)
    store.put(job)
    assert "queue_position" not in app_module._job_payload(job)
