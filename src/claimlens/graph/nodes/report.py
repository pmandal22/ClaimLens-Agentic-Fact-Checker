"""Render the final cited report."""

from collections.abc import Mapping, Sequence

from claimlens.domain.schemas import Claim, Evidence, Verdict
from claimlens.graph.nodes.aggregate import OverallVerdict, in_claim_order

LABEL_TEXT = {
    "supported": "Supported",
    "refuted": "Refuted",
    "misleading": "Misleading",
    "nei": "Not enough evidence",
}
DISCLAIMER = (
    "Verdicts are automated and may be wrong. They rate the claims, not the person making "
    "them; follow the sources to check the reasoning."
)


def render_report(
    claims: Sequence[Claim],
    verdicts: Sequence[Verdict],
    evidence: Mapping[str, Sequence[Evidence]],
    overall: OverallVerdict,
) -> str:
    """Markdown report: overall rating, then each claim with its verdict and citations."""
    titles = {e.url: e.title for items in evidence.values() for e in items}
    lines = [
        f"# Overall: {overall.rating.replace('_', ' ')}",
        "",
        overall.summary,
        "",
    ]
    for claim, verdict in zip(claims, in_claim_order(claims, verdicts), strict=True):
        at = f" (at {claim.timestamp_s:.0f}s)" if claim.timestamp_s is not None else ""
        lines.append(f"## {claim.id}: {claim.text}{at}")
        if verdict is None:
            lines += ["", "**Not checked.**", ""]
            continue
        heading = f"**{LABEL_TEXT[verdict.label]}**"
        # An abstention with no stance-taking evidence was never scored by the judge.
        if verdict.label != "nei" or any(
            e.stance in ("supports", "refutes") for e in evidence.get(claim.id, [])
        ):
            heading += f" · confidence {verdict.confidence:.0%}"
        lines += ["", heading]
        lines += ["", verdict.rationale, ""]
        for url in verdict.citations:
            lines.append(f"- [{titles.get(url, url)}]({url})")
        if verdict.citations:
            lines.append("")
    lines.append(f"_{DISCLAIMER}_")
    return "\n".join(lines) + "\n"
