"""Research code must never reach the order-placing code: only bot.execution, bot.runner and
bot.killswitch may import bot.execution, and only the Alpaca adapter may import alpaca.trading."""
import ast
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOT = ROOT / "bot"
MAY_IMPORT_EXECUTION = ("bot/execution/", "bot/runner/", "bot/killswitch.py")
MAY_IMPORT_ALPACA_TRADING = ("bot/execution/brokers/alpaca.py",)
RESEARCH = ("strategies", "fusion", "sentiment", "regime", "backtest")


def imported_modules(path: Path):
    tree = ast.parse(path.read_text())
    package = ".".join(path.relative_to(ROOT).with_suffix("").parts[:-1])
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            yield from (a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # resolve relative imports against the module's package
                base = package.split(".")[: len(package.split(".")) - node.level + 1]
                yield ".".join(base + ([node.module] if node.module else []))
            else:
                yield node.module or ""


def violations(prefix: str, allowed: tuple):
    for path in BOT.rglob("*.py"):
        rel = path.relative_to(ROOT).as_posix()
        if rel.startswith(allowed):
            continue
        for mod in imported_modules(path):
            if mod == prefix or mod.startswith(prefix + "."):
                yield f"{rel} imports {mod}"


def test_only_execution_runner_and_killswitch_import_execution():
    assert list(violations("bot.execution", MAY_IMPORT_EXECUTION)) == []


def test_only_the_alpaca_adapter_imports_alpaca_trading():
    assert list(violations("alpaca.trading", MAY_IMPORT_ALPACA_TRADING)) == []


def test_research_packages_exist_and_are_scanned():
    for pkg in RESEARCH:
        assert any((BOT / pkg).rglob("*.py")), pkg


def test_running_a_backtest_loads_no_execution_code():
    code = ("import sys, bot.backtest.__main__, bot.backtest.engine, bot.pipeline, bot.sentiment.aggregate, "
            "bot.regime.macro, bot.fusion.fusion, bot.strategies.sma_trend\n"
            "bad = [m for m in sys.modules if m.startswith(('bot.execution', 'alpaca.trading'))]\n"
            "print(bad); sys.exit(1 if bad else 0)")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0, out.stdout + out.stderr
