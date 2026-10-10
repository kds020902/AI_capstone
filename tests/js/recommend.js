// 웹 체험판 추천 엔진(web/src/closet.js)으로 같은 조건을 계산해 파이썬 결과와 비교할 수 있게 출력한다.
// 사용: node tests/js/recommend.js <입력.json>   입력 = {data: closet_data(), cases: [{items, args}]}
const fs = require("fs"), path = require("path");
const { data, cases } = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const C = require(path.join(__dirname, "../../web/src/closet.js")).create(data);
const out = cases.map(({ items, args }) => {
  const r = C.recommend(items, { ...args, all: true });
  return {
    entries: r.results.map((x) => x.pieces.map((p) => p.id).sort((a, b) => a - b).join(",") + "=" + x.score.toFixed(4)
      + "|" + (x.carry ? x.carry.id : "") + "|" + (x.reasons.find((t) => t.includes("쯤 체감")) || "")).sort(),
    eff: r.context.eff,
    warnings: r.warnings,
  };
});
process.stdout.write(JSON.stringify(out));
