import json

from brain.calibration import Calibrator


def _game(root, name, ps, result, max_turn=6):
    lines = [json.dumps({"type": "turn", "state": {"turn": t, "max_turn": max_turn}, "decision": {"win_probability": p}})
             for t, p in ps]
    lines.append(json.dumps({"type": "game_end", "result": result}))
    (root / f"{name}.jsonl").write_text("\n".join(lines), "utf-8")


def test_final_turn_overconfidence_is_pulled_down(tmp_path):
    """终局预估 75–80% 的历史样本 6 局只赢 1 局：新的终局 78% 校准到明显更低；非终局不受影响。"""
    for i, (p, r) in enumerate([(0.78, "loss"), (0.76, "win"), (0.78, "loss"), (0.73, "loss"), (0.79, "loss"), (0.8, "loss")]):
        _game(tmp_path, f"g{i}", [(3, 0.6), (6, p)], r)
    c = Calibrator(tmp_path)
    cal = c.calibrate(0.78, final_turn=True)
    assert 0.40 < cal < 0.50
    assert round(c.calibrate(0.95, final_turn=True), 6) == 0.95          # 附近没有样本：保持原值
    assert c.calibrate(None, final_turn=True) is None
