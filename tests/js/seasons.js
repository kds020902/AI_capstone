// 웹 체험판(web/src/closet.js)의 계절 판단을 출력해 파이썬(core/taxonomy.py)과 비교한다.
// 사용: node tests/js/seasons.js <입력.json>   입력 = {data: closet_data(), items: [{subcategory, seasons}]}
const fs = require("fs"), path = require("path");
const { data, items } = JSON.parse(fs.readFileSync(process.argv[2], "utf8"));
const C = require(path.join(__dirname, "../../web/src/closet.js")).create(data);
process.stdout.write(JSON.stringify({
  items: items.map((x) => { const s = C.itemSeasons(x); return { seasons: s, label: C.seasonLabel(s) }; }),
  months: Array.from({ length: 12 }, (_, i) => C.currentSeason(i + 1)),
}));
