"""Stage 6D1: unified dashboard with guarded Saved Jobs edits."""
from contextlib import closing
from pathlib import Path
import sqlite3
import pandas as pd
import streamlit as st

DB_PATH = Path(__file__).parent / 'jobpilot.db'
STATUS_CHOICES = ['Interested', 'Applied', 'Interview', 'Rejected', 'Offer', 'Archived']
COLUMNS = ['source', 'record_id', 'company', 'title', 'status', 'date', 'url', 'notes', 'location', 'score', 'reason']

def load_application_history(db_path=DB_PATH):
    """Read existing jobs and unmatched tracker history without writing to SQLite."""
    db_path = Path(db_path).resolve()
    if not db_path.is_file():
        raise FileNotFoundError(f'Application database not found: {db_path.name}')
    db_uri = f'{db_path.as_uri()}?mode=ro'
    with closing(sqlite3.connect(db_uri, uri=True)) as con:
        con.row_factory = sqlite3.Row
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if 'jobs' not in tables:
            raise ValueError('Expected jobs table is missing')
        main = [dict(r) for r in con.execute('SELECT id,company,title,status,posted_at,url,notes,location,score FROM jobs ORDER BY id')]
        unmatched = ([dict(r) for r in con.execute('SELECT record_key,company,title,status,added_date,job_url,reason,source_export_row FROM rabbitresume_unmatched_history ORDER BY source_export_row,record_key')]
                     if 'rabbitresume_unmatched_history' in tables else [])
    records = []
    for r in main:
        records.append(dict(source='Saved Jobs', record_id=str(r['id']), company=r['company'] or '', title=r['title'] or '',
                            status=r['status'] or 'Interested', date='', url=r['url'] or '', notes=r['notes'] or '',
                            location=r['location'] or '', score=r['score'], reason=''))
    for r in unmatched:
        records.append(dict(source='RabbitResume history', record_id=r['record_key'], company=r['company'] or '',
                            title=r['title'] or '', status=r['status'] or '', date=r['added_date'] or '',
                            url=r['job_url'] or '', notes='', location='', score=None, reason=r['reason'] or ''))
    return pd.DataFrame(records, columns=COLUMNS), len(main), len(unmatched)



def update_saved_job_guarded(db_path, job_id, expected_status, expected_notes, status, notes):
    """Update one existing Saved Job after explicit confirmation. No ledger writes.

    Fail closed if it has been edited since the dashboard was loaded.
    """
    if status not in STATUS_CHOICES:
        raise ValueError('Unsupported application status')
    if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id <= 0:
        raise ValueError('Invalid Saved Jobs ID')
    db_path = Path(db_path).resolve()
    if not db_path.is_file():
        raise FileNotFoundError('Application database does not exist')
    with closing(sqlite3.connect(db_path, timeout=15)) as con:
        con.execute('BEGIN IMMEDIATE')
        row = con.execute('SELECT status, notes FROM jobs WHERE id=?', (job_id,)).fetchone()
        if row is None:
            raise ValueError('Saved Job no longer exists; refresh dashboard')
        if (row[0] or 'Interested') != expected_status or (row[1] or '') != expected_notes:
            raise ValueError('Saved Job changed elsewhere; refresh before saving')
        con.execute('UPDATE jobs SET status=?, notes=? WHERE id=?', (status, notes, job_id))
        con.commit()


def render_application_dashboard(db_path=DB_PATH):
    st.subheader('Application dashboard')
    st.caption('Unified overview with confirmed edits for Saved Jobs only. '
               'RabbitResume Added Date is not assumed to be an application date.')
    try:
        df, main_count, unmatched_count = load_application_history(db_path)
    except (FileNotFoundError, sqlite3.Error, ValueError) as exc:
        st.error(f'Could not read application history: {exc}')
        return
    if df.empty:
        st.info('No application records found yet.')
        return
    statuses = df['status'].fillna('Unknown').replace('', 'Unknown')
    counts = statuses.value_counts()
    c1, c2, c3, c4 = st.columns(4)
    c1.metric('Total tracked records', len(df))
    c2.metric('Applied', int(counts.get('Applied', 0)))
    c3.metric('Rejected', int(counts.get('Rejected', 0)))
    c4.metric('Interview / Offer', int(counts.get('Interview', 0) + counts.get('Offer', 0)))
    st.caption(f'{main_count} Saved Jobs records + {unmatched_count} separate history records. '
               'Counts are records, not necessarily unique job openings; possible duplicates were preserved.')
    st.markdown('#### Status breakdown')
    st.bar_chart(counts.rename_axis('Status').rename('Records'))
    c1, c2, c3 = st.columns(3)
    with c1:
        selected_statuses = st.multiselect('Filter statuses', sorted(df['status'].fillna('Unknown').unique().tolist()))
    with c2:
        selected_source = st.selectbox('Record source', ['Both sources', 'Saved Jobs', 'RabbitResume history'])
    with c3:
        query = st.text_input('Search company or title')
    shown = df.copy()
    if selected_statuses:
        shown = shown[shown['status'].isin(selected_statuses)]
    if selected_source != 'Both sources':
        shown = shown[shown['source'] == selected_source]
    if query.strip():
        q = query.strip()
        shown = shown[shown['company'].str.contains(q, case=False, regex=False, na=False) |
                    shown['title'].str.contains(q, case=False, regex=False, na=False)]
    st.caption(f'Showing {len(shown)} of {len(df)} records')
    display_cols = ['source', 'company', 'title', 'status', 'date', 'location', 'score', 'url', 'notes', 'reason']
    st.dataframe(shown[display_cols], use_container_width=True, hide_index=True,
                 column_config={'date': st.column_config.TextColumn('Tracker Added Date (if known)'),
                                'url': st.column_config.LinkColumn('Job URL')})
    st.download_button('Download filtered records CSV', data=shown[display_cols].to_csv(index=False).encode('utf-8-sig'),
                       file_name='jobpilot_application_dashboard.csv', mime='text/csv')
    st.markdown('#### Update a Saved Job')
    st.caption('Only Saved Jobs can be edited here. Historical RabbitResume ledger records remain read-only. '
               'No changes are made until you explicitly click Save changes.')
    saved_rows = df[df['source'] == 'Saved Jobs']
    if saved_rows.empty:
        st.info('No editable Saved Jobs exist.')
        return
    choices = {f"#{r['record_id']} — {r['company']} — {r['title']}": r
               for _, r in saved_rows.iterrows()}
    selected_label = st.selectbox('Saved Job to edit', list(choices.keys()), key='stage6d_job')
    selected = choices[selected_label]
    job_id = int(selected['record_id'])
    previous_status = selected['status']
    previous_notes = selected['notes']
    with st.form(key=f'stage6d_edit_{job_id}'):
        new_status = st.selectbox('New status', STATUS_CHOICES,
                                  index=STATUS_CHOICES.index(previous_status) if previous_status in STATUS_CHOICES else 0)
        new_notes = st.text_area('Notes / interview updates / follow-up details', value=previous_notes)
        confirm = st.checkbox('I reviewed this update and want to save it')
        submitted = st.form_submit_button('Save changes')
    if submitted:
        if not confirm:
            st.warning('Check the confirmation box before saving.')
        elif new_status == previous_status and new_notes == previous_notes:
            st.info('No changes to save.')
        else:
            try:
                update_saved_job_guarded(db_path, job_id, previous_status, previous_notes,
                                         new_status, new_notes)
                st.success('Saved Job updated. Dashboard counts will refresh.')
                st.rerun()
            except (sqlite3.Error, OSError, ValueError) as exc:
                st.error(f'No changes saved: {exc}')
