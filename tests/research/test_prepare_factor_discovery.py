"""Archive schema and true aggressor volume are not silently synthesized."""

import zipfile

import pytest

from research.crypto.prepare_factor_discovery import read_flow


def test_headerless_archive_keeps_real_aggressor_volume(tmp_path):
    p = tmp_path / "real.zip"
    with zipfile.ZipFile(p, "w") as archive:
        archive.writestr(
            "real.csv", "1735689600000,100,101,99,100,10,1735689899999,1000,20,8,800,0\n"
        )
    f = read_flow(p)
    assert f.close.iloc[0] == f.open.iloc[0]
    assert f.taker_buy_quote_volume.iloc[0] == 800
    assert (2 * f.taker_buy_quote_volume / f.quote_volume - 1).iloc[0] == pytest.approx(0.6)


def test_invalid_aggressor_quote_is_rejected(tmp_path):
    p = tmp_path / "bad.zip"
    with zipfile.ZipFile(p, "w") as archive:
        archive.writestr(
            "bad.csv", "1735689600000,100,101,99,100,10,1735689899999,1000,20,8,1001,0\n"
        )
    with pytest.raises(ValueError, match="aggressor"):
        read_flow(p)
