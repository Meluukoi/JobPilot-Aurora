# Aurora portfolio demo mode

This is a **local, fictional, session-only** showcase of JobPilot's user experience.
It is not an independently verified job search, external application, or live notification workflow.

## Run

Install requirements in a virtual environment, then launch:

```powershell
.\.venv\Scripts\python.exe -B -m streamlit run app.py --server.address 127.0.0.1 --server.port 8501 --server.headless true --browser.gatherUsageStats false
```

Enable **Demo mode (fictional jobs)** in the sidebar. Discover, Saved Jobs,
Applications, and Settings will display synthetic data. Changes are stored
only in the current Streamlit browser session; none of the demo actions call
collectors, write the job database, modify discovery history, send emails, or
submit applications. Fictional listings have no application URLs.

Turn Demo Mode off to access the original live configuration and collection
functions. **Live mode is not a production-safe hosted multi-user service.**
The real collection code can contact supported ATS endpoints and persist
history; configure sources deliberately before triggering a scan.

## Validation

```powershell
.\.venv\Scripts\python.exe -B -m unittest discover -p "test_*.py" -q
```

The original baseline is 263 tests, including four skips for missing private
historical fixtures. The synthetic demo adds five offline tests.

## Public release safeguards

Before publishing: inspect `git status --short`, `git ls-files --cached`,
review all files for credentials, private paths, realistic candidate profiles,
work histories and application details, and verify only synthetic fixtures are
included. The presence of `.gitignore` by itself is not proof of privacy.
Do not publish any local database or actual credentials. Do not claim the
fictional demo demonstrates real-time job availability or verified dates.
