import sys,asyncio,time
sys.path.insert(0,'.')
from backend.domains.outlook import service as outlook
import sqlite3
async def main():
    t=time.time()
    done=0
    try:
        async for ev in outlook.batch_triage(limit=20):
            if ev.get('type') in ('batch_done','done'):
                done=ev.get('total',0)
        print('GETRIEERD:',done,'in %.1fs'%(time.time()-t),flush=True)
    except Exception as e:
        print('TRIAGE ERR',repr(e),flush=True)
    t2=time.time()
    try:
        n=await outlook.ensure_suggested_replies(limit=6)
        print('CONCEPTEN:',n,'in %.1fs'%(time.time()-t2),flush=True)
    except Exception as e:
        print('CONCEPT ERR',repr(e),flush=True)
    c=sqlite3.connect('data/agentos.db'); c.row_factory=sqlite3.Row
    r=c.execute("SELECT COUNT(*) n FROM outlook_emails WHERE folder='inbox' AND triage_label=''").fetchone()['n']
    print('ONGETRIEERD over:',r,flush=True)
asyncio.run(main())
print('KAAR',flush=True)
