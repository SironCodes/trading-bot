"""python -m bot.killswitch [--env paper|live] [--dry-run] [--reset] [--db PATH]

Cancels every open order and flattens every position on every configured broker, then latches the bot
so the runner opens nothing new until `--reset`. Alpaca flattens immediately with market orders; the
TSX SimulatedBroker queues market sells that fill at the next session's open. Exit code 1 if any broker
failed (the others are still flattened)."""
import argparse
import logging
import os

import pandas as pd
from dotenv import load_dotenv

from bot.config import ROOT, load_settings, resolve_env
from bot.execution.manager import OrderManager
from bot.execution.router import BrokerRouter
from bot.storage import log as logsetup
from bot.storage.db import connect, set_halted

log = logging.getLogger("bot.killswitch")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m bot.killswitch", description=__doc__)
    p.add_argument("--env", choices=["paper", "live"])
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--reset", action="store_true", help="clear the latch so the runner may open positions again")
    p.add_argument("--db")
    a = p.parse_args(argv)
    load_dotenv(ROOT / ".env")
    s = load_settings()
    env = resolve_env(a.env, s.mode, os.environ)
    logsetup.setup(ROOT / s.paths.logs, s.logging.level, os.environ)
    conn, now = connect(a.db or ROOT / s.paths.db.format(env=env)), pd.Timestamp.now(tz="UTC")
    if a.reset:
        set_halted(conn, False, now)
        print("kill-switch latch cleared: the runner may open new positions again")
        return 0
    router = BrokerRouter.from_settings(s, env, conn)
    om = OrderManager(env, router, conn, s, dry_run=a.dry_run)
    report = om.kill(now)
    if not a.dry_run:
        om.reconcile(now)  # record the cancellations locally
    for name, result in report.items():
        print(f"{name}: {result}")
        log.warning("kill switch %s: %s", name, result)
    print("latched: no new entries until `python -m bot.killswitch --reset`" if not a.dry_run else "dry run: nothing sent")
    return 1 if any(r.startswith("FAILED") for r in report.values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
