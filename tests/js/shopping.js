// 웹 체험판의 '사기 전에 맞춰보기' 결과를 출력해 파이썬(core/shopping.py)과 비교한다.
// 사용: node tests/js/shopping.js <입력.json>   입력 = {data: closet_data(), cases: [{cand, items}]}
const fs = require("fs"), path = require("path");
const { data, cases } = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const C = require(path.join(__dirname, "../../web/src/closet.js")).create(data);
process.stdout.write(JSON.stringify(cases.map(({ cand, items }) => {
  const r = C.candidateReport(cand, items);
  const cells = {};
  for (const [k, c] of Object.entries(r.cells)) cells[k] = [c.n, c.best, c.best_without, c.temp, c.results.map((x) => x.score)];
  return { seasons: r.seasons, total: r.total, partners: r.partners, verdict: r.verdict, cells,
    improves: r.improves.map((x) => [x.season, x.purpose, x.before, x.after]),
    same_type: r.similar.same_type.map((x) => x.name), same_color: r.similar.same_color.map((x) => x.name) };
})));
