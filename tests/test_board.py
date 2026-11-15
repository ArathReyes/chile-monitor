from pathlib import Path

import pytest

from monitor.sources.board import (
    find_photo,
    is_image,
    parse_authorities,
    parse_person_page,
    surname_key,
)


@pytest.mark.parametrize(
    ("full", "key"),
    [
        ("Rosanna Costa Costa", "Costa"),
        ("Luis Felipe Céspedes Cifuentes", "Céspedes"),
        ("Alberto Naudon Dell'Oro", "Naudon"),
        ("Kevin Cowan Logan", "Cowan"),
        ("Kevin Cowan", "Cowan"),
        ("José De Gregorio Rebeco", "De Gregorio"),
    ],
)
def test_surname_key(full, key):
    assert surname_key(full) == key


def test_parse_authorities(load_text_fixture):
    df = parse_authorities(load_text_fixture("bcch_authorities_history.txt"))
    cur = df[df["end_year"].isna()]
    board = cur[cur["role"] == "Board member"].set_index("surname_key")["start_year"].to_dict()
    assert board == {"Costa": 2017, "Naudon": 2018, "Céspedes": 2022, "Soto": 2024, "Cowan": 2026}
    assert cur[cur["role"] == "Governor"]["full_name"].tolist() == ["Rosanna Costa Costa"]
    assert cur[cur["role"] == "Vice-Governor"]["surname_key"].tolist() == ["Naudon"]
    garcia = df[(df["surname_key"] == "García") & (df["role"] == "Board member")].iloc[0]
    assert (garcia["start_year"], garcia["end_year"]) == (2014, 2024)


def test_parse_authorities_layout_change():
    with pytest.raises(ValueError):
        parse_authorities("no sections here\n2017 - Someone")


def test_parse_person_page(load_text_fixture):
    p = parse_person_page(load_text_fixture("bcch_person_page_cowan.html"))
    assert p.name == "Kevin Cowan" and p.title == "Board Member"
    assert "January 2026" in p.bio
    assert len(p.education) == 1 and "PhD in economics" in p.education[0]
    assert "Catholic University of Chile" in p.education[0]
    assert p.education[0].startswith("Mr. Cowan holds a degree")


def test_person_page_layout_change():
    with pytest.raises(ValueError):
        parse_person_page("<html><body><p>nothing</p></body></html>")


def test_photo_must_be_a_real_image(tmp_path: Path):
    photos = tmp_path / "photos"
    photos.mkdir()
    (photos / "cowan.jpg").write_bytes(b"<html>Incapsula challenge</html>")
    assert find_photo("Cowan", tmp_path) is None
    (photos / "costa.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 20)
    assert find_photo("Costa", tmp_path) == photos / "costa.png"
    assert is_image(photos / "costa.png")


def test_current_board_and_stance_join(load_text_fixture):
    import pandas as pd

    from monitor.models.board import attach_stance, current_board, former_in_statements

    auth = parse_authorities(load_text_fixture("bcch_authorities_history.txt"))
    board = current_board(auth, 2026)
    assert board["surname_key"].tolist()[:2] == ["Costa", "Naudon"]
    assert board.set_index("surname_key").loc["Naudon", "role_now"] == "Vice-Governor"
    assert board.set_index("surname_key").loc["Cowan", "years_on_board"] == 0
    stance = pd.DataFrame(
        {"member": ["Costa", "García", "Griffith-Jones"], "last_role": ["Governor"] * 3,
         "dissents": [0, 2, 3], "hawkish": [0, 1, 0], "dovish": [0, 1, 3],
         "net_gap_bp": [0.0, -25.0, -100.0], "named_votes": [4, 2, 4],
         "stance": ["No recorded dissents", "Dovish-leaning", "Dovish-leaning"]}
    )  # fmt: skip
    joined = attach_stance(board, stance, {}).set_index("surname_key")
    assert joined.loc["Costa", "stance"] == "No recorded dissents"
    assert joined.loc["Cowan", "stance"] == "No named votes in statements"
    former = former_in_statements(stance, board, auth, {}).set_index("member")
    assert former.loc["García", "board_term"] == "2014–2024"
    assert former.loc["Griffith-Jones", "board_term"] == "not found in authorities list"
    aliased = former_in_statements(stance, board, auth, {"Griffith-Jones": "Novy"})
    assert aliased.set_index("member").loc["Griffith-Jones", "board_term"] == "2022–2025"


def test_match_profile_and_education_filter():
    from monitor.sources.board import Profile, match_profile

    profs = [
        Profile("Luis Felipe Céspedes", "Board Member", ""),
        Profile("Rosanna Costa", "Governor", ""),
    ]
    assert match_profile("Luis Felipe Céspedes Cifuentes", profs).name == "Luis Felipe Céspedes"
    assert match_profile("Rosanna Costa Costa", profs).name == "Rosanna Costa"
    assert match_profile("Claudio Soto Gamboa", profs) is None
    html = """<html><body><div class="info-investigador"><p class="f-opensans-bold">X Y</p>
    <p>Board Member</p><p>Mr. X holds an M.S. from Universidad de Chile and a Ph.D. from NYU.
    He has taught graduate courses in macroeconomics.
    Other roles include Member of the Council of the Master’s Program at UAI.</p>
    </div></body></html>"""
    p = parse_person_page(html)
    assert len(p.education) == 1 and "Ph.D." in p.education[0]


def test_statement_tone_mapping():
    import pandas as pd

    from monitor.models.board import statement_tone, tone_label

    meetings = [pd.Timestamp("2026-09-08").date(), pd.Timestamp("2026-10-27").date()]
    tone = pd.Series([5.0, 8.0], index=pd.to_datetime(["2026-09-24", "2026-11-11"]))
    df = statement_tone(meetings, tone)
    assert df["tone"].tolist() == [5.0, 8.0]
    assert df["tone_label"].tolist() == ["Neutral", "Hawkish"]
    assert tone_label(4.0) == "Mildly dovish" and tone_label(6.0) == "Mildly hawkish"
    # a reading after the next meeting is not attributed to the earlier one
    later = [pd.Timestamp("2026-09-08").date(), pd.Timestamp("2026-09-20").date()]
    df2 = statement_tone(later, tone.iloc[:1])
    assert df2["meeting_date"].tolist() == [pd.Timestamp("2026-09-20").date()]
