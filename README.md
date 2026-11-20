# JobPilot Aurora

**Local-first job discovery and application tracking, with a fictional interactive portfolio demo.**

JobPilot combines job collection, rule-based matching, review queues, application status tracking, and optional local email digests. The Streamlit interface uses the Aurora design: a split-view opportunity explorer, saved jobs, and an application pipeline.

**Portfolio default:** Demo Mode starts enabled. All three demo jobs and employers are fictional. Saving opportunities and moving cards between application stages affect only the current Streamlit session. Demo Mode does not fetch jobs, send emails, write to a job database, or submit applications. It does not establish current job availability or posting freshness.

## Quick start (Windows / PowerShell)

Requires Python 3.11+ and Git for the optional version-control workflow.

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -B -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
```

Open `http://127.0.0.1:8501`. The sidebar's **Demo mode (fictional jobs)** control is enabled initially. Navigate among **Discover**, **Saved Jobs**, **Applications**, and **Settings** to explore the demo. No real ATS configuration is needed to use it.

## Run offline tests

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -p "test_*.py" -q
```

During portfolio preparation, the suite completed 268 tests with four skips because private historical fixtures were not included. Test results may differ with environment and later changes.

## Local live mode (advanced)

The original local workflow is retained separately from the fictional demo. To experiment with it, make local copies of the example configuration files, then edit the copies for your own purposes:

```powershell
Copy-Item profile.example.json profile.json
Copy-Item candidate_profile.example.json candidate_profile.json
Copy-Item sources.example.csv sources.csv
```

These local filenames are excluded from Git. The sample source is **disabled** and the example candidate is **not approved** for unattended collection. Users must explicitly configure supported Greenhouse/Lever sources, review candidate evidence and policy settings, and deliberately opt in to scans. Live operations can contact employer APIs, persist local data, and, if separately configured, send email. Do not use live mode on a public, shared, or internet-facing Streamlit deployment; this repository is designed for local operation, not authenticated multi-user hosting.

`run_jobpilot_daily.cmd` is an **optional** Windows Task Scheduler launcher for an existing configured local workflow. Merely cloning this repository does not install or enable a scheduled task. Avoid enabling the launcher until sources, evidence, credentials, and email settings have been reviewed.

## Architecture

| Area | Responsibilities |
| --- | --- |
| `app.py` | Streamlit navigation, local workflow and demo routing |
| `portfolio_demo.py` | Fictional in-session jobs, saved items and pipeline |
| `collectors.py` | Greenhouse and Lever collection utilities |
| `scorer.py`, `candidate_matching.py`, `geography.py` | Rule-based match evidence and eligibility signals |
| `db.py`, `application_tracking.py`, `discovery_history.py` | Local persistence, deduplication and status tracking |
| `scheduled_scan.py`, `email_notifications.py`, `alert_policy.py` | Optional local scan, alert policy and email delivery |
| `stage8_*.py` | Workday/Greenhouse research, verification and regression modules; some are experimental |

## Privacy and limitations

- No candidate's personal employment history, resumes, real application records, databases, credentials, or email delivery state should be committed.
- `.gitignore` excludes local profile/configuration files, SQLite data, local reports, secrets, backups, and temporary development scripts. Review the staged Git diff before every publication; `.gitignore` alone is not a privacy audit.
- Match scores are heuristics, not guarantees of applicant suitability. Verification of dates, requirements, geographic eligibility, and source reliability remains necessary in live operation.
- Demo **Apply** controls are disabled; JobPilot does not automatically submit job applications.
- The project is a portfolio demonstration, not a live employment service or production-grade hosted SaaS application.

For demo-specific behavior, see [AURORA_DEMO.md](AURORA_DEMO.md).
