import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rental_group_matching import match_rental, towers


def test_tower_range_expansion():
    assert towers("1, 19-25座") == {1, 19, 20, 21, 22, 23, 24, 25}
    assert towers("16,17,20 & 23座") == {16, 17, 20, 23}
    assert towers("3,5-6座") == {3, 5, 6}


def test_taikoo_garden_priority():
    assert match_rental("太古城 海景花園(東) 美菊閣 高層 F室").group_id == "HMA034:EA0044"
    assert match_rental("太古城 海景花園(西) 青松閣").group_id == "HMA034:EA0046"
    assert match_rental("太古城 高山台 泰山閣").group_id == "HMA034:EA0030"
    assert match_rental("太古城 海天花園 景天閣").group_id == "HMA034:EA0039"


def test_hui_xi_phase_mapping():
    assert match_rental("匯璽 3期(匯璽II) 5B座").group_id == "HMA081:SP0193"
    assert match_rental("匯璽 5期(匯璽III) 8座").group_id == "HMA081:SP0194"
    assert match_rental("匯璽 2A期 2A座").group_id == "HMA081:SP0192"
    assert match_rental("匯璽 3期 III 5B座").group_id is None


def test_other_estate_rules():
    assert match_rental("日出康城 Malibu 2座").group_id == "HMA114:SK0195"
    assert match_rental("映灣園 2期 7座").group_id == "HMA174:IS0005"
    assert match_rental("珀麗灣 1期 09座").group_id == "HMA109:TW0084"
    assert match_rental("沙田第一城 1期 08座").group_id == "HMA062:ST0025"
    assert match_rental("沙田第一城 2期 20座").group_id == "HMA062:ST0027"
    assert match_rental("沙田第一城 6期 24座").group_id == "HMA062:ST0034"
