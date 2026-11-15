from datetime import date

import pytest

from monitor.models.statements import (
    decision,
    meeting_date,
    normalize,
    parse_statement,
    standard_filename,
    vote,
)


def votes_of(p):
    return {(v.role, v.name): (v.preferred_bp, v.dissent) for v in p.vote.votes}


@pytest.mark.parametrize(
    ("fixture", "d", "bp", "rate"),
    [
        ("stmt_2022-09-06_two_dissents.txt", date(2022, 9, 6), 100, 10.75),
        ("stmt_2023-06-19_hold_dissent.txt", date(2023, 6, 19), 0, 11.25),
        ("stmt_2024-01-31_dissent.txt", date(2024, 1, 31), -100, 7.25),
        ("stmt_2024-06-18_dissent.txt", date(2024, 6, 18), -25, 5.75),
        ("stmt_2025-12-16_us_date.txt", date(2025, 12, 16), -25, 4.5),
        ("stmt_2022-03-29_ordinal.txt", date(2022, 3, 29), 150, 7.0),
        ("stmt_2021-05-13_comma.txt", date(2021, 5, 13), 0, 0.5),
        ("stmt_2022-01-26_present.txt", date(2022, 1, 26), 150, 5.5),
    ],
)
def test_date_and_decision(load_text_fixture, fixture, d, bp, rate):
    t = load_text_fixture(fixture)
    assert meeting_date(t) == d
    dec = decision(t)
    assert (dec.change_bp, dec.new_rate) == (bp, rate)


def test_unanimous_variants(load_text_fixture):
    for f in ["stmt_2025-12-16_us_date.txt", "stmt_2022-01-26_present.txt"]:
        p = parse_statement(load_text_fixture(f))
        assert p.vote.unanimous and p.vote.votes == []


def test_two_dissents_opposite_directions(load_text_fixture):
    p = parse_statement(load_text_fixture("stmt_2022-09-06_two_dissents.txt"))
    v = votes_of(p)
    assert v[("Governor", "Costa")] == (100, False)
    assert v[("Board member", "Naudon")] == (100, False)
    assert v[("Vice-Governor", "García")] == (125, True)
    assert v[("Board member", "Griffith-Jones")] == (75, True)
    assert len(v) == 5


def test_joint_dissent_on_hold(load_text_fixture):
    p = parse_statement(load_text_fixture("stmt_2023-06-19_hold_dissent.txt"))
    v = votes_of(p)
    assert v[("Vice-Governor", "García")] == (-50, True)
    assert v[("Board member", "Griffith-Jones")] == (-50, True)
    assert v[("Governor", "Costa")] == (0, False)


def test_vice_governor_in_majority(load_text_fixture):
    p = parse_statement(load_text_fixture("stmt_2024-01-31_dissent.txt"))
    v = votes_of(p)
    assert v[("Vice-Governor", "Griffith-Jones")] == (-100, False)
    assert v[("Board member", "Céspedes")] == (-125, True)
    assert p.meeting_id == "RPM-2024-01"


def test_watermark_removed(load_text_fixture):
    t = normalize(load_text_fixture("stmt_2024-06-18_dissent.txt"))
    assert t.startswith("Tuesday, 18 June 2024")


def test_fail_loudly():
    with pytest.raises(ValueError):
        meeting_date("no date here")
    with pytest.raises(ValueError):
        decision("Tuesday, 1 April 2025 The Board did something.")
    with pytest.raises(ValueError):
        vote("The decision was adopted with the votes of Governor Costa.", 0)  # no dissent


def test_standard_filename():
    assert standard_filename(date(2024, 6, 18)) == "rpm_2024-06-18_statement_en.pdf"


def test_board_stance_from_real_dissents(load_text_fixture):
    from monitor.models.board import member_stance, member_votes, vote_record

    parsed = [
        parse_statement(load_text_fixture(f))
        for f in [
            "stmt_2022-09-06_two_dissents.txt",
            "stmt_2023-06-19_hold_dissent.txt",
            "stmt_2024-01-31_dissent.txt",
            "stmt_2024-06-18_dissent.txt",
            "stmt_2025-12-16_us_date.txt",
        ]
    ]
    rec = vote_record(parsed)
    assert rec["split"].tolist() == ["3–2", "3–2", "4–1", "4–1", "unanimous"]
    st = member_stance(member_votes(parsed)).set_index("member")
    # Griffith-Jones: dovish in Sep-22 (75 vs 100), Jun-23 (-50 vs 0), Jun-24 (-50 vs -25)
    assert st.loc["Griffith-Jones", "dovish"] == 3 and st.loc["Griffith-Jones", "hawkish"] == 0
    assert st.loc["Griffith-Jones", "stance"] == "Dovish-leaning"
    # García: hawkish Sep-22 (+25), dovish Jun-23 (-50) -> net -25
    assert (st.loc["García", "hawkish"], st.loc["García", "dovish"]) == (1, 1)
    assert st.loc["García", "net_gap_bp"] == -25
    assert st.loc["Costa", "stance"] == "No recorded dissents"
    assert st.loc["Céspedes", "net_gap_bp"] == -25
