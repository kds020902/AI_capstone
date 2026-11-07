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
    const fmt0 = (v) => {  // 파이썬 f"{v:.0f}" 와 같게 (.5는 짝수 쪽으로)
      const r = Math.round(v);
      return (Math.abs(v % 1) === 0.5 && r % 2 !== 0 ? r - 1 : r).toFixed(0);
    };
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

    // ---------------------------------------------------------------- 보온 (core/recommender.py 와 같음)
    function targetWarmth(temp) {
      const pts = D.TARGET_CURVE;
      if (temp <= pts[0][0]) return pts[0][1];
      if (temp >= pts[pts.length - 1][0]) return pts[pts.length - 1][1];
      for (let i = 0; i < pts.length - 1; i++) {
        const [t0, w0] = pts[i], [t1, w1] = pts[i + 1];
        if (t0 <= temp && temp <= t1) return w0 + (w1 - w0) * (temp - t0) / (t1 - t0);
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
    function josa(word, withFinal = "이", without = "가") {
      const ch = word.charCodeAt(word.length - 1);
      if (ch >= 0xac00 && ch <= 0xd7a3) return (ch - 0xac00) % 28 ? withFinal : without;
      return withFinal;
    }

    // ---------------------------------------------------------------- 온도·후보 자격·젖음 (core/eligibility.py 와 같음)
    function temperatureContext(temperature, apparent, rain, purpose, cold = 0, heat = 0) {
      const tEnv = apparent === null || apparent === undefined ? temperature : apparent;
      const tFilter = tEnv + (purpose === "운동" ? D.EXERCISE_SHIFT : 0);
      cold = +cold || 0; heat = +heat || 0;
      const chill = rain ? Math.min(1, Math.max(0, (D.RAIN_CHILL_WARM - tEnv) / D.RAIN_CHILL_SPAN)) : 0;
      const adj = [];
      if (cold) adj.push(["추위를 타는 체질", -D.THERMAL_SHIFT * cold]);
      if (heat) adj.push(["더위를 타는 체질", D.THERMAL_SHIFT * heat]);
      if (chill) adj.push(["쌀쌀한 비", -D.RAIN_CHILL * chill]);
      return { t_env: tEnv, t_filter: tFilter, t_pref: adj.reduce((s, x) => s + x[1], tFilter),
        rain: !!rain, purpose, adjustments: adj };
    }
    const tempFits = (r, t) => r.min_temp <= t && t <= r.max_temp;
    function tempOk(x, ctx) {
      const r = ruleFor(x.subcategory);
      if (!r) return true;
      return tempFits(r, ctx.t_filter) || (ctx.rain && r.weather_shell && tempFits(r, ctx.t_env));
    }
    function itemFlags(x, ctx) {  // null = 이 목적엔 절대 안 됨, 아니면 [목적 완화 필요, 기온 완화 필요]
      const r = ruleFor(x.subcategory);
      if (!r) return [false, false];
      if (r.blocked_purposes.includes(ctx.purpose)) return null;
      return [r.fallback_purposes.includes(ctx.purpose), !tempOk(x, ctx)];
    }
    function allowed(flags, tier) {
      if (flags === null) return false;
      const [pr, tr] = D.TIERS[tier];
      return (pr || !flags[0]) && (tr || !flags[1]);
    }
    const wearable = (x, ctx) => allowed(itemFlags(x, ctx), 0);
    function wetBurden(x) {
      const rainOk = !!x.rain_ok, r = ruleFor(x.subcategory);
      if (!r) return rainOk ? 0 : 1;
      let w = r.wet_burden;
      if (rainOk !== r.default_rain_ok) {
        if (rainOk && w > 0.5) w = 0.5;
        else if (!rainOk && w <= 0.5) w = 1;
      }
      return w;
    }

    // ---------------------------------------------------------------- 후보 자격 단계
    const CATS = ["상의", "하의", "원피스", "아우터", "신발"];
    function poolsAt(usable, flags, tier) {
      const pools = { 상의: [], 하의: [], 원피스: [], 아우터: [], 신발: [] };
      for (const x of usable) if (allowed(flags.get(x), tier)) pools[x.category].push(x);
      return pools;
    }
    const feasible = (p, outerRequired) => !!(p["신발"].length && ((p["상의"].length && p["하의"].length) || p["원피스"].length)
      && (p["아우터"].length || !outerRequired));
    function chooseTier(usable, flags, outerRequired) {
      for (let tier = 0; tier < D.TIERS.length; tier++) {
        const pools = poolsAt(usable, flags, tier);
        if (feasible(pools, outerRequired)) return [tier, pools];
      }
      return [null, null];
    }
    function relaxWarnings(usable, flags, tier, pools, outerRequired, ctx) {
      const p0 = poolsAt(usable, flags, 0);
      const need = ["신발", ...(outerRequired ? ["아우터"] : [])];
      if (pools["상의"].length && pools["하의"].length) need.push("상의", "하의");
      if (pools["원피스"].length) need.push("원피스");
      const p1 = tier === 3 ? poolsAt(usable, flags, 1) : null, p2 = tier === 3 ? poolsAt(usable, flags, 2) : null;
      const purpose = ctx.purpose, t = fmt0(ctx.t_filter), out = [];
      for (const cat of CATS) {
        if (!need.includes(cat) || p0[cat].length || !pools[cat].length) continue;
        const label = CAT_LABEL[cat];
        const kind = tier < 3 ? tier : p1[cat].length ? 1 : p2[cat].length ? 2 : 3;
        if (kind === 1) out.push(`'${purpose}'에 맞는 ${label}${josa(label)} 옷장에 없어 목적 기준을 완화했어요.`);
        else if (kind === 2) out.push(`추천 기준 ${t}℃에 맞는 ${label}${josa(label)} 옷장에 없어 기온 기준을 완화했어요.`);
        else out.push(`'${purpose}'·추천 기준 ${t}℃에 맞는 ${label}${josa(label)} 옷장에 없어 목적·기온 기준을 완화했어요.`);
      }
      return out;
    }
    function missing(usable, flags, ctx) {
      const p = poolsAt(usable, flags, D.TIERS.length - 1), purpose = ctx.purpose;
      const blocked = (cats) => {
        const names = usable.filter((x) => cats.includes(x.category) && flags.get(x) === null).slice(0, 3).map((x) => x.name);
        return names.length ? ` (${names.join(", ")}${josa(names[names.length - 1], "은", "는")} '${purpose}'에 추천하지 않아요.)` : "";
      };
      const out = [];
      if (!p["신발"].length) out.push(`'${purpose}'에 신을 수 있는 신발이 옷장에 없어요.${blocked(["신발"])}`);
      if (!((p["상의"].length && p["하의"].length) || p["원피스"].length))
        out.push(`'${purpose}'에 입을 수 있는 상의·하의나 원피스가 옷장에 없어요.${blocked(["상의", "하의", "원피스"])}`);
      return out;
    }
    function pairsOk(pieces) {
      const subs = pieces.map(sub).filter(Boolean);
      for (let i = 0; i < subs.length; i++)
        for (let j = i + 1; j < subs.length; j++)
          if (INCOMP.has([subs[i], subs[j]].sort().join("|"))) return false;
      return true;
    }
    const outfitKey = (pieces) => pieces.map((p) => +p.id).sort((a, b) => a - b).join(",");
    function topUnits(tops, temp) {
      const units = tops.map((t) => [t]);
      if (temp < D.LAYER_MAX_TEMP)
        for (const a of tops) for (const b of tops) if (LAYER.has(sub(a) + "|" + sub(b))) units.push([a, b]);
      return units;
    }
    function prune(pools, temp) {
      const pre = (x) => {
        const r = ruleFor(x.subcategory);
        if (!r) return 0;
        return -Math.abs(temp - (Math.max(r.min_temp, -15) + Math.min(r.max_temp, 35)) / 2);
      };
      const size = () => (topUnits(pools["상의"], temp).length * pools["하의"].length + pools["원피스"].length)
        * Math.max(1, pools["신발"].length) * (pools["아우터"].length + 1);
      while (size() > D.MAX_COMBOS) {
        const cat = CATS.reduce((a, c) => (pools[c].length > pools[a].length ? c : a));
        if (pools[cat].length <= 3) break;
        pools[cat] = pools[cat].slice().sort((a, b) => pre(b) - pre(a)).slice(0, -1);
      }
      return pools;
    }

    // ---------------------------------------------------------------- 점수
    function exposed(pieces) {  // 비를 맞는 옷: [신발, 하체, 가장 바깥 상체]
      const by = {};
      roles(pieces).forEach((r, i) => { by[r] = pieces[i]; });
      return [by["신발"], by["하의"] || by["원피스"], by["아우터"] || by["상의"] || by["원피스"]];
    }
    function seedRank(seed, pieces) {  // FNV-1a 32비트 (core/recommender.py seed_rank 와 같은 값)
      const text = `${seed}|` + pieces.map((p) => +p.id).sort((a, b) => a - b).join(",");
      let h = 0x811c9dc5;
      for (const b of new TextEncoder().encode(text)) h = Math.imul(h ^ b, 0x01000193) >>> 0;
      return h;
    }
    function score(pieces, ctx) {
      const target = ctx.target, actual = outfitWarmth(pieces);
      const weather = Math.max(0, 1 - Math.abs(actual - target) / D.WARMTH_TOLERANCE);
      const hasOuter = pieces.some((p) => p.category === "아우터");
      let wet = null, total = weather, shoe, lower, upper;
      if (ctx.rain) {
        [shoe, lower, upper] = exposed(pieces);
        wet = D.WET_PARTS["신발"] * wetBurden(shoe) + D.WET_PARTS["하체"] * wetBurden(lower)
          + D.WET_PARTS["상체"] * wetBurden(upper);
        total = D.RAIN_MIX.weather * weather + D.RAIN_MIX.wet * (1 - wet);
      }
      const scoreI = Math.floor(Math.min(1, Math.max(0, total)) * D.SCORE_SCALE + 0.5 + 1e-9);
      const [cScore, cReason] = colorScore(pieces);

      const reasons = [];
      const rs = roles(pieces);
      if (rs.includes("이너")) {
        const inner = pieces[rs.indexOf("이너")], over = pieces[rs.indexOf("상의")];
        reasons.push(`${inner.name} 위에 ${over.name}${josa(over.name, "을", "를")} 겹쳐 입는 레이어드 코디예요.`);
      }
      const where = ctx.purpose === "운동" ? `운동 기준 ${fmt0(ctx.t_filter)}℃` : `체감 ${fmt0(ctx.t_env)}℃`;
      const d = actual - target;
      if (Math.abs(d) <= D.FIT_GAP) reasons.push(`${where}에 두께가 잘 맞습니다.`);
      else if (d < 0) reasons.push(`${where}에는 조금 서늘할 수 있어요. 안에 한 겹 더 입는 걸 권장해요.`);
      else reasons.push(`${where}에는 조금 더울 수 있어요.` + (hasOuter ? " 겉옷을 벗어 조절하세요." : ""));
      if (ctx.rain) {
        const weak = [];
        for (const p of [shoe, lower, upper]) if (wetBurden(p) >= 1 && !weak.includes(p.name)) weak.push(p.name);
        if (weak.length) reasons.push(`비에 약한 ${weak.join(", ")}${josa(weak[weak.length - 1])} 포함돼 있어요. 젖지 않게 주의하세요.`);
        else reasons.push("비를 맞는 신발·옷이 비에 강한 조합입니다." + (hasOuter ? "" : " 우산을 챙기세요."));
      }
      reasons.push(cReason);
      const r1 = (v) => Math.round(v * 10) / 10;
      return {
        result: { score: scoreI / D.SCORE_SCALE, reasons,
          components: { weather_score: round4(weather), rain_score: wet === null ? null : round4(1 - wet),
            color_score: round4(cScore), target_warmth: round4(target), actual_warmth: round4(actual),
            env_temp: r1(ctx.t_env), filter_temp: r1(ctx.t_filter), pref_temp: r1(ctx.t_pref) } },
        key: [Math.floor(scoreI / D.BUCKET), cScore, scoreI],
      };
    }

    const cmpIds = (a, b) => {
      for (let i = 0; i < Math.min(a.length, b.length); i++) if (a[i] !== b[i]) return a[i] - b[i];
      return a.length - b.length;
    };

    // ---------------------------------------------------------------- 일교차 (core/recommender.py _later_advice 와 같음)
    const laterPurposeOk = (o, lt) => { const f = itemFlags(o, lt); return f !== null && !f[0]; };
    function laterAdvice(pieces, ctx, outers) {
      const lt = ctx.later;
      if (!lt || lt.t_env > ctx.t_env - D.LATER_DROP) return null;
      const hasOuter = pieces.some((p) => p.category === "아우터");
      const short = lt.target - outfitWarmth(pieces) > D.FIT_GAP;
      if (!(short || (!hasOuter && lt.t_filter <= D.OUTER_REQUIRED_AT))) return null;
      const when = `${lt.label}쯤 체감 ${fmt0(lt.t_env)}℃까지 내려가요.`;
      const r6 = (v) => Math.round(v * 1e6) / 1e6;
      const fit = (base, o) => [r6(Math.abs(outfitWarmth([...base, o]) - lt.target)), -colorScore([...base, o])[0], +o.id];
      const less = (a, b) => a[0] !== b[0] ? a[0] < b[0] : a[1] !== b[1] ? a[1] < b[1] : a[2] < b[2];
      const best = (list, base) => list.reduce((m, o) => (m === null || less(fit(base, o), fit(base, m)) ? o : m), null);
      if (hasOuter) {
        const cur = pieces.find((p) => p.category === "아우터"), rest = pieces.filter((p) => p !== cur);
        const gap = r6(Math.abs(outfitWarmth(pieces) - lt.target));
        const better = outers.filter((o) => +o.id !== +cur.id && level(o) > level(cur) && laterPurposeOk(o, lt)
          && tempOk(o, lt) && pairsOk([...rest, o]) && fit(rest, o)[0] < gap);
        if (!better.length) return [null, `${when} ${cur.name}만으로는 조금 추울 수 있으니 안에 한 겹 더 챙기세요.`];
        const b = best(better, rest);
        return [b, `${when} ${cur.name}보다 ${b.name}${josa(b.name)} 더 따뜻해요. 늦게까지 밖에 있으면 ${b.name}${josa(b.name, "을", "를")} 입고 나가세요.`];
      }
      const ok = outers.filter((o) => laterPurposeOk(o, lt) && pairsOk([...pieces, o]));
      const warm = ok.filter((o) => tempOk(o, lt)), cands = warm.length ? warm : ok;
      if (!cands.length) return [null, `${when} 챙길 만한 겉옷이 옷장에 없어요.`];
      const b = best(cands, pieces);
      return [b, `${when} ${b.name}${josa(b.name, "을", "를")} 챙기세요.`];
    }
    function attachLater(result, ctx, outers) {
      result.carry = null;
      if (!ctx.later) return;
      result.components.later_env_temp = Math.round(ctx.later.t_env * 10) / 10;
      const advice = laterAdvice(result.pieces, ctx, outers);
      if (!advice) return;
      result.carry = advice[0];
      const i = result.reasons.findIndex((r) => r.startsWith("체감 ") || r.startsWith("운동 기준 "));
      result.reasons.splice(i < 0 ? result.reasons.length : i + 1, 0, advice[1]);
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
      return picked.sort((a, b) => a.rank - b.rank);
    }

    // ---------------------------------------------------------------- 메인
    // later: 귀가 전까지 가장 추운 때 { temperature, apparent, label } — 주면 결과마다 carry(챙길 겉옷)와 안내 문구
    // mustInclude: 이 id의 옷이 들어간 코디만 (사기 전에 맞춰보기) — 결과에 comboKeys(옷 id 목록)도 준다
    // all: 다양화 없이 순위대로 모든 후보 (비교 시험용)
    function recommend(items, { temperature, apparent = null, rain = false, purpose = "등교", cold = 0, heat = 0,
      topK = 3, excludeKeys = [], seed = 0, all = false, later = null, mustInclude = null } = {}) {
      const ctx = temperatureContext(temperature, apparent, rain, purpose, cold, heat);
      ctx.target = targetWarmth(ctx.t_pref); ctx.later = null;
      if (later) {
        const lt = temperatureContext(later.temperature, later.apparent === undefined ? null : later.apparent, rain, purpose, cold, heat);
        lt.target = targetWarmth(lt.t_pref); lt.label = later.label || "저녁";
        ctx.later = lt;
      }
      const temp = ctx.t_filter, warnings = [];
      const usable = items.filter((x) => x.category in D.WARMTH_CONTRIB);
      const flags = new Map(usable.map((x) => [x, itemFlags(x, ctx)]));
      let outerRequired = temp <= D.OUTER_REQUIRED_AT;
      let [tier, pools] = chooseTier(usable, flags, outerRequired);
      if (tier === null && outerRequired) {
        warnings.push(`추천 기준 ${fmt0(temp)}℃라 겉옷이 필요하지만 옷장에 맞는 겉옷이 없어요. 겉옷을 등록하면 날씨에 맞게 추천할 수 있어요.`);
        outerRequired = false;
        [tier, pools] = chooseTier(usable, flags, outerRequired);
      }
      if (tier === null)
        return { results: [], warnings: [...warnings, D.NO_COMBO, ...missing(usable, flags, ctx)], context: ctx,
          nCombos: 0, ...(mustInclude === null ? {} : { comboKeys: [] }) };
      if (tier) warnings.push(...relaxWarnings(usable, flags, tier, pools, outerRequired, ctx));
      if (temp <= D.THICK_OUTER_AT && !pools["아우터"].some((x) => level(x) >= D.THICK_LEVEL))
        warnings.push(`추천 기준 ${fmt0(temp)}℃인데 두꺼운 겉옷(코트·패딩)이 옷장에 없어 보온이 부족할 수 있어요.`);

      if (mustInclude === null) prune(pools, temp);  // 특정 옷을 꼭 넣을 때는 덜어내지 않는다
      const outers = outerRequired ? pools["아우터"] : [null, ...pools["아우터"]];
      const candidates = [];
      for (const top of topUnits(pools["상의"], temp))
        for (const bottom of pools["하의"]) for (const outer of outers) for (const shoe of pools["신발"])
          candidates.push([...top, bottom, ...(outer ? [outer] : []), shoe]);
      for (const dress of pools["원피스"]) for (const outer of outers) for (const shoe of pools["신발"])
        candidates.push([dress, ...(outer ? [outer] : []), shoe]);

      const ex = new Set(excludeKeys);
      let kept;
      if (mustInclude !== null) {
        kept = candidates.filter((c) => c.some((p) => +p.id === +mustInclude) && pairsOk(c) && !ex.has(outfitKey(c)));
      } else {
        let paired = candidates.filter(pairsOk);
        if (!paired.length) { warnings.push("옷장 구성상 평소엔 피하는 조합도 포함했어요."); paired = candidates; }
        kept = paired.filter((c) => !ex.has(outfitKey(c)));
        if (!kept.length) { warnings.push("'별로'를 누른 조합을 빼면 추천할 코디가 없어 다시 포함했어요."); kept = paired; }
      }
      const nCombos = kept.length;
      const comboKeys = mustInclude === null ? null
        : kept.map((c) => c.map((p) => +p.id).sort((a, b) => a - b)).sort(cmpIds);

      const scored = kept.map((pieces) => {
        const { result, key } = score(pieces, ctx);
        const relaxed = pieces.filter((p) => { const f = flags.get(p); return f !== null && (f[0] || f[1]); }).length;
        return { r: { pieces, ...result }, key: [relaxed, -key[0], -key[1], -key[2], seedRank(seed, pieces)] };
      });
      scored.sort((a, b) => { for (let i = 0; i < 5; i++) if (a.key[i] !== b.key[i]) return a.key[i] - b.key[i]; return 0; });
      const ranked = scored.map(({ r }, rank) => ({ ...r, rank }));
      const results = (all ? ranked : pickDiverse(ranked, topK)).map(({ rank, ...r }) => r);
      const outerItems = usable.filter((x) => x.category === "아우터");
      for (const r of results) attachLater(r, ctx, outerItems);
      return { results, warnings, context: ctx, nCombos, ...(comboKeys ? { comboKeys } : {}) };
    }

    // ---------------------------------------------------------------- 계절 (core/taxonomy.py 와 같음)
    const SEASONS = D.SEASONS;
    function seasonsFor(subcategory) {
      const r = ruleFor(subcategory);
      if (!r) return SEASONS.slice();
      return SEASONS.filter((s) => D.SEASON_TEMPS[s].some((t) => tempFits(r, t)));
    }
    function manualSeasons(item) {  // 직접 고른 계절: 배열 또는 '여름|겨울'
      const v = item.seasons, list = Array.isArray(v) ? v : String(v || "").split("|");
      return SEASONS.filter((s) => list.includes(s));
    }
    const itemSeasons = (item) => { const m = manualSeasons(item); return m.length ? m : seasonsFor(item.subcategory); };
    const seasonLabel = (list) => (list.length === SEASONS.length ? "사계절" : list.join(", "));
    const currentSeason = (month) => ([6, 7, 8].includes(month) ? "여름" : [12, 1, 2].includes(month) ? "겨울" : "봄·가을");

    // ---------------------------------------------------------------- 사기 전에 맞춰보기 (core/shopping.py 와 같음)
    function similarItems(cand, items) {
      const s = canon(cand.subcategory), color = normalizeColor(cand.color);
      const same = items.filter((x) => canon(x.subcategory) === s);
      return { same_type: same, same_color: same.filter((x) => color && normalizeColor(x.color) === color) };
    }
    function candidateReport(candidate, items, { cold = 0, heat = 0 } = {}) {
      const cand = { ...candidate, id: D.CANDIDATE_ID }, seasons = itemSeasons(cand);
      const cells = {}, allKeys = new Set();
      for (const season of SEASONS) for (const purpose of D.PURPOSES) {
        const temps = D.SEASON_TEMPS[season];
        const cell = { n: 0, best: null, best_without: null, results: [], temp: temps[temps.length - 1] }, keys = new Set();
        for (const t of temps) {
          const args = { temperature: t, apparent: t, rain: false, purpose, cold, heat };
          const base = recommend(items, { ...args, topK: 1 });
          if (base.results.length) {
            const sc = base.results[0].score;
            cell.best_without = cell.best_without === null ? sc : Math.max(cell.best_without, sc);
          }
          if (!seasons.includes(season) || !wearable(cand, base.context)) continue;
          const out = recommend([...items, cand], { ...args, topK: 3, mustInclude: D.CANDIDATE_ID });
          for (const k of out.comboKeys) keys.add(k.join(","));
          if (out.results.length && (cell.best === null || out.results[0].score > cell.best)) {
            cell.best = out.results[0].score; cell.results = out.results; cell.temp = t;
          }
        }
        cell.n = keys.size; keys.forEach((k) => allKeys.add(k));
        cells[`${season}|${purpose}`] = cell;
      }
      const improves = [];
      for (const [key, c] of Object.entries(cells)) {
        const [season, purpose] = key.split("|");
        if (c.best !== null && (c.best_without === null || c.best - c.best_without >= D.IMPROVE_MIN))
          improves.push({ season, purpose, before: c.best_without, after: c.best });
      }
      improves.sort((a, b) => (b.after - (b.before || 0)) - (a.after - (a.before || 0)));
      const partners = [...new Set([...allKeys].flatMap((k) => k.split(",").map(Number)))]
        .filter((i) => i !== D.CANDIDATE_ID).sort((a, b) => a - b);
      const similar = similarItems(cand, items), total = allKeys.size;
      let verdict;
      if (similar.same_color.length) {
        verdict = ["dup", `거의 같은 옷이 이미 있어요 (${similar.same_color.slice(0, 3).map((x) => x.name).join(", ")}). 한 번 더 생각해 보세요.`];
      } else if (total === 0) {
        verdict = ["none", "지금 옷장으로는 함께 입을 코디가 없어요."];
      } else if (improves.length) {
        verdict = ["gap", `옷장에 없던 걸 채워 줘요. ${improves.slice(0, 3).map((x) => `${x.season} ${x.purpose}`).join(", ")}에서 코디 점수가 올라가요.`];
      } else {
        verdict = ["fit", `가진 옷 ${partners.length}벌과 어울려요.`];
      }
      return { seasons, cells, total, partners, improves, similar, verdict };
    }

    function breakdown(c) {  // 맑은 날 날씨 100%, 비 오는 날 날씨 70% + 젖음 30%. 색은 점수가 비슷할 때 순서만 (weight null)
      const rainy = c.rain_score !== null && c.rain_score !== undefined;
      return [
        { label: "날씨 적합도", score: c.weather_score, weight: rainy ? D.RAIN_MIX.weather : 1 },
        ...(rainy ? [{ label: "비에 젖어도 괜찮음", score: c.rain_score, weight: D.RAIN_MIX.wet }] : []),
        { label: "색상 조합", score: c.color_score, weight: null },
      ];
    }

    return { recommend, breakdown, outfitKey, normalizeColor, hexToName, ruleFor, roles, josa, COLOR_KO, D,
      temperatureContext, itemFlags, wetBurden,
      SEASONS, seasonsFor, manualSeasons, itemSeasons, seasonLabel, currentSeason, candidateReport };
  }

  const api = { create, COLOR_KO };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.Closet = api;
})(typeof self !== "undefined" ? self : this);
