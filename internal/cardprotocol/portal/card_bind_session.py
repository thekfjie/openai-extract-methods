from __future__ import annotations
import json, sys, random, re, base64, hashlib, uuid, os
from pathlib import Path
from curl_cffi.requests import Session
ACCOUNT_FILE="/opt/account-service/success_accounts.jsonl"
ACCOUNT_API_BASE=os.getenv("CARD_ACCOUNT_API_BASE", "").strip().rstrip("/")
PK_RE=re.compile(r"pk_(?:live|test)_[A-Za-z0-9]{24,}")
def find_publishable_key(value):
 if isinstance(value,dict):
  for key in ("publishable_key","public_key","stripe_publishable_key","key"):
   candidate=str(value.get(key) or "").strip()
   if PK_RE.fullmatch(candidate): return candidate
  for item in value.values():
   found=find_publishable_key(item)
   if found:return found
 elif isinstance(value,list):
  for item in value:
   found=find_publishable_key(item)
   if found:return found
 elif isinstance(value,str):
  match=PK_RE.search(value)
  if match:return match.group(0)
 return ""
UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"

def auto_us_billing(email):
 addresses=[
  ("750 West 5th Avenue","Anchorage","AK","99501"),
  ("1209 Orange Street","Wilmington","DE","19801"),
  ("301 South Park Avenue","Helena","MT","59601"),
  ("100 North Main Street","Concord","NH","03301"),
  ("800 Northeast Oregon Street","Portland","OR","97232"),
 ]
 line1,city,state,postal_code=random.choice(addresses)
 names=["James Wilson","Michael Brown","Daniel Miller","David Anderson","Robert Taylor","William Thomas"]
 return {
  "name":random.choice(names),
  "email":str(email or ""),
  "phone":"+1202555%04d" % random.randint(100,9999),
  "address":{
   "country":"US",
   "line1":line1,
   "line2":"",
   "city":city,
   "state":state,
   "postal_code":postal_code,
  }
 }

def main():
 if not ACCOUNT_API_BASE:
  print(json.dumps({"ok":False,"error":"ACCOUNT_API_BASE_MISSING"})); return 11
 email=str(sys.argv[1] if len(sys.argv)>1 else "").strip().lower(); account=None
 access_token_override=str(sys.argv[3] if len(sys.argv)>3 else "").strip()
 email_candidate=None
 account_path=Path(ACCOUNT_FILE)
 if account_path.is_file():
  with account_path.open(encoding="utf-8",errors="ignore") as h:
   for line in h:
    try:item=json.loads(line)
    except:continue
    if access_token_override and str(item.get("access_token") or "").strip()==access_token_override:
     account=item
     break
    if str(item.get("email") or "").strip().lower()==email:
     email_candidate=item
 if not account and access_token_override:
  try:
   segment=access_token_override.split(".")[1];segment += "=" * (-len(segment) % 4);claims=json.loads(base64.urlsafe_b64decode(segment.encode()).decode());auth=claims.get("https://api.openai.com/auth") or {};profile=claims.get("https://api.openai.com/profile") or {};account_id=str(auth.get("chatgpt_account_id") or claims.get("account_id") or "").strip();token_hash=hashlib.sha256(access_token_override.encode()).hexdigest();decoded_email=str(profile.get("email") or claims.get("email") or email or f"external-{token_hash[:16]}@example.com").strip().lower();record_id=hashlib.sha256(("external-at:"+account_id).encode()).hexdigest()[:12];device_id=str(uuid.uuid5(uuid.NAMESPACE_URL,"reg153-external-at:"+account_id));account={"id":record_id,"email":decoded_email,"account_id":account_id,"access_token":access_token_override,"session_cookies":{"oai-did":device_id},"proxy":"DIRECT"}
  except Exception:
   account=None
 if not account: account=email_candidate
 if not account: print(json.dumps({"ok":False,"error":"ACCOUNT_NOT_FOUND"})); return 2
 if access_token_override:
  account=dict(account);account["access_token"]=access_token_override
 http=Session(impersonate="firefox144"); http.trust_env=False
 proxy=str(sys.argv[2] if len(sys.argv)>2 else account.get("proxy") or "").strip()
 if proxy and proxy.upper()!="DIRECT":http.proxies={"http":proxy,"https":proxy}
 cookies=account.get("session_cookies") or {}
 if isinstance(cookies,str):
  try:cookies=json.loads(cookies)
  except:cookies={}
 for name,value in dict(cookies).items():
  try:http.cookies.set(str(name),str(value),domain="chatgpt.com")
  except:pass
 headers={"Authorization":"Bearer "+str(account.get("access_token") or ""),"ChatGPT-Account-Id":str(account.get("account_id") or ""),"OAI-Device-Id":str(cookies.get("oai-did") or ""),"User-Agent":UA,"Accept":"*/*","Origin":ACCOUNT_API_BASE,"Referer":ACCOUNT_API_BASE+"/","Content-Type":"application/json"}
 try:
  response=http.post(ACCOUNT_API_BASE+"/backend-api/payments/payment_method",json={"account_id":account.get("account_id")},headers=headers,timeout=45)
  try:data=response.json() or {}
  except:data={}
  secret=str(data.get("client_secret") or "")
  if response.status_code!=200 or not secret.startswith("seti_"):
   detail=data.get("detail") or data.get("error") or ""
   print(json.dumps({"ok":False,"error":"SETUP_INTENT_FAILED","status":response.status_code,"detail":str(detail)[:300]})); return 3
  billing=auto_us_billing(email)
  print(json.dumps({"ok":True,"client_secret":secret,"publishable_key":find_publishable_key(data),"record_id":str(account.get("id") or ""),"account_email":email,"billing_details":billing})); return 0
 finally:http.close()
if __name__=="__main__":
 try:
  raise SystemExit(main())
 except SystemExit:
  raise
 except Exception as exc:
  print(json.dumps({"ok":False,"error":"CARD_BIND_HELPER_ERROR","detail":f"{type(exc).__name__}: {exc}"[:300]}))
  raise SystemExit(10)
