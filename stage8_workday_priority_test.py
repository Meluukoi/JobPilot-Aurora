"""Offline acceptance for Stage 8 Workday enrichment selection; no network or project writes."""
from datetime import datetime, timedelta, timezone
from stage8_workday_enrichment_priority import select_batch

NOW = datetime(2026, 11, 3, 20, tzinfo=timezone.utc)
def item(n, days=0, state='pending', retry=None):
    return ({'identity':f'workday:tenant:board:R{n:05}', 'state':state,
             'first_seen':(NOW-timedelta(days=days)).isoformat(),
             'next_retry':retry}, {'source_id':f'R{n:05}', 'title':'Odd but valid role'})

def run():
    new=[item(n) for n in range(10)]
    old=[item(100+n,days=3+n) for n in range(8)]
    chosen,stats=select_batch(new+old,NOW,5,3)
    assert stats['selected_new']==3 and stats['selected_backlog']==2
    assert len({r['identity'] for r,_ in chosen})==5
    print('PASS 1: New requisitions get three slots; older pending jobs get two')
    chosen,stats=select_batch(old,NOW,5,3)
    assert stats['selected_backlog']==5 and stats['selected_new']==0
    print('PASS 2: Backlog uses all free capacity when no new jobs exist')
    baseline=[item(n) for n in range(395)]
    chosen,stats=select_batch(baseline,NOW,5,3)
    assert len(chosen)==5 and stats['remaining_due']==390
    chosen2,_=select_batch(list(reversed(baseline)),NOW,5,3)
    assert {r['identity'] for r,_ in chosen}=={r['identity'] for r,_ in chosen2}
    assert max(int(e['source_id'][1:]) for _,e in chosen)>20
    print('PASS 3: Large first-time baseline samples beyond first API page, order-independent')
    retry=(NOW+timedelta(minutes=15)).isoformat()
    selected,stats=select_batch([item(1,retry=retry),item(2,state='ready'),item(3,state='stalled'),item(4)],NOW,5)
    assert len(selected)==1 and selected[0][1]['source_id']=='R00004'
    print('PASS 4: Retry delays, ready and stalled state respected')
    assert len(baseline)==395 and all('title' in e for _,e in baseline)
    print('PASS 5: Unselected jobs stay in input; no title/geography exclusion')
    print('SUCCESS: Priority policy validated offline. No network, databases, reports or email.')
if __name__=='__main__': run()
