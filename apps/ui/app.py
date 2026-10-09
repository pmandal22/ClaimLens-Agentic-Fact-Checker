"""Streamlit demo: submit a reel URL, watch the job, read the verdicts.

A thin client of the API (apps/api), so it shows exactly what the API returns.
Run the API and a worker first, then:  make ui
"""

import os
from typing import Any

import httpx
import streamlit as st

API_URL = os.getenv("CLAIMLENS_API_URL", "http://localhost:8000").rstrip("/")
POLL_SECONDS = 3

# The order a job moves through while it runs; used for the progress bar.
STEPS = ["queued", "downloading", "ingesting", "verifying"]
STEP_TEXT = {
    "queued": "Waiting for a worker",
    "downloading": "Downloading the reel",
    "ingesting": "Reading speech, on-screen text and caption",
    "verifying": "Checking each claim against evidence",
}
LABELS = {  # verdict label -> (badge text, badge colour)
    "supported": ("Supported", "green"),
    "refuted": ("Refuted", "red"),
    "misleading": ("Misleading", "orange"),
    "nei": ("Not enough evidence", "gray"),
}
RATINGS = {  # overall rating -> (headline, badge colour)
    "mostly_supported": ("Mostly supported", "green"),
    "mixed": ("Mixed", "orange"),
    "misleading": ("Misleading", "red"),
    "inconclusive": ("Inconclusive", "gray"),
}
SOURCES = {"speech": "Speech", "on_screen": "On-screen text", "caption": "Caption"}


class ApiError(Exception):
    pass


def api(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    try:
        resp = httpx.request(method, f"{API_URL}{path}", timeout=15, **kwargs)
    except httpx.HTTPError as exc:
        raise ApiError(f"Can't reach the ClaimLens API at {API_URL}. Is it running?") from exc
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail", resp.text)
        except ValueError:
            detail = resp.text
        if isinstance(detail, list):  # FastAPI validation errors
            detail = "; ".join(d.get("msg", str(d)) for d in detail)
        raise ApiError(str(detail))
    return resp.json()


def fmt_time(seconds: float | None) -> str | None:
    if seconds is None:
        return None
    return f"{int(seconds) // 60}:{int(seconds) % 60:02d}"


def show_progress(check: dict[str, Any]) -> None:
    status = check["status"]
    step = STEPS.index(status) if status in STEPS else 0
    st.progress((step + 1) / (len(STEPS) + 1), text=f"{STEP_TEXT.get(status, status)}…")
    st.caption("A reel usually takes a minute or two. This page updates by itself.")


def show_claim(result: dict[str, Any], pending: bool = False) -> None:
    claim, verdict, evidence = result["claim"], result["verdict"], result["evidence"]
    titles = {e["url"]: e["title"] or e["url"] for e in evidence}
    with st.container(border=True):
        st.markdown(f"**{claim['text']}**")
        meta = [SOURCES.get(claim["source"], claim["source"])]
        if (t := fmt_time(claim.get("timestamp_s"))) is not None:
            meta.append(f"at {t}")
        meta.append(claim.get("category", "general"))
        st.caption(" · ".join(meta))
        show_review_status(result, pending)

        if verdict is None:
            st.badge("No verdict", color="gray")
            st.caption("The check did not reach a verdict for this claim.")
        else:
            text, color = LABELS[verdict["label"]]
            st.badge(text, color=color)
            # An abstention with no stance-taking evidence was never scored by the judge.
            judged = verdict["label"] != "nei" or any(
                e.get("stance") in ("supports", "refutes") for e in evidence
            )
            if judged:
                st.progress(verdict["confidence"], text=f"Confidence {verdict['confidence']:.0%}")
            st.write(verdict["rationale"])
            if verdict["citations"]:
                st.markdown("**Sources**")
                st.markdown(
                    "\n".join(f"- [{titles.get(url, url)}]({url})" for url in verdict["citations"])
                )

        if evidence:
            with st.expander(f"All evidence found ({len(evidence)})"):
                for e in evidence:
                    stance = e.get("stance") or "unrated"
                    publisher = f" — {e['publisher']}" if e.get("publisher") else ""
                    st.markdown(f"[{e['title'] or e['url']}]({e['url']}){publisher} · *{stance}*")
                    if e.get("snippet"):
                        st.caption(e["snippet"])


def show_review_status(result: dict[str, Any], pending: bool) -> None:
    review, verdict = result.get("review"), result["verdict"]
    if review:
        text = "Reviewed by a person"
        if review["model_label"] != verdict["label"]:
            model = LABELS[review["model_label"]][0] if review["model_label"] else "no verdict"
            text += f": changed from {model}"
        if review["note"]:
            text += f". Note: {review['note']}"
        st.caption(text)
    elif pending and result.get("review_reasons"):
        st.caption(f"Waiting for review: {'; '.join(result['review_reasons'])}")


def show_results(check: dict[str, Any], provisional: bool = False) -> None:
    overall, results = check.get("overall"), check.get("results") or []
    if overall:
        headline, color = RATINGS[overall["rating"]]
        st.subheader("Overall (before review)" if provisional else "Overall")
        st.badge(headline, color=color)
        counts = overall["counts"]
        cols = st.columns(4)
        for col, key in zip(cols, ["supported", "refuted", "misleading", "nei"], strict=True):
            col.metric(LABELS[key][0], counts.get(key, 0))

    st.subheader(f"Claims ({len(results)})")
    if not results:
        st.info("No checkable factual claims were found in this reel.")
    for result in results:
        show_claim(result, pending=provisional)


def review_form(check: dict[str, Any]) -> None:
    """Let a reviewer set the final label of each flagged claim, then publish."""
    flagged = [r for r in check.get("results") or [] if r.get("review_reasons")]
    st.subheader("Review")
    st.write(
        "Pick the final label for each flagged claim. Open its sources above to check the "
        "evidence. Your labels replace the model's when the results are published."
    )
    with st.form("review"):
        decisions = []
        for result in flagged:
            claim, verdict = result["claim"], result["verdict"]
            options = list(LABELS)
            st.markdown(f"**{claim['text']}**")
            st.caption("; ".join(result["review_reasons"]))
            label = st.selectbox(
                "Final label",
                options,
                index=options.index(verdict["label"] if verdict else "nei"),
                format_func=lambda key: LABELS[key][0],
                key=f"label_{claim['id']}",
            )
            note = st.text_input(
                "Note (optional, shown with the result)", max_chars=1000, key=f"note_{claim['id']}"
            )
            decisions.append({"claim_id": claim["id"], "label": label, "note": note.strip()})
        token = st.text_input(
            "Reviewer token", type="password", help="Only if the API requires one."
        )
        publish = st.form_submit_button("Publish reviewed results", type="primary")
    if publish:
        headers = {"X-Review-Token": token} if token else {}
        try:
            api(
                "POST",
                f"/checks/{check['id']}/review",
                json={"decisions": decisions},
                headers=headers,
            )
        except ApiError as exc:
            st.error(str(exc))
        else:
            st.rerun()


def show_check(check: dict[str, Any]) -> None:
    st.markdown(f"Checking [{check['url']}]({check['url']})")
    status = check["status"]
    if status == "done":
        show_results(check)
    elif status == "failed":
        st.error(f"This check failed: {check.get('error') or 'unknown error'}")
        st.caption("Submit the same URL again to retry.")
    elif status == "needs_review":
        st.warning(
            "Some verdicts are uncertain or about a sensitive topic, so this check is waiting "
            "for a human reviewer before results are published."
        )
        show_results(check, provisional=True)
        review_form(check)
    else:
        show_progress(check)


@st.fragment(run_every=POLL_SECONDS)
def poll_check(job_id: str) -> None:
    """Re-fetch every few seconds until the job stops moving, then redraw the full page."""
    try:
        check = api("GET", f"/checks/{job_id}")
    except ApiError as exc:
        st.error(str(exc))
        return
    if check["status"] not in STEPS:
        st.rerun(scope="app")
    show_check(check)


def main() -> None:
    st.set_page_config(page_title="ClaimLens", layout="centered")
    st.title("ClaimLens")
    st.write(
        "Paste a link to a short video. ClaimLens pulls out its factual claims and checks "
        "each one against published evidence."
    )

    with st.form("submit"):
        url = st.text_input("Reel URL", placeholder="https://www.youtube.com/shorts/...")
        submitted = st.form_submit_button("Check this reel", type="primary")
    if submitted:
        if not url.strip():
            st.warning("Paste a URL first.")
        else:
            try:
                check = api("POST", "/checks", json={"url": url.strip()})
            except ApiError as exc:
                st.error(str(exc))
            else:
                st.query_params["job"] = check["id"]  # keeps the result on refresh and in links

    job_id = st.query_params.get("job")
    if job_id:
        st.divider()
        try:
            check = api("GET", f"/checks/{job_id}")
        except ApiError as exc:
            st.error(str(exc))
        else:
            if check["status"] in STEPS:
                poll_check(job_id)
            else:
                show_check(check)

    st.divider()
    st.caption(
        "Verdicts are produced automatically and can be wrong. They describe the claim, not "
        "the person who made it. Open the sources to judge the evidence yourself."
    )


main()
