"""Board vote record and revealed hawk/dove stance from statement dissents. Pure functions.

Stance is *revealed preference*: only meetings where a member dissented carry information about
their individual view (unanimous statements don't name members). gap_bp = preferred change −
decided change; positive = hawkish (wanted tighter policy), negative = dovish.
"""

from __future__ import annotations

import pandas as pd

from monitor.models.statements import ParsedStatement

DECISION_COLUMNS = [
    "meeting_date",
    "meeting_id",
    "change_bp",
    "new_rate",
    "unanimous",
    "split",
    "dissent_summary",
]
MEMBER_COLUMNS = [
    "meeting_date",
    "meeting_id",
    "member",
    "role",
    "decided_bp",
    "preferred_bp",
    "gap_bp",
    "dissent",
]


def vote_record(parsed: list[ParsedStatement]) -> pd.DataFrame:
    rows = []
    for p in sorted(parsed, key=lambda x: x.meeting_date):
        v = p.vote
        if v.unanimous:
            split, summary = "unanimous", ""
        else:
            n_dis = len(v.dissents)
            split = f"{len(v.votes) - n_dis}–{n_dis}"
            summary = "; ".join(f"{d.role} {d.name}: {d.preferred_bp:+d}bp" for d in v.dissents)
        rows.append(
            (
                p.meeting_date,
                p.meeting_id,
                p.decision.change_bp,
                p.decision.new_rate,
                v.unanimous,
                split,
                summary,
            )
        )
    return pd.DataFrame(rows, columns=DECISION_COLUMNS)


def member_votes(parsed: list[ParsedStatement]) -> pd.DataFrame:
    rows = []
    for p in parsed:
        for mv in p.vote.votes:
            rows.append(
                (
                    p.meeting_date,
                    p.meeting_id,
                    mv.name,
                    mv.role,
                    p.decision.change_bp,
                    mv.preferred_bp,
                    mv.preferred_bp - p.decision.change_bp,
                    mv.dissent,
                )
            )
    return pd.DataFrame(rows, columns=MEMBER_COLUMNS).sort_values(["meeting_date", "member"])


def stance_label(n_hawk: int, n_dove: int, net_bp: float) -> str:
    if n_hawk == 0 and n_dove == 0:
        return "No recorded dissents"
    if net_bp > 0:
        return "Hawkish-leaning"
    if net_bp < 0:
        return "Dovish-leaning"
    return "Mixed"


def member_stance(votes: pd.DataFrame) -> pd.DataFrame:
    """Per member: named votes, dissents by direction, net and average dissent gap."""
    if votes.empty:
        return pd.DataFrame(
            columns=[
                "member",
                "last_role",
                "named_votes",
                "dissents",
                "hawkish",
                "dovish",
                "net_gap_bp",
                "avg_gap_bp",
                "first_seen",
                "last_seen",
                "stance",
            ]
        )
    rows = []
    for member, g in votes.groupby("member"):
        d = g[g["dissent"]]
        hawk, dove = int((d["gap_bp"] > 0).sum()), int((d["gap_bp"] < 0).sum())
        net = float(d["gap_bp"].sum())
        rows.append(
            {
                "member": member,
                "last_role": g.sort_values("meeting_date")["role"].iloc[-1],
                "named_votes": len(g),
                "dissents": len(d),
                "hawkish": hawk,
                "dovish": dove,
                "net_gap_bp": net,
                "avg_gap_bp": float(d["gap_bp"].mean()) if len(d) else 0.0,
                "first_seen": g["meeting_date"].min(),
                "last_seen": g["meeting_date"].max(),
                "stance": stance_label(hawk, dove, net),
            }
        )
    return pd.DataFrame(rows).sort_values(["dissents", "member"], ascending=[False, True])


# ------------------------------------------------------------------ board view


def current_board(
    authorities: pd.DataFrame,
    year: int,
) -> pd.DataFrame:
    """Current members (open-ended Board rows) with their current role."""
    if authorities.empty:
        return pd.DataFrame(
            columns=[
                "surname_key",
                "full_name",
                "role_now",
                "board_since",
                "role_since",
                "years_on_board",
            ]
        )
    open_rows = authorities[authorities["end_year"].isna()]
    members = open_rows[open_rows["role"] == "Board member"]
    rows = []
    for m in members.itertuples(index=False):
        role_now, role_since = "Board member", m.start_year
        for role in ("Governor", "Vice-Governor"):
            hit = open_rows[
                (open_rows["role"] == role) & (open_rows["surname_key"] == m.surname_key)
            ]
            if not hit.empty:
                role_now, role_since = role, int(hit["start_year"].iloc[0])
        rows.append(
            {
                "surname_key": m.surname_key,
                "full_name": m.full_name,
                "role_now": role_now,
                "board_since": int(m.start_year),
                "role_since": role_since,
                "years_on_board": year - int(m.start_year),
            }
        )
    order = {"Governor": 0, "Vice-Governor": 1, "Board member": 2}
    df = pd.DataFrame(rows)
    return df.sort_values(
        ["role_now", "board_since"], key=lambda c: c.map(order) if c.name == "role_now" else c
    )


def attach_stance(
    board: pd.DataFrame, stance: pd.DataFrame, aliases: dict[str, str]
) -> pd.DataFrame:
    """Join statement-based stance onto board rows by surname (statement name or alias)."""
    st = stance.copy()
    st["key"] = st["member"].map(lambda n: aliases.get(n, n))
    cols = ["dissents", "hawkish", "dovish", "net_gap_bp", "named_votes", "stance", "member"]
    out = board.merge(st[["key", *cols]], left_on="surname_key", right_on="key", how="left")
    out["stance"] = out["stance"].fillna("No named votes in statements")
    return out.drop(columns=["key"])


def _term(a: pd.DataFrame) -> str:
    if a.empty:
        return "not found in authorities list"
    start, end = a["start_year"].iloc[0], a["end_year"].iloc[0]
    return f"{int(start)}–{int(end) if pd.notna(end) else 'present'}"


def former_in_statements(
    stance: pd.DataFrame, board: pd.DataFrame, authorities: pd.DataFrame, aliases: dict[str, str]
) -> pd.DataFrame:
    """Members named in statements who are not on the current Board."""
    current = set(board["surname_key"])
    rows = []
    for r in stance.itertuples(index=False):
        key = aliases.get(r.member, r.member)
        if key in current:
            continue
        a = authorities[
            (authorities["surname_key"] == key) & (authorities["role"] == "Board member")
        ]
        term = _term(a)
        rows.append(
            {
                "member": r.member,
                "full_name": a["full_name"].iloc[0] if not a.empty else "",
                "board_term": term,
                "last_role_in_statements": r.last_role,
                "dissents": r.dissents,
                "hawkish": r.hawkish,
                "dovish": r.dovish,
                "net_gap_bp": r.net_gap_bp,
                "stance": r.stance,
            }
        )
    return pd.DataFrame(rows)
