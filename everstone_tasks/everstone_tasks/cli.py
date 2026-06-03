import argparse, json, os, sys
from datetime import datetime
from typing import Optional
from .client import TasksClient
from .deeplink import build_deeplink


def _client(env):
    url = env.get("EVERSTONE_CALDAV_URL")
    if not url:
        raise SystemExit("EVERSTONE_CALDAV_URL not set")
    return TasksClient(url, env.get("EVERSTONE_CALDAV_USER", ""), env.get("EVERSTONE_CALDAV_PASSWORD", ""))


def _parser():
    p = argparse.ArgumentParser(prog="everstone-tasks")
    p.add_argument("--json", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)
    pl = sub.add_parser("list")
    pl.add_argument("--list", dest="list_name", default="inbox")
    pl.add_argument("--json", action="store_true")
    pa = sub.add_parser("add"); pa.add_argument("summary")
    pa.add_argument("--list", dest="list_name", default="inbox")
    pa.add_argument("--note", default=None)
    pa.add_argument("--remind-at", dest="remind_at", default=None)
    pa.add_argument("--json", action="store_true")
    pd = sub.add_parser("done"); pd.add_argument("uid")
    pd.add_argument("--list", dest="list_name", default="inbox")
    pd.add_argument("--json", action="store_true")
    pk = sub.add_parser("link"); pk.add_argument("uid"); pk.add_argument("--note", required=True)
    pk.add_argument("--list", dest="list_name", default="inbox")
    pk.add_argument("--json", action="store_true")
    return p


def main(argv: Optional[list] = None, env: Optional[dict] = None) -> int:
    env = os.environ if env is None else env
    a = _parser().parse_args(argv); c = _client(env)
    if a.cmd == "list":
        ts = c.list_tasks(a.list_name)
        print(json.dumps(ts) if a.json else "\n".join(
            f"[{'x' if t['status']=='COMPLETED' else ' '}] {t['summary']} ({t['uid']})" for t in ts))
        return 0
    if a.cmd == "add":
        url = build_deeplink(env["EVERSTONE_VAULT_NAME"], a.note) if a.note else None
        remind = datetime.fromisoformat(a.remind_at) if a.remind_at else None
        uid = c.add_task(a.summary, a.list_name, url=url, remind_at=remind)
        print(json.dumps({"uid": uid}) if a.json else uid); return 0
    if a.cmd == "done":
        c.complete_task(a.uid, a.list_name)
        if a.json:
            print(json.dumps({"uid": a.uid, "status": "COMPLETED"}))
        return 0
    if a.cmd == "link":
        url = build_deeplink(env["EVERSTONE_VAULT_NAME"], a.note)
        c.set_note_link(a.uid, a.list_name, url)
        if a.json:
            print(json.dumps({"uid": a.uid, "url": url}))
        return 0
    return 1
