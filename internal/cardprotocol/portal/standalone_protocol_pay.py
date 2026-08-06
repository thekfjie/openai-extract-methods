from __future__ import annotations
import base64
import copy
import json
import sys
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any
import ph_checkout_protocol as protocol

UA="Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:144.0) Gecko/20100101 Firefox/144.0"
def emit(message:str,level:str="info",phase:str="")->None:print(json.dumps({"type":"log","level":level,"message":message,"phase":phase},ensure_ascii=False),flush=True)
def saved_methods(snapshot:dict[str,Any])->list[str]:
 proxy=protocol.normalize_proxy(str(snapshot.get("proxy") or snapshot.get("checkout_proxy") or ""));http=protocol.build_identity_http(proxy,str(snapshot.get("user_agent") or UA));did=str(snapshot.get("checkout_device_id") or uuid.uuid4());protocol.install_cookies(http,dict(snapshot.get("session_cookies") or {}),did);aid=str(snapshot.get("chatgpt_account_id") or "")
 headers={"Authorization":f"Bearer {str(snapshot.get('access_token') or '')}","ChatGPT-Account-Id":aid,"OAI-Device-Id":did,"User-Agent":str(snapshot.get("user_agent") or UA),"Accept":"application/json","Origin":"https://chatgpt.com","Referer":"https://chatgpt.com/"}
 r=http.get("https://chatgpt.com/backend-api/payments/payment_methods",params={"account_id":aid},headers=headers,timeout=45)
 if r.status_code in {401,403}:raise RuntimeError(f"AT_INVALID_OR_EXPIRED: HTTP {r.status_code}")
 if r.status_code!=200:raise RuntimeError(f"PAYMENT_METHOD_QUERY_FAILED: HTTP {r.status_code}")
 try:b=r.json()
 except Exception as exc:raise RuntimeError("PAYMENT_METHOD_QUERY_NON_JSON") from exc
 items=b if isinstance(b,list) else ((b.get("data") or b.get("payment_methods") or []) if isinstance(b,dict) else [])
 return [str(x.get("id") or "") for x in items if isinstance(x,dict) and str(x.get("id") or "").startswith("pm_")]
def prepare(snapshot:dict[str,Any],proxies:list[str])->dict[str,Any]:
 last="";snapshot.setdefault("user_agent",UA)
 for index,candidate in enumerate(proxies[:3],1):
  a=dict(snapshot);a["proxy"]=candidate;a["checkout_proxy"]=candidate
  if not bool(snapshot.get("preserve_checkout_identity")):
   a["checkout_device_id"]=str(uuid.uuid4());a["checkout_chatgpt_session_id"]=""
  else:emit(f"Reusing original Checkout identity: {str(snapshot.get('context_source') or 'context_store')} (route {index})",phase="validate")
  try:
   emit(f"Preparing saved card route {index}/{min(3,len(proxies))}",phase="validate");methods=saved_methods(a)
   if not methods:raise RuntimeError("NO_SAVED_PAYMENT_METHOD: bind a card first")
   proxy=protocol.normalize_proxy(candidate);http=protocol.build_identity_http(proxy,str(a.get("user_agent") or UA));did=str(a.get("checkout_device_id") or uuid.uuid4());protocol.install_cookies(http,dict(a.get("session_cookies") or {}),did)
   state=protocol.resolve_oaics_checkout(http,a,str(a.get("processor_entity") or "openai_llc"),str(a.get("checkout_session_id") or ""),did);pk=str(state.get("publishable_key") or "");amount,currency=protocol.oaics_total(state)
   stripe=protocol.build_identity_http(proxy,str(a.get("user_agent") or UA));ctx=protocol.fetch_deferred_elements(stripe,state,str(a.get("checkout_session_id") or ""),str(a.get("user_agent") or UA))
   checkout_methods=list(ctx.get("customer_payment_method_ids") or [])
   if methods[0] not in checkout_methods:raise RuntimeError("SAVED_PAYMENT_METHOD_CONTEXT_MISMATCH: saved card is not attached to this Checkout customer")
   emit(f"Checkout customer/card context matched: customer={str(ctx.get('customer_id') or '')[-6:]} pm={methods[0][-6:]}",phase="validate")
   ctoken=protocol.create_confirmation_token(stripe,pk,methods[0],str(state.get("confirm_return_url") or ""),setup_only=(amount==0),elements_context=ctx,user_agent=str(a.get("user_agent") or UA))
   emit("Saved PaymentMethod and ConfirmationToken are prepared",phase="prepared")
   pp={"short_url":a.get("short_url"),"processor_entity":a.get("processor_entity"),"checkout_session_id":a.get("checkout_session_id"),"access_token":a.get("access_token"),"chatgpt_account_id":a.get("chatgpt_account_id"),"session_cookies":a.get("session_cookies") or {},"user_agent":a.get("user_agent") or UA,"checkout_device_id":a.get("checkout_device_id"),"checkout_chatgpt_session_id":a.get("checkout_chatgpt_session_id"),"email":a.get("email"),"proxy":candidate,"proxies":[candidate],"confirmation_token":ctoken,"card_retry_count":0,"card_retry_delay":1,"prepared_checkout_state":state,"prepared_elements_context":ctx,"prepared_amount":amount,"prepared_currency":currency,"preserve_checkout_identity":True,"billing_details":a.get("billing_details") or {}}
   return {"status":"prepared","prepared":{"protocol_payload":pp},"safe":{"amount":amount,"currency":currency.upper(),"saved_payment_method_count":len(methods),"route_attempt":index}}
  except Exception as exc:last=f"{type(exc).__name__}: {exc}";emit(f"Preparation route {index} failed: {last}","warn","validate")
 raise RuntimeError(last or "PREPARE_FAILED")
def confirm(prepared:dict[str,Any])->dict[str,Any]:
 pp=dict(prepared.get("protocol_payload") or {})
 if not pp or not str(pp.get("confirmation_token") or "").startswith("ctoken_"):raise ValueError("PREPARED_CONTEXT_INVALID")
 emit("Barrier released; submitting subscription now",phase="protocol");result=dict(protocol.run(pp) or {});result.update({"flow":"batch_saved_card_protocol","account_email":str(pp.get("email") or ""),"checkout_url":str(pp.get("short_url") or "")});return result
def confirm_burst(prepared:dict[str,Any],count:int)->dict[str,Any]:
 count=max(1,min(10,int(count or 10)));barrier=threading.Barrier(count);errors=[];verification=[]
 def worker(index:int):
  barrier.wait(timeout=10)
  result=confirm(copy.deepcopy(prepared));result["burst_attempt"]=index;result["burst_count"]=count;return result
 emit(f"Launching {count} concurrent confirmation requests in one process",phase="protocol")
 with ThreadPoolExecutor(max_workers=count,thread_name_prefix="confirm") as pool:
  futures=[pool.submit(worker,index) for index in range(1,count+1)]
  for future in as_completed(futures):
   try:
    result=dict(future.result() or {})
    if str(result.get("status") or "").lower()=="verification_required":verification.append(result)
    else:return result
   except Exception as exc:errors.append(f"{type(exc).__name__}: {exc}")
 if verification:return verification[0]
 raise RuntimeError(errors[-1] if errors else "CONFIRM_BURST_FAILED")
def main()->int:
 payload=json.loads(sys.stdin.read() or "{}");mode=str(payload.get("mode") or "run").lower()
 if mode=="prepare":result=prepare(dict(payload.get("snapshot") or {}),list(payload.get("proxies") or []))
 elif mode=="confirm":result=confirm(dict(payload.get("prepared") or {}))
 elif mode=="confirm_burst":result=confirm_burst(dict(payload.get("prepared") or {}),int(payload.get("burst_count") or 10))
 else:
  pre=prepare(dict(payload.get("snapshot") or {}),list(payload.get("proxies") or []));result=confirm(dict(pre.get("prepared") or {}))
 print(json.dumps({"type":"result","result":result},ensure_ascii=False),flush=True);return 0
if __name__=="__main__":
 try:raise SystemExit(main())
 except Exception as exc:print(json.dumps({"type":"error","error":f"{type(exc).__name__}: {exc}"},ensure_ascii=False),flush=True);raise SystemExit(1)
