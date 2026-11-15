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
