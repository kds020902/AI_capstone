"""사기 전에 맞춰보기: 사려는 옷(구매 후보)을 지금 옷장과 맞춰 본다.

계절마다 대표 기온(core/taxonomy.py SEASON_TEMPS) × 외출 목적 4가지에서
1) 후보와 같이 입을 수 있는 내 옷이 몇 벌인지, 후보가 들어간 코디가 몇 개인지 (같은 코디는 한 번만 셈)
2) 후보가 들어가면 그 상황의 최고 코디 점수가 얼마나 오르는지 (옷장에 비어 있던 걸 채우는지)
3) 같은 종류·같은 색 옷이 이미 있는지
를 계산하고, 한 줄 판단(verdict)을 붙인다. 추천 규칙은 core/recommender.py 를 그대로 쓴다.
"""
from __future__ import annotations

from core.colors import normalize_color
from core.eligibility import wearable
from core.recommender import recommend
from core.taxonomy import PURPOSES, SEASON_TEMPS, SEASONS, canonical_subcategory, item_seasons

CANDIDATE_ID = -1        # 후보 옷의 임시 id (옷장 옷 id와 겹치지 않게 음수)
IMPROVE_MIN = 0.05       # 최고 점수가 이만큼 이상 오르면 '옷장에 없던 걸 채운다'고 본다


def similar_items(cand, items):
    """같은 종류인 옷과 그중 색까지 같은 옷."""
    sub = canonical_subcategory(cand.get("subcategory"))
    same = [x for x in items if canonical_subcategory(x.get("subcategory")) == sub]
    color = normalize_color(cand.get("color"))
    same_color = [x for x in same if color and normalize_color(x.get("color")) == color]
    return {"same_type": same, "same_color": same_color}


def candidate_report(candidate, items, cold=0.0, heat=0.0):
    """반환 {"seasons", "cells", "total", "partners", "improves", "similar", "verdict"}.
    cells[(계절, 목적)] = {"n": 코디 수, "best": 최고 점수, "best_without": 후보 없이 최고 점수, "results": TOP3, "temp"}"""
    cand = {**candidate, "id": CANDIDATE_ID}
    seasons = item_seasons(cand)
    cells, all_keys = {}, set()
    for season in SEASONS:
        for purpose in PURPOSES:
            cell, keys = _cell(cand, items, season in seasons, SEASON_TEMPS[season], purpose, cold, heat)
            all_keys |= keys
            cells[(season, purpose)] = cell

    improves = [{"season": s, "purpose": p, "before": c["best_without"], "after": c["best"]}
                for (s, p), c in cells.items()
                if c["best"] is not None and (c["best_without"] is None or c["best"] - c["best_without"] >= IMPROVE_MIN)]
    improves.sort(key=lambda x: -(x["after"] - (x["before"] or 0)))
    partners = sorted({i for k in all_keys for i in k if i != CANDIDATE_ID})
    similar = similar_items(cand, items)
    return {"seasons": seasons, "cells": cells, "total": len(all_keys), "partners": partners, "improves": improves,
            "similar": similar, "verdict": _verdict(similar, len(all_keys), improves, partners)}


def _cell(cand, items, in_season, temps, purpose, cold, heat):
    """한 상황(계절 대표 기온들 × 목적): 후보 없이 최고 점수, 후보를 넣은 코디 수·최고 점수·TOP 3."""
    cell = {"n": 0, "best": None, "best_without": None, "results": [], "temp": temps[-1]}
    keys = set()
    for t in temps:
        base = recommend(items, t, t, 60, 0, False, 8, purpose, cold, heat, 1)
        if base["results"]:
            s = base["results"][0]["score"]
            cell["best_without"] = s if cell["best_without"] is None else max(cell["best_without"], s)
        if not in_season or not wearable(cand, base["context"]):
            continue
        out = recommend(items + [cand], t, t, 60, 0, False, 8, purpose, cold, heat, 3, must_include=CANDIDATE_ID)
        keys.update(tuple(k) for k in out["combo_keys"])
        if out["results"] and (cell["best"] is None or out["results"][0]["score"] > cell["best"]):
            cell["best"], cell["results"], cell["temp"] = out["results"][0]["score"], out["results"], t
    cell["n"] = len(keys)
    return cell, keys


def _verdict(similar, total, improves, partners):
    """한 줄 판단: 중복 → 코디 없음 → 빈 곳 채움 → 잘 어울림."""
    if similar["same_color"]:
        names = ", ".join(x["name"] for x in similar["same_color"][:3])
        return "dup", f"거의 같은 옷이 이미 있어요 ({names}). 한 번 더 생각해 보세요."
    if total == 0:
        return "none", "지금 옷장으로는 함께 입을 코디가 없어요."
    if improves:
        where = ", ".join(f"{x['season']} {x['purpose']}" for x in improves[:3])
        return "gap", f"옷장에 없던 걸 채워 줘요. {where}에서 코디 점수가 올라가요."
    return "fit", f"가진 옷 {len(partners)}벌과 어울려요."
