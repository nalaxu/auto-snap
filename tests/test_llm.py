"""多厂商大模型层：请求结构、接口回退、拒答、配置（伪造客户端，不访问网络）。"""
import json
from types import SimpleNamespace as NS

import pytest

from brain import llm
from brain.llm.anthropic_client import AnthropicClient
from brain.llm.openai_client import OpenAIClient

TASK = llm.TaskConfig(model="gpt-6.1-sol", effort="medium")
SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"], "additionalProperties": False}
ASSESS = {"effect": "End of Turn: +1 Power here.", "bias": {"early": 2, "mid": 1, "final": 0},
          "tags": ["buff_per_turn"], "confidence": "high", "notes": "越早越好"}


class NotFound(Exception):
    status_code = 404


class FakeOpenAI:
    def __init__(self, payload, responses_supported=True, refusal=None):
        self.calls = []
        outer = self

        class Responses:
            def create(self, **kw):
                outer.calls.append(("responses", kw))
                if not responses_supported:
                    raise NotFound("404 page not found: /v1/responses")
                content = [NS(type="refusal", refusal=refusal)] if refusal else [NS(type="output_text")]
                return NS(status="completed", output=[NS(content=content)], output_text=json.dumps(payload))

        class Completions:
            def create(self, **kw):
                outer.calls.append(("chat", kw))
                return NS(choices=[NS(finish_reason="stop", message=NS(content=json.dumps(payload), refusal=refusal))])

        self.responses = Responses()
        self.chat = NS(completions=Completions())


def test_openai_responses_request_shape():
    fake = FakeOpenAI({"ok": True})
    assert OpenAIClient("k", client=fake).complete_json(TASK, "sys", "hi", SCHEMA, "ping") == {"ok": True}
    kind, kw = fake.calls[0]
    assert kind == "responses" and kw["model"] == "gpt-6.1-sol" and kw["reasoning"] == {"effort": "medium"}
    assert kw["text"]["format"] == {"type": "json_schema", "name": "ping", "schema": SCHEMA, "strict": True}


def test_openai_auto_falls_back_to_chat_for_proxies():
    fake = FakeOpenAI({"ok": True}, responses_supported=False)
    c = OpenAIClient("k", api="auto", client=fake)
    assert c.complete_json(TASK, "sys", "hi", SCHEMA, "ping") == {"ok": True}
    assert [k for k, _ in fake.calls] == ["responses", "chat"] and c.api == "chat"
    chat_kw = fake.calls[1][1]
    assert chat_kw["reasoning_effort"] == "medium"
    assert chat_kw["response_format"]["json_schema"]["strict"] is True
    c.complete_json(TASK, "sys", "hi", SCHEMA, "ping")       # 记住：之后直接走 chat
    assert [k for k, _ in fake.calls] == ["responses", "chat", "chat"]


def test_openai_refusal_raises():
    with pytest.raises(llm.LLMRefusal):
        OpenAIClient("k", client=FakeOpenAI({}, refusal="no")).complete_json(TASK, "s", "u", SCHEMA, "x")


def test_anthropic_client_request_shape():
    seen = {}

    def create(**kw):
        seen.update(kw)
        return NS(stop_reason="end_turn", content=[NS(type="text", text='{"ok": true}')])

    c = AnthropicClient(client=NS(messages=NS(create=create)))
    assert c.complete_json(llm.TaskConfig(model="claude-opus-5-5"), "s", "u", SCHEMA, "x") == {"ok": True}
    assert seen["extra_body"]["output_config"]["format"]["type"] == "json_schema"
    assert seen["extra_body"]["fallbacks"] == "default"


def test_location_analyst_is_provider_agnostic():
    a = llm.LocationAnalyst(OpenAIClient("k", client=FakeOpenAI(ASSESS)), TASK)
    info = a.analyze("MuirIsland")
    assert info.bias == {"early": 2, "mid": 1, "final": 0} and info.source == "llm:openai/gpt-6.1-sol"
    low = dict(ASSESS, confidence="low")
    assert llm.LocationAnalyst(OpenAIClient("k", client=FakeOpenAI(low)), TASK).analyze("X") is None


def test_make_client_and_task_config(monkeypatch, tmp_path):
    monkeypatch.setattr(llm, "load_dotenv", lambda *a, **k: None)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    settings = {"llm": {"provider": "openai", "tasks": {"decision": {"model": "gpt-6-luna", "effort": "none"}}}}
    assert llm.make_client(settings) is None                       # 没有密钥 -> 不启用
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://relay.example.com/v1")
    c = llm.make_client(settings)
    assert c.provider == "openai" and str(c.client.base_url).startswith("https://relay.example.com/v1")
    dec = llm.task_config(settings, "decision")
    assert (dec.model, dec.effort, dec.timeout_s, dec.max_retries) == ("gpt-6-luna", "none", 45, 0)  # 覆盖模型，其余用默认
    rev = llm.task_config(settings, "review")
    assert (rev.model, rev.effort) == ("gpt-6.1-sol", "high")


def test_load_dotenv_does_not_override(monkeypatch, tmp_path):
    env = tmp_path / ".env"
    env.write_text("# c\nFOO_KEY='abc'\nBAR_KEY=1\n", "utf-8")
    monkeypatch.setenv("BAR_KEY", "keep")
    monkeypatch.delenv("FOO_KEY", raising=False)
    llm.load_dotenv(env)
    import os
    assert os.environ["FOO_KEY"] == "abc" and os.environ["BAR_KEY"] == "keep"


def test_model_not_found_does_not_switch_to_chat():
    class ModelMissing(Exception):
        status_code = 404

    class Resp:
        def create(self, **kw):
            raise ModelMissing("Error code: 404 - {'error': {'code': 'model_not_found', 'message': 'Model \"x\" is not available'}}")

    c = OpenAIClient("k", api="auto", client=NS(responses=Resp(), chat=None))
    with pytest.raises(llm.LLMError):
        c.complete_json(TASK, "s", "u", SCHEMA, "x")
    assert c.api == "auto"   # 没有被误判为端点不支持


def test_unified_decider_prompt_and_fallback():
    """场地分析并入决策：提示词含知识库场地、对手手牌/牌库、剩余牌库；非法输出重试后由规则 AI 兜底。"""
    from brain.llm.decider import LLMDecider, build_prompt
    from brain.models import Card, GameState, Location

    st = GameState(turn=2, energy=2, hand=[Card(id="Medusa", cost=2, power=2)], deck_remaining=["Hulk"],
                   opp_hand_count=5, opp_deck_count=7, my_deck_count=8,
                   locations=[Location(idx=0, name="Machineworld"), Location(idx=1, name="Ruins"),
                              Location(idx=2, name="", revealed=False)])
    data = json.loads(build_prompt(st).split("\n", 1)[1])
    assert data["opp_hand_count"] == 5 and data["deck_remaining"] == ["Hulk"] and data["card_info"]["Hulk"].startswith("6费")
    assert data["locations"][0]["kb"]["bias_now"] == -3 and data["locations"][2]["kb"] is None

    good = {"location_assessment": [], "target_locations": [1, 2], "plan": "后续", "reason": "中路+3",
            "actions": [{"card": "Medusa", "location": 1}]}
    dec = LLMDecider(OpenAIClient("k", client=FakeOpenAI(good)), llm.TaskConfig("m"))
    assert dec.decide(st).actions[0].location == 1 and dec.last_raw["plan"] == "后续"

    bad = dict(good, actions=[{"card": "Hulk", "location": 1}])   # 手里没有 Hulk -> 非法
    fake = FakeOpenAI(bad)
    d = LLMDecider(OpenAIClient("k", client=fake), llm.TaskConfig("m")).decide(st)
    assert len(fake.calls) == 2 and d.source == "rule_based" and "兜底" in d.reason


def test_deadline_falls_back_fast_and_effort_follows_timer():
    """有回合计时：用 effort_timed，且到截止时间立即用规则 AI 方案（不等慢的大模型）。"""
    import time as _t
    from brain.llm.decider import LLMDecider
    from brain.models import Card, GameState, Location

    seen = []

    class Slow:
        provider = "fake"

        def complete_json(self, task, *a):
            seen.append(task.effort)
            _t.sleep(1.5)
            return {"location_assessment": [], "target_locations": [], "plan": "", "reason": "",
                    "actions": [{"card": "Medusa", "location": 1}]}

    st = GameState(turn=2, energy=2, hand=[Card(id="Medusa", cost=2, power=2)],
                   locations=[Location(idx=i, name="Ruins") for i in range(3)], turn_timer=True)
    dec = LLMDecider(Slow(), llm.TaskConfig("m", effort="medium"), effort_timed="low", deadline_timed_s=0.3, deadline_s=5)
    t0 = _t.time()
    d = dec.decide(st)
    assert _t.time() - t0 < 1.0 and d.source == "rule_based" and "未返回" in d.reason
    assert seen == ["low"]

    st2 = st.model_copy(update={"turn_timer": False})        # 无计时：用 medium，等得起
    d2 = dec.decide(st2)
    assert d2.source.startswith("llm:m/medium") and seen[-1] == "medium"


def test_late_turns_get_longer_deadline():
    from brain import llm
    from brain.llm.decider import LLMDecider
    from brain.models import Card, GameState, Location

    dec = LLMDecider(object(), llm.TaskConfig("m", effort="medium", timeout_s=45), effort_timed="low",
                     deadline_timed_s=12, deadline_late_s=22, late_from_turn=5)
    mk = lambda t, timer=True: GameState(turn=t, energy=t, hand=[Card(id="X", cost=1, power=1)], turn_timer=timer,
                                         locations=[Location(idx=i) for i in range(3)])
    assert dec._task_for(mk(4))[1] == 12 and dec._task_for(mk(4))[0].timeout_s == 17
    assert dec._task_for(mk(5))[1] == 22 and dec._task_for(mk(6))[0].timeout_s == 27
    assert dec._task_for(mk(6, timer=False))[1] == dec.deadline_s


def test_profile_switches_provider_settings(monkeypatch):
    from brain import llm
    monkeypatch.delenv("LLM_PROFILE", raising=False)
    monkeypatch.setenv("DS_KEY_T", "k")
    settings = {"llm": {"provider": "openai", "api": "auto", "api_key_env": "OPENAI_API_KEY",
                        "tasks": {"decision": {"model": "gpt-6.1-sol", "effort": "low"}},
                        "profile": "ds",
                        "profiles": {"ds": {"api": "chat", "api_key_env": "DS_KEY_T", "base_url": "https://api.deepseek.com",
                                            "json_mode": "object", "extra_body": {"thinking": {"type": "disabled"}},
                                            "tasks": {"decision": {"model": "deepseek-flash", "effort": "off"}}}}}}
    c = llm.make_client(settings)
    assert c.api == "chat" and c.json_mode == "object" and c.extra_body == {"thinking": {"type": "disabled"}}
    t = llm.task_config(settings, "decision")
    assert t.model == "deepseek-flash" and t.effort == "off"


def test_object_mode_puts_schema_in_prompt_and_omits_reasoning():
    from brain.llm.base import TaskConfig
    from brain.llm.openai_client import OpenAIClient

    seen = {}

    class Msg:
        content = '{"x": 1}'
        refusal = None

    class Resp:
        choices = [type("C", (), {"message": Msg(), "finish_reason": "stop"})()]

    class Comp:
        def create(self, **kw):
            seen.update(kw)
            return Resp()

    fake = type("F", (), {"chat": type("Ch", (), {"completions": Comp()})()})()
    c = OpenAIClient("k", api="chat", client=fake, json_mode="object", extra_body={"thinking": {"type": "disabled"}})
    assert c.complete_json(TaskConfig("m", effort="off"), "SYS", "U", {"type": "object"}, "n") == {"x": 1}
    assert seen["response_format"] == {"type": "json_object"} and "JSON Schema" in seen["messages"][0]["content"]
    assert "reasoning_effort" not in seen and seen["extra_body"] == {"thinking": {"type": "disabled"}}


def test_warmup_is_throttled_and_non_blocking():
    import time as _t
    from brain import llm
    from brain.llm.decider import LLMDecider

    calls = []

    class C:
        provider = "fake"

        def warmup(self):
            calls.append(_t.time())
            _t.sleep(0.2)
            return True

    dec = LLMDecider(C(), llm.TaskConfig("m"))
    t0 = _t.time()
    for _ in range(50):
        dec.warmup()
    assert _t.time() - t0 < 0.1          # 不阻塞主循环
    _t.sleep(0.3)
    assert len(calls) == 1               # 5s 内只提交一次


def test_decider_snap_flag_respects_can_snap():
    """Snap 进入结构化输出与提示；本局已 Snap（can_snap=False）时即使模型说要 Snap 也不执行。"""
    from brain.llm.decider import SCHEMA as DSCHEMA, LLMDecider, build_prompt
    from brain.models import Card, GameState, Location

    def state(can_snap):
        return GameState(turn=4, energy=4, hand=[Card(id="Medusa", cost=2, power=2)], can_snap=can_snap,
                         opp_snapped=True, cube_stake=2, cube_next=2,
                         locations=[Location(idx=i, name="Ruins") for i in range(3)])

    assert "snap" in DSCHEMA["required"]
    data = json.loads(build_prompt(state(True)).split("\n", 1)[1])
    assert data["can_snap"] is True and data["opp_snapped"] is True and data["cube_next"] == 2
    out = {"location_assessment": [], "target_locations": [0, 1], "plan": "p", "reason": "r", "snap": True,
           "actions": [{"card": "Medusa", "location": 1}]}
    assert LLMDecider(OpenAIClient("k", client=FakeOpenAI(out)), llm.TaskConfig("m")).decide(state(True)).snap is True
    assert LLMDecider(OpenAIClient("k", client=FakeOpenAI(out)), llm.TaskConfig("m")).decide(state(False)).snap is False


def test_decider_respects_turn_timer_budget():
    """回合剩余时间不够：直接规则 AI，不调用大模型；剩余有限：截止时间收紧到预算。"""
    import time as _t

    from brain.llm.decider import LLMDecider
    from brain.models import Card, GameState, Location

    class Boom:
        def complete_json(self, *a, **k):
            raise AssertionError("不应调用大模型")

    st = GameState(turn=2, energy=2, hand=[Card(id="Medusa", cost=2, power=2)],
                   locations=[Location(idx=i, name="Ruins") for i in range(3)],
                   turn_seconds_left=9.0, timer_read_at=_t.monotonic())
    dec = LLMDecider(Boom(), llm.TaskConfig("m"), deadline_timed_s=16, timer_reserve_s=7, min_llm_s=5)
    d = dec.decide(st)
    assert d.source == "rule_based" and "回合剩余" in d.reason and d.actions

    st.turn_seconds_left, st.timer_read_at = 30.0, _t.monotonic()
    assert 22 < dec.time_budget(st) <= 23                     # 30 − 7
    st.turn_seconds_left = None
    assert dec.time_budget(st) is None                         # 读不到计时：按配置截止
