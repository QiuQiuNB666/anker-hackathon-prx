#!/usr/bin/env python3
"""轮询安克黑客松官方资料，只在内容变化时向 stdout 发一行事件。

需要一个飞书自建应用（开通 bitable:app 与 drive:drive 权限）：
    export LARK_APP_ID=cli_xxxx
    export LARK_APP_SECRET=xxxx
    python3 watch_anker.py

指纹只比对字段的文本值，忽略飞书 link/mention 结构的格式抖动，避免官方
重新编辑一个链接字段就误报成内容更新。
"""
import subprocess, json, hashlib, time, os, sys

APP = "GHLSbtHaWa3VKgs79fqccxmUn7b"
APP_ID = os.environ.get("LARK_APP_ID", "")      # 飞书自建应用凭证，从环境变量读
APP_SECRET = os.environ.get("LARK_APP_SECRET", "")
RULES_DOC = "UyMOd96PMoW6WaxboP6cgFNIn8I"
STATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".anker_watch_state.json")
INTERVAL = 600  # 活跃录入窗口已过，回到 10 分钟

TABLES = [
    ("赛前赋能表", "tblNtnQs0v1bECcU"),   # 直播资料最可能落在这
    ("学习资料表", "tblaj5LKLs8XPdYR"),   # 预赛模板可能新增在这
    ("FAQ常见问题表", "tblnGxdWWteuGq5D"),
    ("入围名单表", "tbl7Jo7dncpHvzZu"),
]


def sh(args, data=None):
    try:
        p = subprocess.run(args, capture_output=True, input=data, timeout=45)
        return p.stdout.decode("utf-8", "replace")
    except Exception:
        return ""


def jload(s):
    try:
        return json.loads(s, strict=False)
    except Exception:
        return {}


def token():
    if not APP_ID or not APP_SECRET:
        raise SystemExit("请先设置 LARK_APP_ID 与 LARK_APP_SECRET 环境变量")
    body = json.dumps({"app_id": APP_ID, "app_secret": APP_SECRET}).encode()
    d = jload(sh(["curl", "-s", "-X", "POST",
                  "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
                  "-H", "Content-Type: application/json", "-d", "@-"], body))
    return d.get("tenant_access_token", "")


def flatten(fields):
    """把一条记录压成可比的纯文本，丢掉飞书的结构包装（link/mention/附件对象）。"""
    def txt(v):
        if isinstance(v, list):
            return " | ".join(txt(x) for x in v)
        if isinstance(v, dict):
            for k in ("text", "name", "link"):
                if k in v:
                    return str(v[k])
            return json.dumps(v, sort_keys=True, ensure_ascii=False)
        return str(v)
    return "; ".join(f"{k}={txt(v)}" for k, v in sorted(fields.items()))


def probe(tok):
    """返回 {目标: (指纹, 摘要)}。任何一项失败就跳过该项，不影响其它。"""
    out = {}
    auth = f"Authorization: Bearer {tok}"
    for name, tid in TABLES:
        d = jload(sh(["curl", "-s", "-H", auth,
                      f"https://open.feishu.cn/open-apis/bitable/v1/apps/{APP}/tables/{tid}/records?page_size=200"]))
        if d.get("code") != 0:
            continue
        items = d.get("data", {}).get("items", [])
        # 指纹只取字段的文本值：官方重新编辑一个链接字段会改变存储结构但内容没变，
        # 直接 hash 原始 JSON 会把这种格式抖动报成更新。
        blob = json.dumps(sorted(flatten(i.get("fields", {})) for i in items), ensure_ascii=False)
        out[name] = (hashlib.md5(blob.encode()).hexdigest(), f"{len(items)} 条记录")

    d = jload(sh(["curl", "-s", "-H", auth,
                  f"https://open.feishu.cn/open-apis/docx/v1/documents/{RULES_DOC}/raw_content"]))
    if d.get("code") == 0:
        c = d.get("data", {}).get("content", "")
        out["赛事细则文档"] = (hashlib.md5(c.encode()).hexdigest(), f"{len(c)} 字")
    return out


def main():
    state = {}
    if os.path.exists(STATE):
        try:
            state = json.load(open(STATE))
        except Exception:
            state = {}
    first = not state

    while True:
        tok = token()
        if not tok:
            time.sleep(INTERVAL)
            continue

        cur = probe(tok)
        if cur:
            if first:
                print(f"[基线] 已建立监控基线：" + "，".join(f"{k} {v[1]}" for k, v in cur.items()), flush=True)
                first = False
            else:
                for name, (h, summ) in cur.items():
                    old = state.get(name)
                    if old is None:
                        print(f"🆕 新出现监控目标：{name}（{summ}）", flush=True)
                    elif old[0] != h:
                        print(f"🔔 官方资料更新：{name} —— {old[1]} → {summ}", flush=True)
            state.update({k: list(v) for k, v in cur.items()})
            json.dump(state, open(STATE, "w"))

        time.sleep(INTERVAL)


if __name__ == "__main__":
    main()
