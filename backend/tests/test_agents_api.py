"""Agent CRUD and the telephony endpoints, mounted on a bare FastAPI app.

No model, no GPU, no socket. The conversation loop itself is covered by
``tests/test_conversation_session.py``; this file covers the HTTP surface —
including the properties that make the telephony guardrails meaningful at the
API boundary rather than only inside the service module.
"""

from __future__ import annotations


import pytest

# conftest.py puts `backend/` on sys.path and points OMNIVOICE_DATA_DIR at a
# throwaway tmpdir before this module imports the REAL core.config.
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.dependencies import require_admin  # noqa: E402
from api.routers import agents as agents_router  # noqa: E402
from core.db import db_conn, init_db  # noqa: E402


@pytest.fixture(scope="module")
def client():
    init_db()
    app = FastAPI()
    app.include_router(agents_router.router)
    # The router carries `require_admin` at router level, so it applies to every
    # route here. Overridden rather than tested: admin gating is covered once,
    # for the whole surface, by tests/test_admin_route_policy.py — asserting it
    # again per-router would only be testing that FastAPI wires dependencies.
    app.dependency_overrides[require_admin] = lambda: None
    return TestClient(app)


@pytest.fixture(autouse=True)
def clean_tables():
    # Converge the schema here rather than only in the `client` fixture: a test
    # that does not need HTTP still needs the tables, and without this it
    # passes only when an earlier test in the module happened to build them.
    # `init_db` is idempotent, so paying it per-test costs nothing.
    init_db()
    with db_conn() as conn:
        for table in ("telephony_calls", "telephony_allowlist", "agents", "voice_profiles"):
            # nosec B608 -- `table` iterates the literal tuple above; no input reaches it.
            conn.execute(f"DELETE FROM {table}")  # noqa: S608
    yield


def _make_agent(client, **overrides):
    body = {"name": "Support line", "system_prompt": "Be brief.", "language": "en"}
    body.update(overrides)
    res = client.post("/agents", json=body)
    assert res.status_code == 201, res.text
    return res.json()


def _consented_profile(pid="v1", ok=True):
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO voice_profiles (id, name, verified_own_voice) VALUES (?, ?, ?)",
            (pid, "Test voice", 1 if ok else 0),
        )
    return pid


# ── CRUD ─────────────────────────────────────────────────────────────────


def test_agent_crud_round_trip(client):
    created = _make_agent(client, first_message="Hi, how can I help?")
    assert created["name"] == "Support line"
    assert created["enabled"] is True

    listed = client.get("/agents").json()["agents"]
    assert [a["id"] for a in listed] == [created["id"]]

    fetched = client.get(f"/agents/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["first_message"] == "Hi, how can I help?"

    updated = client.put(f"/agents/{created['id']}", json={"name": "Renamed"})
    assert updated.status_code == 200
    assert updated.json()["name"] == "Renamed"
    # A partial update must not blank the fields it did not mention.
    assert updated.json()["system_prompt"] == "Be brief."

    deleted = client.delete(f"/agents/{created['id']}")
    assert deleted.status_code == 204
    assert client.get(f"/agents/{created['id']}").status_code == 404


def test_unknown_agent_is_404_not_500(client):
    assert client.get("/agents/nope").status_code == 404
    patched = client.put("/agents/nope", json={"name": "x"})
    assert patched.status_code == 404
    deleted = client.delete("/agents/nope")
    assert deleted.status_code == 404


def test_agent_name_is_required(client):
    created = client.post("/agents", json={"name": ""})
    assert created.status_code == 422


def test_update_can_clear_the_voice_profile(client):
    """Explicit null must clear, while an omitted key leaves the value alone.

    These are different intents and `exclude_unset` is what keeps them apart —
    worth pinning, because getting it wrong silently unsets fields on every save.
    """
    agent = _make_agent(client, voice_profile="v1")
    patched = client.put(f"/agents/{agent['id']}", json={"name": "n"})
    assert patched.json()["voice_profile"] == "v1"
    cleared = client.put(f"/agents/{agent['id']}", json={"voice_profile": None})
    assert cleared.json()["voice_profile"] is None


# ── allowlist ────────────────────────────────────────────────────────────


def test_allowlist_normalises_on_write(client):
    res = client.post(
        "/telephony/allowlist", json={"destination": "+1 (415) 555-0123", "label": "Ops"}
    )
    assert res.status_code == 201
    assert res.json()["e164"] == "+14155550123"

    listed = client.get("/telephony/allowlist").json()["destinations"]
    assert [d["e164"] for d in listed] == ["+14155550123"]

    removed = client.delete("/telephony/allowlist/+14155550123")
    assert removed.status_code == 204
    assert client.get("/telephony/allowlist").json()["destinations"] == []


def test_allowlist_rejects_a_number_without_a_country_code(client):
    res = client.post("/telephony/allowlist", json={"destination": "4155550123"})
    assert res.status_code == 400
    assert "country code" in res.json()["detail"]


def test_allowlist_write_is_idempotent(client):
    for _ in range(3):
        client.post("/telephony/allowlist", json={"destination": "+14155550123"})
    assert len(client.get("/telephony/allowlist").json()["destinations"]) == 1


# ── preflight ────────────────────────────────────────────────────────────


def test_preflight_reports_the_first_fixable_problem(client):
    agent = _make_agent(client)
    res = client.post(
        "/telephony/preflight",
        json={"agent_id": agent["id"], "destination": "+14155550123"},
    ).json()

    assert res["ok"] is False
    assert res["reason"] == "not_on_allowlist"
    # The preamble is shown before dialling, so the user can see exactly what
    # the callee will hear.
    assert "synthetic voice" in res["disclosure"].lower()


def test_preflight_writes_no_row(client):
    """It must be safe to poll as the user types, without polluting the log."""
    agent = _make_agent(client)
    for _ in range(5):
        client.post(
            "/telephony/preflight",
            json={"agent_id": agent["id"], "destination": "+1415555012"},
        )
    assert client.get("/telephony/calls").json()["calls"] == []


def test_preflight_passes_every_guardrail_then_reports_not_provisioned(client):
    _consented_profile()
    agent = _make_agent(client, voice_profile="v1")
    client.post("/telephony/allowlist", json={"destination": "+14155550123"})

    res = client.post(
        "/telephony/preflight",
        json={"agent_id": agent["id"], "destination": "+14155550123"},
    ).json()

    assert res["ok"] is False
    assert res["state"] == "NOT_PROVISIONED"
    assert res["reason"] == "not_provisioned"


# ── placement ────────────────────────────────────────────────────────────


def test_placement_is_501_when_every_guardrail_passes(client):
    _consented_profile()
    agent = _make_agent(client, voice_profile="v1")
    client.post("/telephony/allowlist", json={"destination": "+14155550123"})

    res = client.post(
        "/telephony/calls",
        json={"agent_id": agent["id"], "destination": "+14155550123"},
    )
    assert res.status_code == 501
    assert res.json()["detail"]["reason"] == "not_provisioned"


def test_placement_is_422_when_a_guardrail_refuses(client):
    agent = _make_agent(client)
    res = client.post(
        "/telephony/calls",
        json={"agent_id": agent["id"], "destination": "+14155550123"},
    )
    assert res.status_code == 422
    assert res.json()["detail"]["reason"] == "not_on_allowlist"


def test_every_placement_attempt_is_logged_including_refusals(client):
    agent = _make_agent(client)
    client.post(
        "/telephony/calls",
        json={"agent_id": agent["id"], "destination": "+14155550123"},
    )
    calls = client.get("/telephony/calls").json()["calls"]
    assert len(calls) == 1
    assert calls[0]["status"] == "refused"
    assert calls[0]["refused_reason"] == "not_on_allowlist"


def test_the_call_log_has_no_delete_endpoint(client):
    """Guardrail 5 is a property of the API's shape, not a policy.

    If a delete route is ever added, this fails — which is the point: an audit
    log the operator can quietly prune is not an audit log.
    """
    paths = {
        (method, route.path)
        for route in agents_router.router.routes
        for method in getattr(route, "methods", set())
    }
    assert not any(
        method == "DELETE" and path.startswith("/telephony/calls")
        for method, path in paths
    )


def test_placement_takes_one_destination_not_a_list(client):
    """No bulk-dial surface, asserted at the API boundary."""
    agent = _make_agent(client)
    res = client.post(
        "/telephony/calls",
        json={"agent_id": agent["id"], "destination": ["+14155550123", "+14155550124"]},
    )
    assert res.status_code == 422


def test_call_log_reports_the_cap_and_provisioning_state(client):
    body = client.get("/telephony/calls").json()
    assert body["provisioned"] is False
    assert body["daily_cap"] > 0
    assert body["calls_today"] == 0


def test_daily_cap_counts_attempts_made_through_the_api(client):
    agent = _make_agent(client)
    for _ in range(3):
        client.post(
            "/telephony/calls",
            json={"agent_id": agent["id"], "destination": "+14155550123"},
        )
    assert client.get("/telephony/calls").json()["calls_today"] == 3


# ── readiness ────────────────────────────────────────────────────────────


def test_readiness_separates_talkable_from_callable(client):
    """A browser conversation does not need a consent-locked voice; a call does."""
    _consented_profile("v2", ok=False)
    agent = _make_agent(client, voice_profile="v2")

    body = client.get(f"/agents/{agent['id']}/readiness").json()
    assert body["voice"]["ok"] is True
    assert body["callable"]["ok"] is False
    assert "own" in body["callable"]["detail"].lower()


def test_readiness_flags_a_deleted_voice_profile(client):
    agent = _make_agent(client, voice_profile="gone")
    body = client.get(f"/agents/{agent['id']}/readiness").json()
    assert body["voice"]["ok"] is False
    assert body["ready"] is False


def test_readiness_reports_llm_state_without_raising(client):
    """Whatever the environment's LLM config is, this must answer, not 500."""
    agent = _make_agent(client)
    res = client.get(f"/agents/{agent['id']}/readiness")
    assert res.status_code == 200
    assert isinstance(res.json()["llm"]["ok"], bool)
    assert res.json()["llm"]["detail"]


def test_every_updatable_field_is_a_real_agents_column():
    """The UPDATE in `update_agent` interpolates column names into SQL.

    Values are bound as parameters, but names cannot be — so the safety of
    that statement rests entirely on `_UPDATABLE_COLUMNS` containing nothing
    but real columns. This pins that, and it fails if someone adds a field to
    `AgentPatch` with no matching column: today that produces an
    `OperationalError` at runtime on the first PUT that touches it, which is a
    worse way to find out.
    """
    from api.routers.agents import _UPDATABLE_COLUMNS

    with db_conn() as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(agents)")}

    assert columns, "agents table missing — the schema did not converge"
    unknown = _UPDATABLE_COLUMNS - columns
    assert not unknown, f"not columns of `agents`: {sorted(unknown)}"


def test_an_unknown_field_cannot_reach_the_update_statement():
    """The guard in `update_agent`, exercised directly.

    Pydantic makes this unreachable through the API, which is exactly why it
    is worth a test: the guard exists for the refactor that starts passing a
    plain dict, and an untested guard is one a future cleanup deletes as dead.
    """
    from api.routers.agents import _UPDATABLE_COLUMNS

    assert "id" not in _UPDATABLE_COLUMNS, "the primary key must not be updatable"
    assert "created_at" not in _UPDATABLE_COLUMNS, "creation time is not updatable"
