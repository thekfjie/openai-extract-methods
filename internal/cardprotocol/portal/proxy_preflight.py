from __future__ import annotations
import json,sys
from concurrent.futures import ThreadPoolExecutor,as_completed
from curl_cffi.requests import Session
TRACE="https://www.cloudflare.com/cdn-cgi/trace"
def check(proxy,expected):
 s=Session(impersonate="firefox144");s.trust_env=False;s.proxies={"http":proxy,"https":proxy}
 try:
  r=s.get(TRACE,timeout=12)
  if r.status_code!=200:return None
  fields=dict(line.split("=",1) for line in str(r.text or "").splitlines() if "=" in line)
  return proxy if str(fields.get("loc") or "").upper()==expected else None
 except Exception:return None
 finally:
  try:s.close()
  except:pass
def validate(values,expected,limit=30):
 source=[]
 for item in values:
  value=str(item or "").strip()
  if value and value not in source:source.append(value)
 source=source[:100]
 valid=[]
 with ThreadPoolExecutor(max_workers=min(24,max(1,len(source)))) as pool:
  futures={pool.submit(check,p,expected):p for p in source}
  for future in as_completed(futures):
   try:result=future.result()
   except Exception:result=None
   if result:valid.append(result)
   if len(valid)>=limit:
    for pending in futures:pending.cancel()
    break
 return valid,len(source)
def main():
 data=json.loads(sys.stdin.read() or "{}")
 if "proxies" in data:
  expected=str(data.get("expected") or "US").upper();limit=max(1,min(30,int(data.get("limit") or 3)))
  valid,total=validate(data.get("proxies") or [],expected,limit)
  print(json.dumps({"ok":bool(valid),"proxies":valid,"expected":expected,"total":total,"valid":len(valid)}));return 0 if valid else 2
 entry_expected=str(data.get("entry_expected") or data.get("entry_country") or "US").upper()
 exit_expected=str(data.get("exit_expected") or data.get("exit_country") or "TR").upper()
 entry,entry_total=validate(data.get("entry") or [],entry_expected);exit,exit_total=validate(data.get("exit") or [],exit_expected)
 print(json.dumps({"ok":bool(entry and exit),"entry":entry,"exit":exit,"entry_expected":entry_expected,"exit_expected":exit_expected,"entry_total":entry_total,"exit_total":exit_total,"entry_valid":len(entry),"exit_valid":len(exit)}));return 0 if entry and exit else 2
if __name__=="__main__":raise SystemExit(main())
