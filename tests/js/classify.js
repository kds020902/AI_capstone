// 웹 체험판 분류기(web/src/closetnet.js + web/model)로 입력 텐서의 로짓을 계산해 출력한다.
// 사용: node tests/js/classify.js <입력.f32> <장수>   입력 = (장수, 3, 224, 224) float32 리틀엔디언
const fs = require("fs"), path = require("path");
const web = path.join(__dirname, "../../web");
const net = require(path.join(web, "src/closetnet.js"));
const spec = JSON.parse(fs.readFileSync(path.join(web, "model/closet_effnet_b0.json"), "utf8"));
const buf = Buffer.from(fs.readFileSync(path.join(web, "model/closet_effnet_b0.f16.b64.txt"), "utf8").trim(), "base64");
const model = net.load(spec, buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength));
const raw = fs.readFileSync(process.argv[2]), n = +process.argv[3], size = 3 * 224 * 224;
const X = new Float32Array(raw.buffer.slice(raw.byteOffset, raw.byteOffset + raw.byteLength));
const logits = [];
for (let i = 0; i < n; i++) logits.push(Array.from(net.forward(model, X.slice(i * size, (i + 1) * size))));
process.stdout.write(JSON.stringify({ classes: spec.classes, logits }));
