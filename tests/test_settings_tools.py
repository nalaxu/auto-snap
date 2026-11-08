"""面板设置（ui/server/settings.py，保留注释的 TOML 编辑）与工具运行器（ui/server/tools.py，白名单）。"""
from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest
from fastapi import HTTPException

from ui.server.settings import BY_PATH, Settings
from ui.server.tools import TOOLS, ToolRunner

ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture()
def settings(tmp_path):
    p = tmp_path / "settings.toml"
    shutil.copy(ROOT / "config" / "settings.toml", p)
    (tmp_path / ".env").write_text("OPENAI_API_KEY=sk-secret\n# DEEPSEEK_API_KEY=x\n", "utf-8")
    return Settings(p, tmp_path, tmp_path / "bk")


def test_read(settings, monkeypatch):
    for n in ("OPENAI_API_KEY", "OPENAI_BASE_URL", "DEEPSEEK_API_KEY", "DEEPSEEK_API_URL", "ANTHROPIC_API_KEY"):
        monkeypatch.delenv(n, raising=False)
    r = settings.read()
    f = {x["path"]: x for x in r["fields"]}
    assert f["game.client"]["options"][0] == "auto" and "cn_official" in f["game.client"]["options"]
    assert "deepseek" in f["llm.profile"]["options"]
    assert isinstance(f["llm.tasks.decision.deadline_timed_s"]["value"], (int, float))
    sec = {s["name"]: s["set"] for s in r["secrets"]}
    assert sec["OPENAI_API_KEY"] is True and sec["DEEPSEEK_API_KEY"] is False   # 注释行不算
    assert "sk-secret" not in str(r)                                             # 不返回密钥内容


def test_write_keeps_comments_and_roundtrips(settings):
    orig = settings.path.read_text("utf-8")
    r = settings.write({"brain.snap": False, "brain.retreat_win_prob": 0.3, "llm.profile": "deepseek",
                        "llm.tasks.decision.deadline_timed_s": 28})
    assert set(r["changed"]) == {"brain.snap", "brain.retreat_win_prob", "llm.profile", "llm.tasks.decision.deadline_timed_s"}
    text = settings.path.read_text("utf-8")
    assert "snap = false" in text and "# 允许大模型决定 Snap" in text and "retreat_win_prob = 0.3" in text
    assert "deadline_timed_s = 28 " in text and "profile = \"deepseek\"" in text
    assert len(list(settings.backup_dir.glob("settings.toml.*"))) == 1
    settings.write({"brain.snap": True, "brain.retreat_win_prob": None, "llm.profile": "",
                    "llm.tasks.decision.deadline_timed_s": 25})
    assert settings.path.read_text("utf-8") == orig
    assert settings.write({"brain.snap": True})["changed"] == []    # 没变化不写、不备份
    assert len(list(settings.backup_dir.glob("settings.toml.*"))) == 2


@pytest.mark.parametrize("values", [
    {"brain.decider": "magic"},
    {"brain.snap": "yes"},
    {"llm.tasks.decision.deadline_timed_s": 500},
    {"brain.retreat_win_prob": 1.5},
    {"game.client": "nope"},
    {"llm.profile": "nope"},
    {"llm.api_key_env": "X"},          # 不在白名单
    {"llm.tasks.decision.model": "  "},
])
def test_write_rejects(settings, values):
    orig = settings.path.read_text("utf-8")
    with pytest.raises(HTTPException):
        settings.write(values)
    assert settings.path.read_text("utf-8") == orig


def test_schema_paths_exist_in_real_config():
    import tomllib

    cfg = tomllib.loads((ROOT / "config" / "settings.toml").read_text("utf-8"))
    for path, f in BY_PATH.items():
        if f.get("optional"):
            continue
        cur = cfg
        for k in path.split("."):
            assert k in cur, path
            cur = cur[k]


def test_tool_build():
    r = ToolRunner(ROOT)
    assert r.build("analyze_games", {})[2:] == ["tools/analyze_games.py"]
    assert r.build("analyze_games", {"last": 5})[-2:] == ["--last", "5"]
    assert r.build("bench_decision", {})[-2:] == ["--reps", "1"]
    for tool, params in [("rm", {}), ("analyze_games", {"last": "x"}), ("analyze_games", {"last": 9999}),
                         ("analyze_games", {"evil": 1}), ("clients", {"last": 1})]:
        with pytest.raises(HTTPException):
            r.build(tool, params)
    assert all(t["argv"][0].endswith(".py") for t in TOOLS.values())


def test_tool_run_and_single_flight(monkeypatch, tmp_path):
    script = tmp_path / "echo.py"
    script.write_text("import time\nprint('第一行')\ntime.sleep(0.6)\nprint('done')\n", "utf-8")
    monkeypatch.setitem(TOOLS, "echo_test", {"label": "测试", "icon": "", "desc": "", "game": False, "argv": [str(script)]})
    r = ToolRunner(ROOT)
    v = r.start("echo_test", {})
    with pytest.raises(HTTPException) as e:
        r.start("echo_test", {})
    assert e.value.status_code == 409
    end = time.time() + 20
    while r.get(v["id"]).ended is None and time.time() < end:
        time.sleep(0.05)
    out = r.get(v["id"]).view()
    assert out["code"] == 0 and out["lines"] == ["第一行", "done"]
    assert r.get(v["id"]).view(since=1)["lines"] == ["done"]
    r.start("echo_test", {})   # 上一次结束后可以再跑


def test_api(settings, tmp_path):
    from fastapi.testclient import TestClient

    from ui.server.app import create_app

    with TestClient(create_app(data_dir=tmp_path / "data", settings_path=settings.path)) as c:
        assert len(c.get("/api/settings").json()["fields"]) == len(BY_PATH)
        assert c.put("/api/settings", json={"values": {"brain.snap": False}}).json() == {"changed": ["brain.snap"]}
        assert c.put("/api/settings", json={"values": {"brain.snap": 3}}).status_code == 400
        assert {t["id"] for t in c.get("/api/tools").json()} == set(TOOLS)
        assert c.post("/api/tools/rm/run", json={"params": {}}).status_code == 404
        assert c.get("/api/tools/runs/999").status_code == 404
