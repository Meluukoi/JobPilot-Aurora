"""Synthetic, session-only UI walkthrough for the public JobPilot portfolio.

No imports from collectors, application storage, email or discovery history.
No filesystem or network operations occur in this module.
"""

DEMO_JOBS = (
    {
        "id": "DEMO-001", "title": "IT Support Specialist",
        "company": "Example Technologies", "location": "Remote — Example Region",
        "work_type": "Remote", "salary": "$55,000–$70,000",
        "score": 91, "posted": "Sample: 2 days ago", "geo": "Eligible (fictional)",
        "description": "Support employees, troubleshoot Windows devices, and document resolutions in a service desk platform.",
        "matches": ("Desktop support and troubleshooting", "Ticketing workflows", "Region eligible in sample configuration"),
        "verify": ("Confirm original publication date", "Check employer's actual remote-location requirements"),
    },
    {
        "id": "DEMO-002", "title": "Technical Support Associate",
        "company": "Northstar Systems", "location": "Hybrid — Example City",
        "work_type": "Hybrid", "salary": "Not provided",
        "score": 86, "posted": "Sample: 4 days ago", "geo": "Needs review (fictional)",
        "description": "Investigate software issues, work with customers, and track incidents until resolution.",
        "matches": ("Customer support", "Software troubleshooting", "Documentation"),
        "verify": ("Verify hybrid office location", "Compensation unavailable"),
    },
    {
        "id": "DEMO-003", "title": "Junior Security Analyst",
        "company": "Summit Digital", "location": "On-site — Example District",
        "work_type": "On-site", "salary": "$62,000–$78,000",
        "score": 79, "posted": "Sample: 6 days ago", "geo": "Needs review (fictional)",
        "description": "Assist analysts with alert triage, incident tickets, and security event documentation.",
        "matches": ("Foundational security knowledge", "Incident ticket documentation"),
        "verify": ("Verify work location", "Review technical experience requirements"),
    },
)
STAGES = ("Interested", "Applied", "Interview", "Offer", "Rejected", "Archived")


def search_demo_jobs(keyword="", arrangement="All"):
    """Deterministic search over fictional jobs; no I/O."""
    q = str(keyword).strip().casefold()
    return [job for job in DEMO_JOBS
            if (arrangement == "All" or job["work_type"] == arrangement)
            and q in (job["title"] + " " + job["company"] + " " + job["description"]).casefold()]


def set_demo_status(stages, job_id, status):
    if job_id not in {j["id"] for j in DEMO_JOBS}:
        raise ValueError("Unknown demo job")
    if status not in STAGES:
        raise ValueError("Invalid stage")
    updated = dict(stages)
    updated[job_id] = status
    return updated


def group_demo_applications(stages):
    groups = {stage: [] for stage in STAGES}
    for job in DEMO_JOBS:
        groups[stages.get(job["id"], "Interested")].append(job)
    return groups


def render_demo(st, page):
    """Render fictional Streamlit UX. Mutates only st.session_state in this browser session."""
    st.session_state.setdefault("jp_demo_saved", set())
    st.session_state.setdefault("jp_demo_stages", {
        "DEMO-001": "Interested", "DEMO-002": "Applied", "DEMO-003": "Interview"})
    st.info("DEMO MODE · Fictional listings and sample scores. No live jobs, database writes, emails, or applications.")

    if page == "Discover":
        st.subheader("Discover jobs")
        a, b = st.columns([3, 1])
        with a:
            keyword = st.text_input("Search", key="jp_demo_search", placeholder="Role, company, or keyword")
        with b:
            arrangement = st.selectbox("Work arrangement", ["All", "Remote", "Hybrid", "On-site"], key="jp_demo_arrangement")
        results = search_demo_jobs(keyword, arrangement)
        st.caption(f"{len(results)} fictional opportunities · Match scores are illustrative, not hiring probabilities")
        if not results:
            st.info("No sample jobs match these filters. Try a different keyword or work arrangement.")
            return
        ids = [job["id"] for job in results]
        if st.session_state.get("jp_demo_selected") not in ids:
            st.session_state["jp_demo_selected"] = ids[0]
        list_col, detail_col = st.columns([0.95, 1.55], gap="large")
        with list_col:
            for job in results:
                selected = st.session_state["jp_demo_selected"] == job["id"]
                with st.container(border=True):
                    st.markdown(f"**{job['title']}**")
                    st.caption(f"{job['company']} · {job['work_type']} · {job['score']}% sample fit")
                    if st.button("Selected" if selected else "View details", key="jp_demo_select_"+job["id"],
                                 disabled=selected, use_container_width=True):
                        st.session_state["jp_demo_selected"] = job["id"]
                        st.rerun()
        job = next(j for j in results if j["id"] == st.session_state["jp_demo_selected"])
        with detail_col:
            with st.container(border=True):
                x, y = st.columns([4, 1])
                with x:
                    st.markdown(f"### {job['title']}")
                    st.write(job["company"])
                    st.caption(job["location"])
                with y:
                    st.metric("Sample fit", str(job["score"]) + "%")
                st.caption(job["salary"] + " · " + job["posted"])
                if "Needs review" in job["geo"]:
                    st.warning("Geographic eligibility requires verification (fictional)")
                else:
                    st.success("Location eligible in demo configuration")
                st.markdown("#### Role overview")
                st.write(job["description"])
                with st.expander("Why this job matches / what to verify"):
                    for reason in job["matches"]:
                        st.write("Match: " + reason)
                    for issue in job["verify"]:
                        st.write("Verify: " + issue)
                action, track = st.columns(2)
                with action:
                    if st.button("Save job" if job["id"] not in st.session_state["jp_demo_saved"] else "Unsave job",
                                 key="jp_demo_save_"+job["id"], use_container_width=True):
                        if job["id"] in st.session_state["jp_demo_saved"]:
                            st.session_state["jp_demo_saved"].remove(job["id"])
                        else:
                            st.session_state["jp_demo_saved"].add(job["id"])
                        st.rerun()
                with track:
                    st.button("Apply (demo unavailable)", key="jp_demo_apply_"+job["id"],
                              disabled=True, use_container_width=True,
                              help="Fictional listings have no application URL")
                stage = st.selectbox("Application stage (demo only)", STAGES,
                                     index=STAGES.index(st.session_state["jp_demo_stages"].get(job["id"], "Interested")),
                                     key="jp_demo_stage_"+job["id"])
                if st.button("Update stage", key="jp_demo_update_"+job["id"]):
                    st.session_state["jp_demo_stages"] = set_demo_status(st.session_state["jp_demo_stages"], job["id"], stage)
                    st.success("Demo stage updated in this browser session only.")
                st.caption("Original posting: unavailable for fictional listings. Apply and Mark as Applied are separate in live mode.")
    elif page == "Saved Jobs":
        st.subheader("Saved opportunities")
        saved = [j for j in DEMO_JOBS if j["id"] in st.session_state["jp_demo_saved"]]
        if not saved:
            st.info("Nothing saved yet. Open Discover to save a sample job.")
        for j in saved:
            with st.container(border=True):
                a, b = st.columns([4,1])
                with a:
                    st.markdown("**" + j["title"] + "**")
                    st.caption(j["company"] + " · " + j["work_type"])
                with b:
                    if st.button("Remove", key="jp_demo_remove_"+j["id"]):
                        st.session_state["jp_demo_saved"].remove(j["id"])
                        st.rerun()
    elif page == "Applications":
        st.subheader("Application board")
        st.caption("Fictional pipeline. Change a stage in Discover to see this board update.")
        groups = group_demo_applications(st.session_state["jp_demo_stages"])
        for start in (0, 3):
            columns = st.columns(3)
            for column, stage in zip(columns, STAGES[start:start+3]):
                with column:
                    with st.container(border=True):
                        st.markdown(f"**{stage} · {len(groups[stage])}**")
                        if not groups[stage]:
                            st.caption("No jobs in this stage")
                        for j in groups[stage]:
                            st.markdown("**"+j["title"]+"**")
                            st.caption(j["company"])
                            st.divider()
    elif page == "Settings":
        st.subheader("Demo workspace settings")
        st.caption("This is a synthetic profile. Return to Live mode to edit actual sources and settings.")
        st.write("Sample categories: technical support, service operations, cybersecurity.")
        st.write("Sample regions are fictitious; demo preferences do not affect scoring, eligibility or production data.")
        if st.button("Reset demo changes", key="jp_demo_reset"):
            st.session_state["jp_demo_saved"] = set()
            st.session_state["jp_demo_stages"] = {"DEMO-001":"Interested", "DEMO-002":"Applied", "DEMO-003":"Interview"}
            st.success("Demo session reset.")
