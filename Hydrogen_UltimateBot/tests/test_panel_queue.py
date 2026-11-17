import sqlite3

from web_panel import app as panel_app


def test_command_lease_prevents_immediate_redelivery_and_recovers_expired_lease(tmp_path, monkeypatch):
    monkeypatch.setattr(panel_app, "DB_PATH", tmp_path / "panel-test.sqlite3")
    with panel_app._db() as conn:
        conn.execute(
            "INSERT INTO commands(guild_id,user_id,command,created_at) VALUES(?,?,?,?)",
            ("123", "456", "status", 1000),
        )

    first = panel_app._claim_commands(now=1000)
    assert len(first) == 1
    assert first[0]["command"] == "status"

    # Another poll during the lease must not receive the same work.
    assert panel_app._claim_commands(now=1010) == []

    # If the worker disappears, the lease expires and the command is recoverable.
    recovered = panel_app._claim_commands(
        now=1000 + panel_app.COMMAND_LEASE_SECONDS + 1
    )
    assert len(recovered) == 1
    assert recovered[0]["id"] == first[0]["id"]

    with panel_app._db() as conn:
        row = conn.execute(
            "SELECT status,delivered_at FROM commands WHERE id=?",
            (first[0]["id"],),
        ).fetchone()
    assert row["status"] == "processing"
    assert row["delivered_at"] == 1000 + panel_app.COMMAND_LEASE_SECONDS + 1


def test_reclaimed_command_gets_new_fencing_token(tmp_path, monkeypatch):
    monkeypatch.setattr(panel_app, "DB_PATH", tmp_path / "panel-fencing.sqlite3")
    with panel_app._db() as conn:
        conn.execute(
            "INSERT INTO commands(guild_id,user_id,command,created_at) VALUES(?,?,?,?)",
            ("123", "456", "status", 1000),
        )

    first = panel_app._claim_commands(now=1000)[0]
    second = panel_app._claim_commands(
        now=1000 + panel_app.COMMAND_LEASE_SECONDS + 1
    )[0]
    assert first["claim_token"]
    assert second["claim_token"]
    assert first["claim_token"] != second["claim_token"]

    # A delayed worker cannot overwrite the result after its lease was reclaimed.
    with panel_app._db() as conn:
        stale = conn.execute(
            "UPDATE commands SET status='completed',result='stale',claim_token=NULL "
            "WHERE id=? AND status='processing' AND claim_token=?",
            (first["id"], first["claim_token"]),
        )
        assert stale.rowcount == 0
        current = conn.execute(
            "UPDATE commands SET status='completed',result='current',claim_token=NULL "
            "WHERE id=? AND status='processing' AND claim_token=?",
            (second["id"], second["claim_token"]),
        )
        assert current.rowcount == 1
        row = conn.execute(
            "SELECT status,result FROM commands WHERE id=?", (first["id"],)
        ).fetchone()
    assert row["status"] == "completed"
    assert row["result"] == "current"
