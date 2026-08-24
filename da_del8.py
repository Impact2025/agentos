import urllib.request, urllib.parse, json, hmac, hashlib
USER_TOK=open("D:/apps/impactos/_fb_token.tmp").read().strip()
# grab app secret from env
env=open("D:/apps/impactos/.env",encoding="utf-8",errors="ignore").read()
import re
aid=re.search(r"FACEBOOK_APP_ID=([0-9]+)",env); asec=re.search(r"FACEBOOK_APP_SECRET=([A-Za-z0-9]+)",env)
print("app_id",aid.group(1) if aid else None,"app_secret?",bool(asec))
def gq(tok,path,params=None,method="GET",ver="v19.0"):
    params=dict(params or {}); params["access_token"]=tok
    url=f"https://graph.facebook.com/{ver}/"+path+"?"+urllib.parse.urlencode(params)
    req=urllib.request.Request(url,method=method)
    try:
        with urllib.request.urlopen(req,timeout=30) as r: return json.loads(r.read().decode())
    except urllib.error.HTTPError as e: return {"__error__":e.read().decode()}
acc=gq(USER_TOK,"me/accounts",{"fields":"id,name,access_token","limit":"200"})
PTOK=None
for pg in acc.get("data",[]):
    if pg.get("id")=="279095855546040": PTOK=pg.get("access_token"); break
CID="1481086660732186_1383662309803744"
for ver in ["v19.0","v21.0"]:
    print(ver,"DELETE:",json.dumps(gq(PTOK,CID,method="DELETE",ver=ver),ensure_ascii=False)[:200])
