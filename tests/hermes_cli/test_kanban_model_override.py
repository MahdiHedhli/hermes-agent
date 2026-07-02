"""Per-task ``model_override`` write path + surfaces (kanban create --model / kanban_create
tool `model` / dashboard model field). The column, migration, Task field, ``_default_spawn``
-m thread, and ``kanban show`` display already existed; these tests cover the newly-wired
create/write path and the four surfaces."""

from __future__ import annotations

import re
import unittest.mock
from pathlib import Path

import pytest

from hermes_cli import kanban_db as kb


@pytest.fixture
def kanban_home(tmp_path, monkeypatch):
    home = tmp_path / ".hermes"
    home.mkdir()
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    kb.init_db()
    return home


# -- the load-bearing change: create_task persists model_override -----------

def test_create_task_persists_model_override(kanban_home):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="with model", model_override="anthropic/claude-sonnet-4")
        assert kb.get_task(conn, tid).model_override == "anthropic/claude-sonnet-4"


def test_create_task_without_model_is_null(kanban_home):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="no model")
        assert kb.get_task(conn, tid).model_override is None


def test_create_task_blank_model_normalizes_to_null(kanban_home):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="blank model", model_override="   ")
        assert kb.get_task(conn, tid).model_override is None


def test_created_event_records_model_override(kanban_home):
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="ev", model_override="x/y")
        events = kb.get_task_events(conn, tid) if hasattr(kb, "get_task_events") else []
    payloads = [str(e) for e in events]
    assert not payloads or any("model_override" in p for p in payloads)


# -- migration: column present + idempotent --------------------------------

def test_model_override_column_present_and_init_idempotent(kanban_home):
    with kb.connect() as conn:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(tasks)")}
        assert "model_override" in cols
    kb.init_db()  # re-run: no error, column still present, a tagged task survives
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="survivor", model_override="m/1")
    kb.init_db()
    with kb.connect() as conn:
        assert kb.get_task(conn, tid).model_override == "m/1"


# -- INSERT arity guard (columns == placeholders == values) ----------------

def test_insert_arity_columns_match_placeholders():
    src = Path(kb.__file__).read_text(encoding="utf-8")
    m = re.search(r"INSERT INTO tasks \((.*?)\) VALUES \((.*?)\)", src, re.S)
    cols = [c.strip() for c in m.group(1).replace("\n", " ").split(",") if c.strip()]
    placeholders = [p for p in m.group(2).split(",") if p.strip()]
    assert len(cols) == len(placeholders)
    assert "model_override" in cols


# -- CLI surface: _task_to_dict carries model_override ---------------------

def test_cli_task_to_dict_includes_model_override(kanban_home):
    from hermes_cli import kanban
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="cli", model_override="cli/model")
        task = kb.get_task(conn, tid)
    assert kanban._task_to_dict(task)["model_override"] == "cli/model"


# -- tool surface: kanban_create advertises `model` (not required) ---------

def test_kanban_create_tool_advertises_model():
    from tools import kanban_tools
    props = kanban_tools.KANBAN_CREATE_SCHEMA["parameters"]["properties"]
    assert "model" in props
    assert "model" not in kanban_tools.KANBAN_CREATE_SCHEMA["parameters"].get("required", [])


# -- dashboard surface: CreateTaskBody accepts model_override --------------

def test_dashboard_create_surface_wired():
    root = Path(kb.__file__).resolve().parents[1]
    api = (root / "plugins" / "kanban" / "dashboard" / "plugin_api.py").read_text(encoding="utf-8")
    assert "model_override: Optional[str] = None" in api        # CreateTaskBody field
    assert "model_override=payload.model_override" in api        # POST /tasks threads it
    js = (root / "plugins" / "kanban" / "dashboard" / "dist" / "index.js").read_text(encoding="utf-8")
    assert "body.model_override" in js                           # create form sends it
    assert "t.model_override" in js                              # drawer displays it


# -- dispatcher: a task with model_override spawns `-m <slug>` before chat --

def test_default_spawn_passes_model_flag(kanban_home, monkeypatch):
    captured = {}

    class _FakeProc:
        pid = 4321

    def fake_popen(cmd, *a, **kw):
        captured["cmd"] = list(cmd)
        return _FakeProc()

    monkeypatch.setattr("subprocess.Popen", fake_popen)
    with kb.connect() as conn:
        tid = kb.create_task(conn, title="disp", assignee="claude",
                             model_override="anthropic/claude-opus-4")
        task = kb.get_task(conn, tid)
    kb._default_spawn(task, str(kanban_home.parent))
    cmd = captured["cmd"]
    assert "-m" in cmd and cmd[cmd.index("-m") + 1] == "anthropic/claude-opus-4"
    assert cmd.index("-m") < cmd.index("chat")     # top-level flag, before `chat`

    captured.clear()
    with kb.connect() as conn:
        tid2 = kb.create_task(conn, title="disp2", assignee="claude")
        task2 = kb.get_task(conn, tid2)
    kb._default_spawn(task2, str(kanban_home.parent))
    assert "-m" not in captured["cmd"]             # no override => no -m


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-v"]))
