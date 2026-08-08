# -*- coding: utf-8 -*-
"""
Unsplash 무료 이미지 자동 다운로드 모듈 (레퍼런스 봇과 동일 기능).

engine이 지정한 {type:"photo", query:"검색어"} 이미지를 Unsplash에서 받아온다.
필요한 키(.env): UNSPLASH_ACCESS_KEY  (없으면 건너뜀)
"""
import os, json, urllib.parse, urllib.request


def available():
    return bool(os.getenv("UNSPLASH_ACCESS_KEY"))


def fetch(query, out_path):
    """query로 Unsplash 검색 후 첫 결과를 out_path(jpg)로 저장. 성공하면 경로 반환."""
    key = os.getenv("UNSPLASH_ACCESS_KEY")
    if not key:
        return None
    url = "https://api.unsplash.com/search/photos?" + urllib.parse.urlencode({
        "query": query, "per_page": 1, "orientation": "landscape", "content_filter": "high",
    })
    req = urllib.request.Request(url, headers={"Authorization": f"Client-ID {key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            data = json.loads(r.read().decode("utf-8"))
        results = data.get("results", [])
        if not results:
            print(f"  [Unsplash] 결과 없음: {query}"); return None
        img_url = results[0]["urls"]["regular"]
        urllib.request.urlretrieve(img_url, out_path)
        print(f"  Unsplash 이미지 받음: {os.path.basename(out_path)}  (검색어: {query})")
        return out_path
    except Exception as e:
        print(f"  [Unsplash] 오류({query}): {e}")
        return None
