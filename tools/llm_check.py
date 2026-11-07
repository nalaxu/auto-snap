"""大模型连通性检查（配置好 .env 后先跑这个）。

    python tools/llm_check.py                       # 列出可用模型 + 每个任务做一次结构化输出测试（含耗时）
    python tools/llm_check.py --task location       # 只测某个任务
    python tools/llm_check.py --analyze MuirIsland  # 试分析一个场地（不写知识库）
    python tools/llm_check.py --analyze KunLun --save

每次测试只发一个极小的请求（几十 token），费用可忽略。
"""
from __future__ import annotations

import argparse
import sys
import time
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from brain import llm  # noqa: E402
from brain.locations import LocationKB  # noqa: E402

PING_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}, "reply": {"type": "string"}},
    "required": ["ok", "reply"],
    "additionalProperties": False,
}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--task", choices=list(llm.DEFAULT_TASKS), action="append")
    ap.add_argument("--analyze", metavar="LOCATION_DEF_ID")
    ap.add_argument("--save", action="store_true", help="--analyze 的结果写入 data/locations.json")
    args = ap.parse_args()

    settings = tomllib.loads((ROOT / "config" / "settings.toml").read_text("utf-8"))
    cfg = settings.get("llm", {})
    client = llm.make_client(settings)
    if client is None:
        print(f"未配置密钥：请在 {ROOT / '.env'} 中填写 {cfg.get('api_key_env', 'OPENAI_API_KEY')}"
              f"（可选 {cfg.get('base_url_env', 'OPENAI_BASE_URL')}），参考 .env.example")
        sys.exit(1)
    print(f"provider={client.provider} api={getattr(client, 'api', '-')} "
          f"base_url={getattr(getattr(client, 'client', None), 'base_url', '-')}")

    if client.provider == "openai":
        try:
            ids = sorted(m.id for m in client.client.models.list())
            wanted = {llm.task_config(settings, t).model for t in llm.DEFAULT_TASKS}
            print(f"端点共 {len(ids)} 个模型；配置所需：" + ", ".join(f"{w}{'✅' if w in ids else '❌'}" for w in sorted(wanted)))
            related = [i for i in ids if i.startswith(("gpt-6", "gpt-5.6", "gpt-5.5"))]
            if related:
                print("  相关型号：" + ", ".join(related))
        except Exception as e:  # noqa: BLE001
            print(f"列出模型失败（部分中转不支持 /models，可忽略）：{e!r}")

    if args.analyze:
        analyst = llm.LocationAnalyst(client, llm.task_config(settings, "location"))
        t = time.time()
        info = analyst.analyze(args.analyze)
        print(f"\n场地 {args.analyze}（{time.time() - t:.1f}s）：", info)
        if info and args.save:
            LocationKB().put(info)
            print("已写入 data/locations.json")
        return

    ok = True
    for name in args.task or list(llm.DEFAULT_TASKS):
        task = llm.task_config(settings, name)
        t = time.time()
        try:
            r = client.complete_json(task, "You are a connectivity test. Answer briefly.",
                                     "Reply with ok=true and reply='pong'.", PING_SCHEMA, "ping")
            print(f"✅ {name:9s} {task.model:16s} effort={task.effort:6s} {time.time() - t:5.1f}s  "
                  f"api={getattr(client, 'api', '-')}  {r}")
        except llm.LLMError as e:
            ok = False
            print(f"❌ {name:9s} {task.model:16s} effort={task.effort:6s} {time.time() - t:5.1f}s  {e}")
    sys.exit(0 if ok else 2)


if __name__ == "__main__":
    main()
