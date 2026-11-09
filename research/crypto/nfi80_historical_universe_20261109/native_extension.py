# ruff: noqa: E501, ISC004
"""Build a pinned isolated native engine with a small explicit historical extension."""

import difflib
import shutil
from pathlib import Path

from research.crypto.nfi80_historical_universe_20261109.prepare import (
    HERE,
    OUT,
    PLAN,
    read,
    ref,
    save,
    sha,
)


def build():
    source = Path(PLAN["engine_source"])
    engine = OUT / "engine"
    if engine.exists():
        raise FileExistsError("Do not alter a registered engine")
    upstream = read(PLAN["engine_manifest"]["path"])
    for name, digest in upstream["files"].items():
        if sha(source / name) != digest:
            raise ValueError("Pinned upstream differs: " + name)
    shutil.copytree(source, engine, ignore=shutil.ignore_patterns("__pycache__"))
    patches = {
        "freqtrade/data/dataprovider.py": [
            (
                "    def _set_dataframe_max_date(self, limit_date: datetime):",
                "    @property\n    def backtest_time(self) -> datetime | None:\n"
                '        """Current replay clock, exposed for causal historical pairlist plugins."""\n'
                "        return self.__slice_date\n\n"
                "    def _set_dataframe_max_date(self, limit_date: datetime):",
            )
        ],
        "freqtrade/optimize/backtesting.py": [
            (
                "            if self.dynamic_pairlist and self.pairlists:\n                self.pairlists.refresh_pairlist",
                "            if self.dynamic_pairlist and self.pairlists:\n"
                "                self.dataprovider._set_dataframe_max_date(current_time)\n"
                "                self.pairlists.refresh_pairlist",
            ),
            (
                "                    trade_dir = self.check_for_trade_entry(row)\n",
                "                    trade_dir = self.check_for_trade_entry(row)\n"
                "                    if self.dynamic_pairlist and pair not in pairs:\n"
                "                        trade_dir = None\n",
            ),
        ],
    }
    diff = []
    for name, replacements in patches.items():
        original = (engine / name).read_text()
        modified = original
        for old, new in replacements:
            if modified.count(old) != 1:
                raise ValueError("Patch does not uniquely match: " + name)
            modified = modified.replace(old, new)
        (engine / name).write_text(modified)
        diff.extend(
            difflib.unified_diff(
                original.splitlines(True),
                modified.splitlines(True),
                fromfile="a/" + name,
                tofile="b/" + name,
            )
        )
    (HERE / "native.patch").write_text("".join(diff))
    plugin = "freqtrade/plugins/pairlist/HistoricalVolumePairList.py"
    shutil.copy2(HERE / "HistoricalVolumePairList.py", engine / plugin)
    files = {n: sha(engine / n) for n in upstream["files"]}
    files[plugin] = sha(engine / plugin)
    save(
        HERE / "engine-manifest.json",
        {
            **{k: v for k, v in upstream.items() if k != "files"},
            "files": files,
            "official_unmodified": False,
            "base_manifest": PLAN["engine_manifest"],
            "local_extension": "Historical pairlist inputs and replay clock; native financial engine",
            "modified_upstream_files": list(patches),
            "added_files": [plugin],
            "patch": ref(HERE / "native.patch"),
            "plugin": ref(HERE / "HistoricalVolumePairList.py"),
        },
    )
    print(
        {
            "engine": str(engine),
            "base_commit": upstream["commit"],
            "modified_files": len(patches),
            "plugin": plugin,
        }
    )


if __name__ == "__main__":
    build()
