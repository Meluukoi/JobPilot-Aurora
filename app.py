

import json

from pathlib import Path

import pandas as pd

import streamlit as st



from collectors import fetch_greenhouse, fetch_lever, batch_fetch, parse_source_url

from db import init_db, upsert_job, list_jobs, update_status, update_notes

from scorer import score_job

from application_tracking import SavedJobIndex, visible_for_status, tracked_sort_key, already_applied

from discovery_history import job_fingerprint, record_scan, new_fingerprints

from application_dashboard import render_application_dashboard



ROOT = Path(__file__).parent

PROFILE_PATH = ROOT / "profile.json"

SOURCES_PATH = ROOT / "sources.csv"

CANDIDATE_PATH = ROOT / "candidate_profile.json"



st.set_page_config(page_title="JobPilot v1.0", page_icon="ðŸ§­", layout="wide")

st.session_state.setdefault("jp_demo_mode", True)  # Safe default for public portfolio
if not st.session_state.get("jp_demo_mode", False):
    init_db()



def load_profile():

    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))



def save_profile(profile):

    PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")



def load_sources():

    if not SOURCES_PATH.exists():

        return pd.DataFrame(columns=["enabled","ats","company","token_or_site"])

    df = pd.read_csv(SOURCES_PATH)

    if "enabled" not in df.columns:

        df["enabled"] = True

    return df



def save_sources(df):

    df.to_csv(SOURCES_PATH, index=False)



def score_all(jobs, matching_profile):

    out = []

    for job in jobs:

        result = score_job(job, matching_profile)

        j = dict(job)

        j.update({

            "score": result["score"],

            "recommendation": result["recommendation"],

            "_reasons": result["reasons"],

            "_gaps": result["gaps"],

            "_hard_flags": result["hard_flags"],

            "_family": result["matched_family"] or "Other",

            "_geo_status": result.get("geo_status", "review"),

            "_geo_label": result.get("geo_label", "Verify job location"),

            "_geo_reason": result.get("geo_reason", ""),

        })

        out.append(j)

    return sorted(out, key=lambda x: x["score"], reverse=True)



profile = {} if st.session_state.get("jp_demo_mode", False) else load_profile()



# Keep editable matching settings separate from candidate evidence.

# The approved candidate file is local and is NOT written into profile.json.

def load_candidate_profile():

    if not CANDIDATE_PATH.exists():

        return None

    try:

        record = json.loads(CANDIDATE_PATH.read_text(encoding="utf-8"))

        if not str(record.get("status", "")).startswith("approved_by_user"):

            st.warning("Candidate profile is not approved; using the original keyword matcher.")

            return None

        return record

    except (OSError, ValueError) as exc:

        st.error(f"Could not load candidate_profile.json: {exc}")

        return None



candidate_profile = None if st.session_state.get("jp_demo_mode", False) else load_candidate_profile()

matching_profile = dict(profile)

if candidate_profile:

    matching_profile["candidate_profile"] = candidate_profile

    # The user's confirmed nationality is relevant only for eligibility checks.

    matching_profile["citizenship_status"] = "us_citizen" if candidate_profile.get("preferences", {}).get("citizenship") else "unknown"



# AURORA UI V1 — Presentation only, existing rules and handlers preserved.
st.markdown("""
<style>
:root {--jp-bg:#0c1223;--jp-panel:#17223a;--jp-line:#32415b;--jp-ink:#eef2ff;--jp-muted:#aab7d0;--jp-violet:#9489ff;--jp-mint:#74e6c6;}
[data-testid="stAppViewContainer"] {background:linear-gradient(145deg,#0c1223 0%,#101a2c 100%);}
[data-testid="stSidebar"] {background:#10192e;border-right:1px solid #2b3b57;}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {color:#dce5fa;}
[data-testid="stSidebar"] [role="radiogroup"] label {border-radius:10px;padding:5px 8px;}
[data-testid="stSidebar"] [role="radiogroup"] label:hover {background:#263450;}
[data-testid="stHeader"] {background:transparent;}
[data-testid="stAppViewBlockContainer"] {max-width:1370px;padding-top:2rem;}
h1,h2,h3,h4 {letter-spacing:-.035em;}
[data-testid="stMetricValue"] {color:#74e6c6;}
div[data-testid="stVerticalBlockBorderWrapper"] > div {border-color:#32415b;border-radius:14px;}
div.stButton > button[kind="primary"] {background:#8178f6;border-color:#8178f6;color:#fff;border-radius:9px;}
div.stButton > button[kind="secondary"], div.stLinkButton > a {border-radius:9px;}
.jp-brand {display:flex;align-items:center;gap:11px;margin-bottom:24px;color:#f1f4ff;font-weight:770;font-size:24px;letter-spacing:-.055em;}
.jp-brandmark {display:grid;place-items:center;background:linear-gradient(135deg,#8278f8,#36c9af);height:38px;width:38px;border-radius:12px;color:white;font-size:25px;}
.jp-hero {padding:6px 0 8px;}
.jp-hero h1 {font-size:clamp(26px,3vw,38px);margin:0;line-height:1.1;color:#f5f6ff;}
.jp-hero p {font-size:14px;color:#aab7d0;margin:9px 0 18px;}
.jp-hero .jp-dot {color:#9489ff;}
.jp-overline {font-size:11px;letter-spacing:.14em;font-weight:750;color:#8f9fbe;margin-bottom:8px;}
</style>
""", unsafe_allow_html=True)
with st.sidebar:
    st.markdown('<div class="jp-brand"><span class="jp-brandmark">↗</span>JobPilot <small style="font-size:10px;color:#aab7d0;align-self:start">v1</small></div>', unsafe_allow_html=True)
    st.caption("WORKSPACE")
    aurora_page = st.radio(
        "Navigation", ["Discover", "Saved Jobs", "Applications", "Settings"],
        label_visibility="collapsed", key="aurora_page"
    )
    if aurora_page == "Discover":
        aurora_tool = st.radio("Discover tools", ["Find jobs", "Single source"], key="aurora_discover_tool")
    elif aurora_page == "Saved Jobs":
        aurora_tool = st.radio("Saved tools", ["Saved jobs", "Add job"], key="aurora_saved_tool")
    elif aurora_page == "Settings":
        aurora_tool = st.radio("Settings tools", ["Sources", "Profile"], key="aurora_settings_tool")
    else:
        aurora_tool = "Application overview"
    st.divider()
    st.toggle("Demo mode (fictional jobs)", key="jp_demo_mode", help="A separate, session-only demo. No scans, database updates, or network calls.")
    st.caption("Local portfolio workspace · Fictional demo available")

st.markdown('<div class="jp-overline">YOUR WORKSPACE</div>', unsafe_allow_html=True)
st.markdown('<div class="jp-hero"><h1>Find your next move<span class="jp-dot">.</span></h1><p>Job discovery, application tracking, and matching evidence in one place.</p></div>', unsafe_allow_html=True)

# JP DEMO MODE V1 — wholly separate from live collectors, database and configuration.
if st.session_state.get("jp_demo_mode", False):
    from portfolio_demo import render_demo
    render_demo(st, aurora_page)

if not st.session_state.get("jp_demo_mode", False) and aurora_page == "Discover" and aurora_tool == "Find jobs":

    st.subheader("Discover jobs")

    st.caption("Search, evaluate, and save opportunities from your configured sources.")

    src_df = load_sources()

    enabled = src_df[src_df["enabled"].astype(str).str.lower().isin(["true","1","yes"])] if not src_df.empty else src_df



    title_filter = st.text_input(

        "Search job titles", placeholder="Role or keyword (for example, support or analyst)"

    )

    with st.expander("Advanced discovery filters", expanded=False):

        c1, c2, c3 = st.columns(3)

        with c1:

            max_age = st.number_input("Greenhouse updated within (days)", 1, 60, 14)

        with c2:

            min_score = st.slider("Minimum score", 0, 100, 45)

        with c3:

            location_filter = st.text_input("Location contains", placeholder="Remote, city, or region")



        eligibility_filter = st.selectbox(

            "Geographic eligibility",

            ["Eligible + needs review (exclude blocked)",

             "Local + eligible regional remote only",

             "Eligible only (local, regional remote, relocation)",

             "Needs verification only", "Blocked only", "All listings"],

            index=0,

            help="Location is checked separately from skill score. Remote eligibility depends on the configured candidate region and job restrictions."

        )



        verify_dates = st.checkbox("Verify original posting dates (slower)", value=False,

                                   help="Off: fast scan uses last-updated dates for Greenhouse. On: extra requests check first-published date when available.")

        if not verify_dates:

            st.caption("Fast mode: Greenhouse results were updated in the selected period, but may have been posted earlier. Lever publication dates are unknown.")

    if st.button("Find jobs", type="primary"):

        if enabled.empty:

            st.warning("Enable at least one company under Settings > Sources first.")

        else:

            sources = enabled[["ats", "company", "token_or_site"]].to_dict("records")

            total = len(sources)

            bar = st.progress(0, text=f"Scanning 0 of {total} company sources")

            update = st.empty()

            def update_progress(completed, total, company, count, error):

                bar.progress(completed / total,

                             text=f"Scanned {completed} of {total} sources")

                if error:

                    update.warning(f"{company}: source error (other companies continue)")

                else:

                    update.caption(f"Completed {company}: {count} listings")

            try:

                jobs, errors = batch_fetch(

                    sources, int(max_age), progress_callback=update_progress,

                    verify_first_published=verify_dates

                )

                update.info(f"Collection finished: {len(jobs)} unique listings from {total} sources; {len(errors)} source errors. Scoring results...")

                scored = score_all(jobs, matching_profile)

                st.session_state["batch_jobs"] = scored

                st.session_state["batch_errors"] = errors

                # Stage 5A: record completed scans in a separate local database.

                # Do not touch jobpilot.db, sources.csv, or approved profiles.

                st.session_state["discovery_run"] = None

                if scored:

                    try:

                        st.session_state["discovery_run"] = record_scan(scored, errors)

                    except Exception as history_exc:

                        # Scanning and saving applications remain usable if

                        # discovery-history storage is unavailable.

                        st.warning(f"Discovery history not updated: {history_exc}")

                bar.empty()

                update.success(f"Scan complete: {len(scored)} jobs; {len(errors)} source errors")

            except Exception as exc:

                bar.empty()

                update.error(f"Scan interrupted: {exc}")



    # Re-evaluate the existing in-memory results after changing profile.json,

    # without downloading the same company feeds again. Saved jobs are untouched.

    if st.session_state.get("batch_jobs") and st.button("Re-score collected jobs (no new scan)"):

        previous = st.session_state["batch_jobs"]

        st.session_state["batch_jobs"] = score_all(previous, matching_profile)

        st.success(f"Re-scored {len(previous)} collected jobs without network requests.")



    jobs = st.session_state.get("batch_jobs", [])

    if not jobs:

        st.info("No jobs displayed yet. Configure an employer source in Settings, then select Find jobs to collect opportunities.")

    if jobs:

        history_run = st.session_state.get("discovery_run")

        latest_new_fingerprints = set()

        if history_run:

            try:

                latest_new_fingerprints = new_fingerprints(history_run["scan_id"])

            except Exception as history_exc:

                st.warning(f"Could not read discovery history: {history_exc}")

            if history_run["baseline"]:

                st.info(f"Discovery baseline created: {history_run['recorded_count']} unique jobs recorded. "

                        "This initial scan cannot establish which jobs were newly posted. "

                        "Later scans will highlight postings first seen by JobPilot.")

            else:

                st.info(f"Discovery history: {history_run['new_count']} first seen by JobPilot in this scan; "

                        f"{history_run['known_count']} already seen in prior scans. "

                        "First seen does not mean newly posted by the employer.")

            if history_run["source_error_count"]:

                st.caption("Some sources failed; missing listings are not assumed closed.")

            if history_run["skipped_count"]:

                st.caption(f"{history_run['skipped_count']} listings lacked stable IDs/URLs "

                           "and were not added to history.")

        # Read saved status once per rerun; never modify saved jobs during a scan.

        saved_index = SavedJobIndex(list_jobs())

        status_filter = st.selectbox(

            "Application tracking filter",

            ["Hide already applied", "All listings", "Untracked only",

             "Tracked only", "Applied / advanced only"],

            help="Already applied includes Interview, Rejected, Offer and Archived. "

                 "Untracked means not saved, not necessarily recently posted."

        )

        discovery_filter = st.selectbox(

            "Discovery history filter",

            ["All collected listings", "First seen by JobPilot in this scan"],

            help="Only compares completed Batch Scans stored on this computer. "

                 "First seen is not the employer's publication date."

        )

        prioritize_untracked = st.checkbox(

            "Prioritize untracked openings within each location group", value=True,

            help="Does not change match scores or job eligibility."

        )

        sort_order = st.selectbox(

            "Sort results",

            ["Local + eligible regional remote first", "Highest match score first"],

            help="Sorting never changes scores or eligibility."

        )

        filtered = []

        for j in jobs:

            if (discovery_filter == "First seen by JobPilot in this scan"

                    and job_fingerprint(j) not in latest_new_fingerprints):

                continue

            if j["score"] < min_score:

                continue

            g = j.get("_geo_status", "review")

            if eligibility_filter.startswith("Eligible +") and g == "blocked":

                continue

            if eligibility_filter == "Local + eligible regional remote only" and g not in ("local", "remote_ca"):

                continue

            if eligibility_filter.startswith("Eligible only") and g not in ("local", "remote_ca", "relocation"):

                continue

            if eligibility_filter == "Needs verification only" and g != "review":

                continue

            if eligibility_filter == "Blocked only" and g != "blocked":

                continue

            if location_filter and location_filter.lower() not in (j.get("location") or "").lower():

                continue

            if title_filter and title_filter.lower() not in (j.get("title") or "").lower():

                continue

            saved, possible = saved_index.lookup(j)

            if not visible_for_status(saved, status_filter):

                continue

            # Runtime-only status: don't change the fetched job or application DB.

            enriched = dict(j)

            enriched["_saved_job"] = saved

            enriched["_possible_duplicates"] = possible

            filtered.append(enriched)



        if sort_order == "Local + eligible regional remote first":

            rank = {"local": 0, "remote_ca": 1, "relocation": 2, "review": 3, "blocked": 4}

            filtered.sort(key=lambda j: (

                rank.get(j.get("_geo_status"), 5),

                tracked_sort_key(j["_saved_job"]) if prioritize_untracked else 0,

                -j["score"]

            ))

        else:

            filtered.sort(key=lambda j: (

                -j["score"], tracked_sort_key(j["_saved_job"]) if prioritize_untracked else 0

            ))



        totals = {k: sum(j.get("_geo_status") == k for j in jobs)

                  for k in ("local", "remote_ca", "relocation", "review", "blocked")}

        saved_in_scan = sum(saved_index.lookup(j)[0] is not None for j in jobs)

        already_applied_in_scan = sum(

            bool(saved and already_applied(saved.get("status")))

            for j in jobs for saved in [saved_index.lookup(j)[0]]

        )

        st.write(f"Showing **{min(len(filtered), 250)}** of **{len(jobs)}** fetched jobs"

                 + (f" ({len(filtered)} match the current filters; first 250 rendered)." if len(filtered) > 250 else "."))

        st.caption(f"Application tracking in this scan: {saved_in_scan} already saved; "

                   f"{already_applied_in_scan} already applied or further along. "

                   "An untracked job is not necessarily a newly posted job.")

        st.caption("Geography across all results: " + ", ".join(

            f"{label} {totals[key]}" for key, label in [

                ("local", "Local area"), ("remote_ca", "Eligible regional remote"),

                ("relocation", "Relocation eligible (configured threshold)"),

                ("review", "Verify eligibility"), ("blocked", "Blocked")]))

        # Diagnose the difference between geographical eligibility and career fit.

        local_ca = [j for j in jobs if j.get("_geo_status") in ("local", "remote_ca")]

        local_targeted = sum(j.get("_family") not in ("Other", None) for j in local_ca)

        st.caption(

            f"Local + eligible regional remote listings: {len(local_ca)}; recognized target roles: "

            f"{local_targeted}; at or above current score {min_score}: "

            f"{sum(j['score'] >= min_score for j in local_ca)}. "

            "A geographical match is not necessarily a qualification match."

        )

        with st.expander("Why are local and eligible remote jobs not appearing?"):

            rows = [{"company": j.get("company", ""),

                     "title": j.get("title", ""),

                     "location": j.get("location", ""),

                     "fit_score": j["score"],

                     "role_family": j.get("_family", "Other"),

                     "eligibility": j.get("_geo_status", "review"),

                     "recommendation": j.get("recommendation", ""),

                     "issues": " | ".join(j.get("_gaps", []))[:250],

                     "url": j.get("url", "")}

                    for j in local_ca]

            if rows:

                table = pd.DataFrame(rows).sort_values(["fit_score"], ascending=False)

                st.dataframe(table, use_container_width=True, hide_index=True)

                st.download_button(

                    "Download local/remote diagnostics CSV",

                    data=table.to_csv(index=False).encode("utf-8-sig"),

                    file_name="jobpilot_local_remote_diagnostics.csv",

                    mime="text/csv"

                )

            else:

                st.info("No local or regionally eligible remote listings in this scan.")



        errors = st.session_state.get("batch_errors", [])

        if errors:

            with st.expander(f"{len(errors)} source errors"):

                for e in errors:

                    st.write(e)



        # AURORA UI V1 — Split-view results; no network requests from selection.
        display_jobs = filtered[:250]
        if not display_jobs:
            st.info("No jobs match these filters. Try another score, location, or title.")
        else:
            list_col, detail_col = st.columns([0.86, 1.5], gap="large")
            choice_ids = [f"{j['source']}::{j['source_id']}" for j in display_jobs]
            if st.session_state.get("aurora_job_choice") not in choice_ids:
                st.session_state["aurora_job_choice"] = choice_ids[0]

            with list_col:
                st.caption(f"{len(display_jobs)} RESULTS · SELECT A JOB")
                with st.container(height=620, border=False):
                    for index, item in enumerate(display_jobs):
                        item_id = choice_ids[index]
                        chosen = item_id == st.session_state["aurora_job_choice"]
                        label = ("● " if chosen else "") + str(item.get("title") or "Untitled job")
                        if st.button(label, key=f"aurora_pick_{index}", use_container_width=True):
                            st.session_state["aurora_job_choice"] = item_id
                            st.rerun()
                        st.caption(f"{item.get('company') or 'Employer unknown'} · "
                                   f"{item.get('location') or 'Location unspecified'} · "
                                   f"{item.get('score', '?')}% match")

            selected_job = display_jobs[choice_ids.index(st.session_state["aurora_job_choice"])]
            job = selected_job  # keep the existing save and duplicate-protection contract
            saved_record = job.get("_saved_job")
            possible_duplicates = job.get("_possible_duplicates", ())
            geo_status = job.get("_geo_status", "review")
            geo_label = job.get("_geo_label", "Verify location")
            geo_reason = job.get("_geo_reason", "")
            key = f"{job['source']}_{job['source_id']}"

            with detail_col:
                with st.container(border=True):
                    top, fit = st.columns([4, 1], vertical_alignment="top")
                    with top:
                        st.markdown(f"### {job.get('title') or 'Untitled job'}")
                        st.write(f"**{job.get('company') or 'Unknown employer'}**")
                        st.caption(f"{job.get('location') or 'Location unspecified'} · "
                                   f"{job.get('source') or 'Source unknown'}")
                    with fit:
                        st.metric("Fit", f"{job.get('score', 0)}%")
                    st.caption(f"Posting date evidence: {job.get('posted_at') or 'Not verified'}")
                    if job.get('salary'):
                        st.caption(f"Reported compensation: {job['salary']}")
                    if job_fingerprint(job) in latest_new_fingerprints:
                        st.caption("First seen in this JobPilot scan (not necessarily newly posted)")
                    if saved_record:
                        st.info(f"Already tracked · {saved_record.get('status') or 'Interested'}")
                    elif possible_duplicates:
                        st.warning("Potential duplicate saved opening — compare official URLs before saving")
                    if geo_status == "blocked":
                        st.error(f"Location: {geo_label}")
                    elif geo_status == "review":
                        st.warning(f"Location needs verification: {geo_label}")
                    else:
                        st.success(f"Location: {geo_label}")
                    st.write(str(job.get('recommendation') or ''))

                    st.markdown("#### About the opportunity")
                    description = str(job.get('description') or '').strip()
                    if description:
                        st.write(description[:5000])
                    else:
                        st.caption("A description was not provided by this source.")

                    with st.expander("Match evidence and eligibility details", expanded=False):
                        if geo_reason:
                            st.write("**Geographic assessment:**", geo_reason)
                        if job.get('_hard_flags'):
                            st.error("Eligibility warnings: " + " | ".join(job['_hard_flags']))
                        if job.get('_gaps'):
                            st.warning("Requirements to verify: " + " | ".join(job['_gaps']))
                        if job.get('_reasons'):
                            st.success("Matching evidence: " + " | ".join(job['_reasons']))
                        if possible_duplicates:
                            refs = ", ".join(str(x.get('id')) for x in possible_duplicates[:3])
                            st.warning(f"Potential duplicate saved IDs: {refs}. Verify official URL and location.")
                        st.caption("Rule-based relevance score, not a hiring probability.")

                    actions, tracking = st.columns([1.2, 1], vertical_alignment="center")
                    with actions:
                        if job.get('url'):
                            st.link_button("Original posting", job['url'], key=f"aurora_official_{key}")
                        else:
                            st.caption("Original posting URL unavailable")
                    with tracking:
                        if saved_record:
                            st.caption("Saved · Manage status in Saved Jobs")
                        else:
                            allow_distinct = True
                            if possible_duplicates:
                                allow_distinct = st.checkbox(
                                    "Separate opening confirmed", value=False,
                                    key=f"aurora_distinct_{key}",
                                    help="Compare official posting IDs and URLs before confirming."
                                )
                            if st.button("Save job", key=f"aurora_save_{key}", disabled=not allow_distinct):
                                current_saved, current_possible = SavedJobIndex(list_jobs()).lookup(job)
                                if current_saved:
                                    st.info("Already saved. No duplicate was created.")
                                elif current_possible and not allow_distinct:
                                    st.warning("Review possible duplicate before saving.")
                                else:
                                    upsert_job(job)
                                    st.success("Saved with status Interested.")
                                    st.rerun()

if not st.session_state.get("jp_demo_mode", False) and aurora_page == "Discover" and aurora_tool == "Single source":

    st.subheader("Test one ATS source")

    c1,c2,c3 = st.columns([1,2,1])

    with c1:

        ats = st.selectbox("ATS", ["Greenhouse","Lever"], key="singleats")

    with c2:

        token = st.text_input("Board token/site")

    with c3:

        age = st.number_input("GH max age",1,60,14,key="singleage")



    if st.button("Fetch source"):

        try:

            jobs = fetch_greenhouse(token, None, int(age)) if ats=="Greenhouse" else fetch_lever(token)

            st.session_state["single_jobs"] = score_all(jobs, matching_profile)

            st.success(f"Fetched {len(jobs)} jobs")

        except Exception as e:

            st.error(str(e))



    single_jobs = st.session_state.get("single_jobs", [])

    single_saved_index = SavedJobIndex(list_jobs()) if single_jobs else None

    for job in single_jobs[:100]:

        with st.container(border=True):

            st.markdown(f"### {job['title']} â€” {job['score']}%")

            st.write(f"{job['company']} Â· {job['location']} Â· {job['recommendation']}")

            matched_saved, _ = single_saved_index.lookup(job)

            if matched_saved:

                st.info(f"Already tracked: {matched_saved.get('status') or 'Interested'}")

            st.caption(f"Geography: {job.get('_geo_label', 'Needs verification')} â€” {job.get('_geo_reason','')}")

            if job.get("_gaps"): st.write("Gaps:", job["_gaps"])

            if job.get("_reasons"): st.write("Matches:", job["_reasons"])

            if job.get("url"): st.link_button("Open official job", job["url"])



if not st.session_state.get("jp_demo_mode", False) and aurora_page == "Applications":
    st.subheader("Application pipeline")
    st.caption("Track each opportunity from initial interest to an outcome.")
    pipeline = list_jobs()
    stages = ["Interested", "Applied", "Interview", "Offer", "Rejected", "Archived"]
    if not pipeline:
        st.info("No saved applications yet. Save a job from Discover to start your pipeline.")
    else:
        for offset in (0, 3):
            lane_cols = st.columns(3, gap="small")
            for col, stage in zip(lane_cols, stages[offset:offset + 3]):
                items = [j for j in pipeline if (j.get("status") or "Interested") == stage]
                with col:
                    with st.container(border=True):
                        st.markdown(f"**{stage}** · {len(items)}")
                        for item in items[:15]:
                            st.markdown(f"**{item.get('title') or 'Untitled'}**")
                            st.caption(str(item.get("company") or "Unknown employer"))
                            st.divider()
                        if len(items) > 15:
                            st.caption(f"+ {len(items)-15} more in this stage")
        st.markdown("#### Update an application")
        options = {f"{j['id']} · {j.get('company')} · {j.get('title')}": j for j in pipeline}
        selected_label = st.selectbox("Application", list(options), key="aurora_pipeline_job")
        selected = options[selected_label]
        current = selected.get("status") or "Interested"
        new_status = st.selectbox("Move to", stages,
                                  index=stages.index(current) if current in stages else 0,
                                  key=f"aurora_move_{selected['id']}")
        if st.button("Update application stage", key="aurora_move_action"):
            update_status(selected["id"], new_status)
            st.success("Application stage updated.")
            st.rerun()
    with st.expander("Application analytics", expanded=False):
        render_application_dashboard()

if not st.session_state.get("jp_demo_mode", False) and aurora_page == "Saved Jobs" and aurora_tool == "Saved jobs":

    st.subheader("Application tracker")

    notice = st.session_state.pop("tracker_notice", None)

    if notice:

        st.success(notice)

    jobs = list_jobs()

    if not jobs:

        st.write("No saved jobs yet.")

    else:

        df = pd.DataFrame(jobs)

        st.dataframe(df[["id","company","title","location","score","recommendation","status","posted_at"]], use_container_width=True, hide_index=True)

        labels = {f"{j['id']} â€” {j['company']} â€” {j['title']}": j for j in jobs}

        label = st.selectbox("Select job", list(labels))

        selected = labels[label]

        status_choices = ["Interested","Applied","Interview","Rejected","Offer","Archived"]

        existing_status = selected.get("status") or "Interested"

        status = st.selectbox(

            "Status", status_choices,

            index=status_choices.index(existing_status) if existing_status in status_choices else 0,

            key=f"saved_status_{selected['id']}"

        )

        notes = st.text_area(

            "Notes", value=selected.get("notes") or "", key=f"saved_notes_{selected['id']}"

        )

        c1,c2 = st.columns(2)

        with c1:

            if st.button("Update status"):

                update_status(selected["id"], status)

                st.session_state["tracker_notice"] = f"Updated {selected['company']} to {status}."

                st.rerun()

        with c2:

            if st.button("Save notes"):

                update_notes(selected["id"], notes)

                st.session_state["tracker_notice"] = "Notes saved."

                st.rerun()



if not st.session_state.get("jp_demo_mode", False) and aurora_page == "Saved Jobs" and aurora_tool == "Add job":

    st.subheader("Analyze any job manually")

    company = st.text_input("Company", key="manualcompany")

    title = st.text_input("Job title", key="manualtitle")

    location = st.text_input("Location", key="manuallocation")

    url = st.text_input("Official job URL", key="manualurl")

    description = st.text_area("Paste job description", height=300)



    manual_job = {

        "source": "Manual", "source_id": url or f"{company}-{title}",

        "company": company, "title": title, "location": location, "url": url,

        "description": description, "posted_at": "Unknown"

    }

    manual_saved, manual_possible = SavedJobIndex(list_jobs()).lookup(manual_job)

    if manual_saved:

        st.info(f"Already saved as ID {manual_saved.get('id')} "

                f"with status {manual_saved.get('status') or 'Interested'}. "

                "Use Saved Jobs to update it.")

    elif manual_possible:

        st.warning("Possible duplicate: same company and title as an existing saved job. "

                   "Compare official URLs and locations before saving separately.")

    manual_distinct = (

        st.checkbox("This is a different opening (I checked its official URL/ID)", value=False)

        if manual_possible and not manual_saved else False

    )

    if st.button("Analyze and save", key="manualsave"):

        if not url.strip():

            st.error("Enter an official job URL before saving so the tracker can prevent duplicates.")

        elif manual_saved:

            st.warning("Already saved; no duplicate created. See Saved Jobs.")

        elif manual_possible and not manual_distinct:

            st.warning("Please compare the possible duplicate and confirm it's a separate opening.")

        else:

            # Verify again when the button is clicked; never overwrite Applied.

            existing, possible = SavedJobIndex(list_jobs()).lookup(manual_job)

            if existing:

                st.info("Already tracked. No duplicate was created.")

            elif possible and not manual_distinct:

                st.warning("Possible duplicate; confirmation is required.")

            else:

                result = score_job(manual_job, matching_profile)

                manual_job["score"] = result["score"]

                manual_job["recommendation"] = result["recommendation"]

                upsert_job(manual_job)

                st.success(f"Saved. {result['score']}% â€” {result['recommendation']}")

                st.info(f"Geography: {result.get('geo_label')} â€” {result.get('geo_reason')}")

                if result["hard_flags"]: st.error("Hard flags: " + " | ".join(result["hard_flags"]))

                if result["gaps"]: st.warning("Gaps: " + " | ".join(result["gaps"]))

                if result["reasons"]: st.success("Matches: " + " | ".join(result["reasons"]))



if not st.session_state.get("jp_demo_mode", False) and aurora_page == "Settings" and aurora_tool == "Sources":

    st.subheader("Company source catalog")

    st.write("Add public Greenhouse or Lever company boards here. Batch Scan checks every enabled row.")



    src_df = load_sources()

    edited = st.data_editor(

        src_df,

        num_rows="dynamic",

        use_container_width=True,

        column_config={

            "enabled": st.column_config.CheckboxColumn("Enabled"),

            "ats": st.column_config.SelectboxColumn("ATS", options=["Greenhouse","Lever"]),

        }

    )

    if st.button("Save source catalog"):

        save_sources(edited)

        st.success("Sources saved")



    st.markdown("#### Add from a public careers URL")

    careers_url = st.text_input("Greenhouse/Lever URL")

    company_name = st.text_input("Company display name")

    if st.button("Parse and add"):

        ats2, token2 = parse_source_url(careers_url)

        if not ats2:

            st.error("Could not recognize a Greenhouse/Lever board URL.")

        else:

            cur = load_sources()

            new = pd.DataFrame([{

                "enabled": True, "ats": ats2,

                "company": company_name or token2,

                "token_or_site": token2

            }])

            cur = pd.concat([cur, new], ignore_index=True)

            cur = cur.drop_duplicates(subset=["ats","token_or_site"], keep="last")

            save_sources(cur)

            st.success(f"Added {ats2}: {token2}")



if not st.session_state.get("jp_demo_mode", False) and aurora_page == "Settings" and aurora_tool == "Profile":

    st.subheader("Matching profile")

    if candidate_profile:

        st.info("A local example candidate profile is loaded. Candidate evidence is used for matching and is not shared by this dashboard.")

    else:

        st.info("No approved candidate evidence is loaded; keyword matching remains available.")

    st.caption("Preferences are configurable. The public sample data is fictional; use your own settings only in a private copy.")

    with st.expander("Advanced matching settings (JSON)", expanded=False):

        raw = st.text_area("Profile JSON", value=json.dumps(profile, indent=2), height=520)

        if st.button("Save profile"):

            try:

                save_profile(json.loads(raw))

                st.success("Profile saved")

            except json.JSONDecodeError as e:

                st.error(f"Invalid JSON: {e}")
