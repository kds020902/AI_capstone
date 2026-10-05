// AI 옷장 코디 추천 엔진 — core/recommender.py · core/colors.py 를 자바스크립트로 옮긴 것.
// 규칙표·스타터 옷장·가중치는 파이썬 소스에서 뽑은 DATA를 그대로 쓴다.
(function (root) {
  "use strict";

  const COLOR_KO = {
    black: "검정", white: "흰색", gray: "회색", navy: "네이비", beige: "베이지", ivory: "아이보리",
    brown: "브라운", khaki: "카키", blue: "파랑", skyblue: "하늘색", red: "빨강", pink: "분홍",
    orange: "주황", yellow: "노랑", green: "초록", purple: "보라",
  };

  function create(D) {
    const canon = (s) => (s && D.LEGACY_RENAMES[s]) || s;
    const ruleFor = (sub) => D.RULES[canon(sub)] || null;
    const INCOMP = new Set(D.INCOMPATIBLE_PAIRS.map((p) => p.slice().sort().join("|")));
    const LAYER = new Set(D.LAYER_PAIRS.map((p) => p[0] + "|" + p[1]));
    const NEUTRALS = new Set(D.NEUTRALS);
    const CAT_LABEL = { 상의: "상의", 하의: "하의", 원피스: "원피스", 아우터: "겉옷", 신발: "신발" };
    const fmt0 = (v) => Math.round(v).toFixed(0);
    const round4 = (v) => parseFloat(v.toFixed(4));

    // ---------------------------------------------------------------- 색상
    const HEX = /^#?([0-9a-fA-F]{6})$/;
    function hexToName(hex) {
      const m = HEX.exec(String(hex).trim());
      if (!m) return null;
      const h = m[1], r = parseInt(h.slice(0, 2), 16), g = parseInt(h.slice(2, 4), 16), b = parseInt(h.slice(4, 6), 16);
      const mx = Math.max(r, g, b), mn = Math.min(r, g, b);
      if (mx - mn < 28) {
        const light = (mx + mn) / 2;
        return light < 60 ? "black" : light > 215 ? "white" : "gray";
      }
      let best = null, bd = Infinity;
      for (const [name, [pr, pg, pb]] of Object.entries(D.PALETTE)) {
        const d = (pr - r) ** 2 + (pg - g) ** 2 + (pb - b) ** 2;
        if (d < bd) { bd = d; best = name; }
      }
      return best;
    }
    function normalizeColor(v) {
      if (v === null || v === undefined) return null;
      const s = String(v).trim().toLowerCase();
      if (!s) return null;
      if (HEX.test(s)) return hexToName(s);
      for (const [keys, name] of D.ALIASES) if (keys.some((k) => s.includes(k))) return name;
      return null;
    }
    const isDenim = (p) => String(p.material || "").toLowerCase() === "denim"
      || ["청바지", "데님재킷"].includes(String(p.subcategory || "")) || String(p.name || "").includes("데님");
    function colorScore(pieces) {
      const acc = new Set();
      for (const p of pieces) {
        const c = normalizeColor(p.color);
        if (c === null || NEUTRALS.has(c) || isDenim(p)) continue;
        acc.add(c);
      }
      const n = acc.size, a = [...acc];
      if (n === 0) return [0.95, "무채색 위주라 무난한 색 조합입니다."];
      if (n === 1) return [1.0, `무채색에 포인트 컬러(${COLOR_KO[a[0]] || a[0]}) 하나를 더한 조합입니다.`];
      if (n === 2) {
        const set = a.slice().sort().join("|");
        if (set === "blue|skyblue" || set === "pink|red") return [0.9, "같은 계열 색을 겹친 톤온톤 조합입니다."];
        return [0.75, "유채색이 2개라 색이 다소 강할 수 있어요."];
      }
      return [0.5, "유채색이 3개 이상이라 색 조합이 산만할 수 있어요."];
    }

    // ---------------------------------------------------------------- 기온 / 보온
    function effectiveTemperature(temperature, apparent, rain, purpose, cold, heat) {
      const base = apparent === null || apparent === undefined ? temperature : apparent;
      const adj = [];
      cold = +cold || 0; heat = +heat || 0;
      if (cold) adj.push(["추위를 타는 체질", -D.THERMAL_SHIFT * cold]);
      if (heat) adj.push(["더위를 타는 체질", D.THERMAL_SHIFT * heat]);
      if (purpose === "운동") adj.push(["운동 중 체온 상승", D.EXERCISE_SHIFT]);
      if (rain && temperature < 20) adj.push(["비", D.RAIN_SHIFT]);
      return [base + adj.reduce((s, x) => s + x[1], 0), adj];
    }
    function targetWarmth(eff) {
      const pts = D.TARGET_CURVE;
      if (eff <= pts[0][0]) return pts[0][1];
      if (eff >= pts[pts.length - 1][0]) return pts[pts.length - 1][1];
      for (let i = 0; i < pts.length - 1; i++) {
        const [t0, w0] = pts[i], [t1, w1] = pts[i + 1];
        if (t0 <= eff && eff <= t1) return w0 + (w1 - w0) * (eff - t0) / (t1 - t0);
      }
      return pts[pts.length - 1][1];
    }
    function level(item) {
      const v = parseFloat(item.warmth);
      return Number.isFinite(v) ? Math.min(5, Math.max(1, Math.round(v))) : 2;
    }
    const sub = (item) => canon(item.subcategory);
    function roles(pieces) {
      const tops = pieces.filter((p) => p.category === "상의");
      let inner = null;
      if (tops.length === 2) inner = LAYER.has(sub(tops[1]) + "|" + sub(tops[0])) ? tops[1] : tops[0];
      return pieces.map((p) => (p === inner ? "이너" : p.category));
    }
    function outfitWarmth(pieces) {
      const rs = roles(pieces);
      return pieces.reduce((s, p, i) => s + (D.WARMTH_CONTRIB[rs[i]] || [0, 0, 0, 0, 0])[level(p) - 1], 0);
    }

    // ---------------------------------------------------------------- 필터
    const purposeOk = (x, purpose) => { const r = ruleFor(x.subcategory); return !r || !r.blocked_purposes.includes(purpose); };
    const tempOk = (x, eff) => { const r = ruleFor(x.subcategory); return !r || (r.min_temp <= eff && eff <= r.max_temp); };
    function josa(word, withFinal = "이", without = "가") {
      const ch = word.charCodeAt(word.length - 1);
      if (ch >= 0xac00 && ch <= 0xd7a3) return (ch - 0xac00) % 28 ? withFinal : without;
      return withFinal;
    }
    function buildPool(items, cat, purpose, eff, rain, warnings) {
      const all = items.filter((x) => x.category === cat);
      if (!all.length) return [];
      const label = CAT_LABEL[cat];
      const checks = [["purpose", (x) => purposeOk(x, purpose)], ["temp", (x) => tempOk(x, eff)]];
      if (cat === "신발" && rain) checks.push(["rain", (x) => !!x.rain_ok]);
      const active = new Set(checks.map((c) => c[0]));
      const run = () => all.filter((x) => checks.every(([n, fn]) => !active.has(n) || fn(x)));
      let pool = run();
      for (const drop of ["rain", "temp", "purpose"]) {
        if (pool.length || !active.has(drop)) continue;
        active.delete(drop);
        pool = run();
        if (pool.length && drop === "rain") {
          const names = pool.filter((x) => !x.rain_ok).map((x) => x.name).join(", ");
          warnings.push(`조건에 맞는 신발 중 비에 강한 것이 없어 비에 약한 신발(${names})도 포함했어요. 젖지 않게 주의하세요.`);
        } else if (pool.length && drop === "temp") {
          warnings.push(`체감 ${fmt0(eff)}℃에 맞는 ${label}${josa(label)} 옷장에 없어 기온 기준을 완화했어요.`);
        } else if (pool.length && drop === "purpose") {
          warnings.push(`'${purpose}'에 맞는 ${label}${josa(label)} 옷장에 없어 목적 기준을 완화했어요.`);
        }
      }
      return pool;
    }
    function pairsOk(pieces) {
      const subs = pieces.map(sub).filter(Boolean);
      for (let i = 0; i < subs.length; i++)
        for (let j = i + 1; j < subs.length; j++)
          if (INCOMP.has([subs[i], subs[j]].sort().join("|"))) return false;
      return true;
    }
    const outfitKey = (pieces) => pieces.map((p) => +p.id).sort((a, b) => a - b).join(",");
    function topUnits(tops, eff) {
      const units = tops.map((t) => [t]);
      if (eff < D.LAYER_MAX_TEMP)
        for (const a of tops) for (const b of tops) if (LAYER.has(sub(a) + "|" + sub(b))) units.push([a, b]);
      return units;
    }
    function prune(pools, eff) {
      const pre = (x) => {
        const r = ruleFor(x.subcategory);
        if (!r) return 0;
        return -Math.abs(eff - (Math.max(r.min_temp, -15) + Math.min(r.max_temp, 35)) / 2);
      };
      const size = () => (topUnits(pools["상의"], eff).length * pools["하의"].length + pools["원피스"].length)
        * Math.max(1, pools["신발"].length) * (pools["아우터"].length + 1);
      while (size() > D.MAX_COMBOS) {
        const cat = ["상의", "하의", "원피스", "아우터", "신발"].reduce((a, c) => (pools[c].length > pools[a].length ? c : a));
        if (pools[cat].length <= 3) break;
        pools[cat] = pools[cat].slice().sort((a, b) => pre(b) - pre(a)).slice(0, -1);
      }
      return pools;
    }

    // ---------------------------------------------------------------- 점수
    function weighted(pieces, rw, fn) {
      const rs = roles(pieces);
      let tw = 0, s = 0;
      pieces.forEach((p, i) => { const w = rw[rs[i]] ?? 0.2; tw += w; s += w * fn(p); });
      return s / tw;
    }
    function score(pieces, ctx) {
      const { eff, target, rain } = ctx;
      const actual = outfitWarmth(pieces);
      const weatherScore = Math.max(0, 1 - Math.abs(actual - target) / D.WARMTH_TOLERANCE);
      const hasOuter = pieces.some((p) => p.category === "아우터");
      let rainScore = 1;
      if (rain) {
        const rw = { ...D.RAIN_ROLE_WEIGHT };
        if (!hasOuter) rw["상의"] = 0.30;
        rainScore = weighted(pieces, rw, (p) => (p.rain_ok ? 1 : 0.2));
      }
      const [cScore, cReason] = colorScore(pieces);
      const W = D.WEIGHTS;
      const final = W.weather * weatherScore + W.rain * rainScore + W.color * cScore;

      const reasons = [];
      const rs = roles(pieces);
      if (rs.includes("이너")) {
        const inner = pieces[rs.indexOf("이너")], over = pieces[rs.indexOf("상의")];
        reasons.push(`${inner.name} 위에 ${over.name}${josa(over.name, "을", "를")} 겹쳐 입는 레이어드 코디예요.`);
      }
      const d = actual - target;
      if (Math.abs(d) <= 0.35) reasons.push(`체감 ${fmt0(eff)}℃ 기준으로 두께가 잘 맞습니다.`);
      else if (d < 0) reasons.push(`체감 ${fmt0(eff)}℃ 기준으로 조금 서늘할 수 있어요. 안에 한 겹 더 입는 걸 권장해요.`);
      else reasons.push(`체감 ${fmt0(eff)}℃ 기준으로 조금 더울 수 있어요.` + (hasOuter ? " 겉옷을 벗어 조절하세요." : ""));
      if (rain) {
        const weak = pieces.filter((p) => (p.category === "신발" || p.category === "아우터") && !p.rain_ok).map((p) => p.name);
        if (weak.length) reasons.push(`비에 약한 ${weak.join(", ")}${josa(weak[weak.length - 1])} 포함돼 있어요. 젖지 않게 주의하세요.`);
        else reasons.push(hasOuter ? "신발·겉옷이 비에 강한 조합입니다." : "신발이 비에 강한 조합입니다. 우산을 챙기세요.");
      }
      reasons.push(cReason);
      return {
        score: round4(Math.max(0, Math.min(1, final))), reasons,
        components: { weather_score: round4(weatherScore), rain_score: round4(rainScore), color_score: round4(cScore),
          target_warmth: round4(target), actual_warmth: round4(actual), effective_temp: Math.round(eff * 10) / 10 },
      };
    }

    // ---------------------------------------------------------------- 다양화
    const core = (pieces) => new Set(pieces.filter((p) => ["상의", "하의", "원피스"].includes(p.category)).map((p) => +p.id));
    const nonShoe = (pieces) => new Set(pieces.filter((p) => p.category !== "신발").map((p) => +p.id));
    const inter = (a, b) => { let n = 0; for (const x of a) if (b.has(x)) n++; return n; };
    const same = (a, b) => a.size === b.size && inter(a, b) === a.size;
    function pickDiverse(scored, k) {
      const picked = [];
      const accept = (cond) => {
        for (const r of scored) {
          if (picked.length >= k) return;
          if (picked.includes(r)) continue;
          if (picked.every((p) => cond(r, p))) picked.push(r);
        }
      };
      accept((a, b) => inter(core(a.pieces), core(b.pieces)) === 0);
      accept((a, b) => !same(core(a.pieces), core(b.pieces)) && inter(nonShoe(a.pieces), nonShoe(b.pieces)) <= 1);
      accept((a, b) => !same(core(a.pieces), core(b.pieces)));
      accept((a, b) => !same(nonShoe(a.pieces), nonShoe(b.pieces)));
      return picked.sort((a, b) => b.score - a.score);
    }

    function seededRandom(seed) {
      let h = 1779033703 ^ String(seed).length;
      for (const ch of String(seed)) { h = Math.imul(h ^ ch.charCodeAt(0), 3432918353); h = (h << 13) | (h >>> 19); }
      return () => {
        h = Math.imul(h ^ (h >>> 16), 2246822507); h = Math.imul(h ^ (h >>> 13), 3266489909);
        return ((h ^= h >>> 16) >>> 0) / 4294967296;
      };
    }

    // ---------------------------------------------------------------- 메인
    function recommend(items, { temperature, apparent = null, rain = false, purpose = "등교", cold = 0, heat = 0,
      topK = 3, excludeKeys = [], seed = 0, all = false } = {}) {
      const [eff, adjustments] = effectiveTemperature(temperature, apparent, rain, purpose, cold, heat);
      const ctx = { eff, adjustments, target: targetWarmth(eff), rain: !!rain, purpose,
        baseTemp: apparent === null || apparent === undefined ? temperature : apparent };
      const warnings = [];
      const usable = items.filter((x) => x.category in D.WARMTH_CONTRIB);
      const pools = { 상의: [], 하의: [], 원피스: [], 아우터: [], 신발: [] };
      pools["신발"] = buildPool(usable, "신발", purpose, eff, rain, warnings);
      const strict = {};
      for (const c of ["상의", "하의", "원피스"])
        strict[c] = usable.filter((x) => x.category === c && purposeOk(x, purpose) && tempOk(x, eff));
      if ((strict["상의"].length && strict["하의"].length) || strict["원피스"].length) {
        const sepOk = !!(strict["상의"].length && strict["하의"].length);
        pools["상의"] = sepOk ? strict["상의"] : [];
        pools["하의"] = sepOk ? strict["하의"] : [];
        pools["원피스"] = strict["원피스"];
      } else {
        const tmp = [];
        const top = buildPool(usable, "상의", purpose, eff, rain, tmp);
        const bottom = buildPool(usable, "하의", purpose, eff, rain, tmp);
        if (top.length && bottom.length) { pools["상의"] = top; pools["하의"] = bottom; warnings.push(...tmp); }
        else pools["원피스"] = buildPool(usable, "원피스", purpose, eff, rain, warnings);
      }
      let outerRequired = eff <= D.OUTER_REQUIRED_AT;
      let strictOuter = usable.filter((x) => x.category === "아우터" && purposeOk(x, purpose) && tempOk(x, eff));
      if (outerRequired && !strictOuter.length) {
        strictOuter = buildPool(usable, "아우터", purpose, eff, rain, warnings);
        if (!strictOuter.length) {
          warnings.push(`체감 ${fmt0(eff)}℃라 겉옷이 필요하지만 옷장에 맞는 겉옷이 없어요.`);
          outerRequired = false;
        }
      }
      pools["아우터"] = strictOuter;
      if (eff <= 5 && !strictOuter.some((x) => level(x) >= 4))
        warnings.push(`체감 ${fmt0(eff)}℃인데 두꺼운 겉옷(코트·패딩)이 옷장에 없어 보온이 부족할 수 있어요.`);
      if (!pools["신발"].length || !((pools["상의"].length && pools["하의"].length) || pools["원피스"].length))
        return { results: [], warnings: [...warnings, "추천에 필요한 옷 조합(상의+하의+신발 또는 원피스+신발)이 없습니다."], context: ctx };

      prune(pools, eff);
      const outers = outerRequired ? pools["아우터"] : [null, ...pools["아우터"]];
      const candidates = [];
      for (const top of topUnits(pools["상의"], eff))
        for (const bottom of pools["하의"]) for (const outer of outers) for (const shoe of pools["신발"])
          candidates.push([...top, bottom, ...(outer ? [outer] : []), shoe]);
      for (const dress of pools["원피스"]) for (const outer of outers) for (const shoe of pools["신발"])
        candidates.push([dress, ...(outer ? [outer] : []), shoe]);

      let paired = candidates.filter(pairsOk);
      if (!paired.length) { warnings.push("옷장 구성상 평소엔 피하는 조합도 포함했어요."); paired = candidates; }
      const ex = new Set(excludeKeys);
      let kept = paired.filter((c) => !ex.has(outfitKey(c)));
      if (!kept.length) { warnings.push("'별로'를 누른 조합을 빼면 추천할 코디가 없어 다시 포함했어요."); kept = paired; }

      const rnd = seededRandom(seed);
      for (let i = kept.length - 1; i > 0; i--) { const j = Math.floor(rnd() * (i + 1)); [kept[i], kept[j]] = [kept[j], kept[i]]; }
      const scored = kept.map((pieces) => ({ pieces, ...score(pieces, ctx) }));
      scored.sort((a, b) => b.score - a.score);
      return { results: all ? scored : pickDiverse(scored, topK), warnings, context: ctx };
    }

    function breakdown(c) {
      const W = D.WEIGHTS;
      return [
        { label: "날씨 적합도", score: c.weather_score, weight: W.weather },
        { label: "비/강수 적합도", score: c.rain_score, weight: W.rain },
        { label: "색상 조합", score: c.color_score, weight: W.color },
      ];
    }

    return { recommend, breakdown, outfitKey, normalizeColor, hexToName, ruleFor, roles, josa, COLOR_KO, D };
  }

  const api = { create, COLOR_KO };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Closet = api;
})(typeof self !== "undefined" ? self : this);
