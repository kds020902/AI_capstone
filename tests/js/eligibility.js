// 웹 체험판의 후보 자격·젖음 부담·온도 계산(web/src/closet.js)을 출력해 파이썬(core/eligibility.py)과 비교한다.
// 사용: node tests/js/eligibility.js <입력.json>   입력 = {data: closet_data(), items, conds: [[t, rain, purpose, cold, heat]]}
const fs = require("fs"), path = require("path");
const { data, items, conds } = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const C = require(path.join(__dirname, "../../web/src/closet.js")).create(data);
const temps = [], flags = [];
for (const [t, rain, purpose, cold, heat] of conds) {
  const ctx = C.temperatureContext(t, t + 0.5, rain, purpose, cold, heat);
  temps.push([ctx.t_env, ctx.t_filter, ctx.t_pref]);
  flags.push(items.map((x) => C.itemFlags(x, ctx)));
}
process.stdout.write(JSON.stringify({ wet: items.map(C.wetBurden), temps, flags }));
