# -*- coding: utf-8 -*-
"""
네이버 키워드 SEO 데이터 모듈 (레퍼런스 봇과 동일 기능).

- 검색광고 API(keywordstool): 월간 검색량(PC+모바일), 경쟁도
- 검색 API(blog search): 블로그 문서수(total)
- 골든 점수 = 월간검색량 / 블로그문서수  (높을수록 '검색 많고 경쟁 적은' 좋은 키워드)

필요한 키(.env):
  NAVER_SEARCH_CLIENT_ID / NAVER_SEARCH_CLIENT_SECRET   (개발자센터 검색 API)
  NAVER_AD_API_KEY / NAVER_AD_SECRET_KEY / NAVER_AD_CUSTOMER_ID  (검색광고 API)
키가 없으면 이 모듈은 조용히 건너뜁니다.
"""
import os, time, hmac, hashlib, base64, urllib.parse, json
import urllib.request


def _has_ad_keys():
    return all(os.getenv(k) for k in ["NAVER_AD_API_KEY", "NAVER_AD_SECRET_KEY", "NAVER_AD_CUSTOMER_ID"])


def _has_search_keys():
    return all(os.getenv(k) for k in ["NAVER_SEARCH_CLIENT_ID", "NAVER_SEARCH_CLIENT_SECRET"])


def _ad_signature(timestamp, method, uri, secret):
    msg = f"{timestamp}.{method}.{uri}"
    dig = hmac.new(secret.encode("utf-8"), msg.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(dig).decode("utf-8")


def _to_int(v):
    try:
        if isinstance(v, str) and "<" in v:
            return 5  # "< 10" 같은 값
        return int(v)
    except Exception:
        return 0


def get_search_volume(keywords):
    """검색광고 API로 월간 검색량·경쟁도 조회. {키워드: {'volume':.., 'comp':..}}"""
    if not _has_ad_keys():
        return {}
    api_key = os.getenv("NAVER_AD_API_KEY"); secret = os.getenv("NAVER_AD_SECRET_KEY")
    customer = os.getenv("NAVER_AD_CUSTOMER_ID")
    uri = "/keywordstool"
    ts = str(round(time.time() * 1000))
    sig = _ad_signature(ts, "GET", uri, secret)
    hint = ",".join(k.replace(" ", "") for k in keywords[:5])
    url = f"https://api.naver.com{uri}?" + urllib.parse.urlencode({"hintKeywords": hint, "showDetail": "1"})
    req = urllib.request.Request(url, headers={
        "X-Timestamp": ts, "X-API-KEY": api_key, "X-Customer": customer, "X-Signature": sig,
    })
    out = {}
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        for row in data.get("keywordList", []):
            kw = row.get("relKeyword", "")
            vol = _to_int(row.get("monthlyPcQcCnt", 0)) + _to_int(row.get("monthlyMobileQcCnt", 0))
            out[kw] = {"volume": vol, "comp": row.get("compIdx", "-")}
    except Exception as e:
        print(f"  [키워드] 검색광고 API 오류: {e}")
    return out


def get_blog_doc_count(keyword):
    """검색 API로 블로그 문서수(total) 조회."""
    if not _has_search_keys():
        return None
    url = "https://openapi.naver.com/v1/search/blog.json?" + urllib.parse.urlencode({"query": keyword, "display": 1})
    req = urllib.request.Request(url, headers={
        "X-Naver-Client-Id": os.getenv("NAVER_SEARCH_CLIENT_ID"),
        "X-Naver-Client-Secret": os.getenv("NAVER_SEARCH_CLIENT_SECRET"),
    })
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return json.loads(r.read().decode("utf-8")).get("total", None)
    except Exception as e:
        print(f"  [키워드] 검색 API 오류: {e}")
        return None


def analyze(keywords):
    """키워드 리스트를 검색량·문서수·골든점수로 평가해 정렬 리스트 반환."""
    if not (_has_ad_keys() or _has_search_keys()):
        return []  # 키 없으면 건너뜀
    vols = get_search_volume(keywords)
    rows = []
    for kw in keywords:
        vol = vols.get(kw.replace(" ", ""), {}).get("volume", vols.get(kw, {}).get("volume", 0))
        comp = vols.get(kw.replace(" ", ""), {}).get("comp", "-")
        docs = get_blog_doc_count(kw)
        golden = round(vol / docs, 4) if (docs and docs > 0) else None
        rows.append({"keyword": kw, "volume": vol, "comp": comp, "docs": docs, "golden": golden})
    # 골든점수 높은 순
    rows.sort(key=lambda x: (x["golden"] is not None, x["golden"] or 0), reverse=True)
    return rows


def print_report(rows):
    if not rows:
        print("  (키워드 데이터 없음 — 네이버 검색/검색광고 API 키를 넣으면 검색량·골든점수가 표시됩니다.)")
        return
    print("  키워드 SEO 데이터 (골든점수 높은 순):")
    print("  " + "-" * 58)
    print(f"  {'키워드':<16}{'월검색량':>10}{'문서수':>12}{'골든점수':>12}")
    for r in rows:
        docs = f"{r['docs']:,}" if r["docs"] is not None else "-"
        gold = f"{r['golden']}" if r["golden"] is not None else "-"
        print(f"  {r['keyword']:<16}{r['volume']:>10,}{docs:>12}{gold:>12}")
