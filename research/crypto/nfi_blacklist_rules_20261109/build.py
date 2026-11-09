"""Pin the native historical extension without mutating an existing engine."""

import difflib
import shutil
from pathlib import Path

from research.crypto.nfi_blacklist_rules_20261109.inputs import (
    HERE,
    OUT,
    ROOT,
    read,
    ref,
    save,
    sha,
)


def main():
    source = ROOT / "user_data/crypto_quant/nfi80_historical_universe_20261109_v001/engine_v002"
    original = ROOT / "research/crypto/nfi80_historical_universe_20261109/v002/engine-manifest.json"
    engine = OUT / "engine"
    if engine.exists():
        raise FileExistsError("Preserve existing native extension")
    prior = read(original)
    for n, h in prior["files"].items():
        if sha(source / n) != h:
            raise ValueError("Prior native extension differs")
    shutil.copytree(source, engine, ignore=shutil.ignore_patterns("__pycache__"))
    plugin = "freqtrade/plugins/pairlist/HistoricalVolumePairList.py"
    shutil.copy2(HERE / "HistoricalVolumePairList.py", engine / plugin)
    prior["files"][plugin] = sha(engine / plugin)
    upstream = read(prior["base_manifest"]["path"])
    from research.crypto.nfi80_historical_universe_20261109.prepare import PLAN

    base = Path(PLAN["engine_source"])
    patch = []
    for name in prior["modified_upstream_files"]:
        patch.extend(
            difflib.unified_diff(
                (base / name).read_text().splitlines(True),
                (engine / name).read_text().splitlines(True),
                fromfile="a/" + name,
                tofile="b/" + name,
            )
        )
    (HERE / "native.patch").write_text("".join(patch))
    prior.update(
        plugin=ref(HERE / "HistoricalVolumePairList.py"),
        patch=ref(HERE / "native.patch"),
        prior_extension_manifest=ref(original),
        upstream_filter_sources={
            name: upstream["files"][name]
            for name in [
                "freqtrade/plugins/pairlist/rangestabilityfilter.py",
                "freqtrade/plugins/pairlist/VolatilityFilter.py",
            ]
        },
        local_extension=(
            "Historical candle bridge; unchanged native range/volatility calculations "
            "and native financial engine"
        ),
    )
    save(HERE / "engine-manifest.json", prior)


if __name__ == "__main__":
    main()
