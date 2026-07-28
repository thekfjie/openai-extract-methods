from __future__ import annotations
METHOD_CATALOG = [
 {'id':'paypal_ba','name':'PayPal BA','countries':['US','GB','DE','FR','NL','CA','AU','IN','PH','TH','BA','AE','BR','TR','VN','JP','BH','MX'],'endpoint':'/api/long-link-task','source':'openai-pay-pp-src + pp_protocol'},
 {'id':'paper_card','name':'Paper Card Short Link','countries':['PH','US','JP','TR','TH','AE','BA'],'endpoint':'/api/paper-card-task','source':'methods/paper_card'},
 {'id':'ph_link','name':'Philippines PHP Link','countries':['PH'],'endpoint':'/api/ph-link-task','source':'methods/philippines_ticdk'},
 {'id':'momo','name':'Vietnam MoMo Eligibility','countries':['VN'],'endpoint':'/api/momo-eligibility','source':'methods/momo'},
 {'id':'kakao','name':'Korea Kakao Pay','countries':['KR'],'endpoint':'/api/kakao-long-link-task','source':'methods/kakao'},
 {'id':'upi','name':'India UPI','countries':['IN'],'endpoint':'/api/upi-long-link-task','source':'upi_go + panel'},
 {'id':'ideal','name':'Netherlands iDEAL','countries':['NL'],'endpoint':'/api/ideal-long-link-task','source':'panel ideal flow'},
]

def method_catalog_payload():
 return {'ok': True, 'methods': METHOD_CATALOG, 'count': len(METHOD_CATALOG)}
