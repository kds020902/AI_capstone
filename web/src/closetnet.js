// AI 옷장 의류 분류기 — EfficientNet-B0 추론을 순수 자바스크립트로 (외부 라이브러리·WebAssembly 없음)
// 텐서는 CHW 순서 Float32Array. 배치정규화는 학습 후 conv 가중치에 합쳐 두었다.
(function (root) {
  "use strict";

  let F16 = null;
  function f16Table() {
    if (F16) return F16;
    F16 = new Float32Array(65536);
    for (let h = 0; h < 65536; h++) {
      const s = h & 0x8000 ? -1 : 1, e = (h >> 10) & 31, f = h & 1023;
      F16[h] = e === 0 ? s * f * 5.960464477539063e-8
        : e === 31 ? (f ? NaN : s * Infinity)
        : s * (1 + f / 1024) * Math.pow(2, e - 15);
    }
    return F16;
  }

  function load(spec, buffer) {
    const t = f16Table(), u = new Uint16Array(buffer), w = new Float32Array(u.length);
    for (let i = 0; i < u.length; i++) w[i] = t[u[i]];
    return { spec, w };
  }

  const view = (W, ref) => W.subarray(ref[0], ref[0] + ref[1]);
  const silu = (v) => v / (1 + Math.exp(-v));

  function conv(t, op, W) {
    const { cin, cout, k, stride, pad, groups } = op;
    const { c, h, w: wd } = t, x = t.d;
    const ho = Math.floor((h + 2 * pad - k) / stride) + 1, wo = Math.floor((wd + 2 * pad - k) / stride) + 1;
    const wt = view(W, op.w), b = view(W, op.b), out = new Float32Array(cout * ho * wo);
    const HWi = h * wd, HWo = ho * wo;
    if (k === 1 && stride === 1 && groups === 1) {            // 1×1: 행렬곱
      for (let co = 0; co < cout; co++) {
        const o = co * HWo;
        out.fill(b[co], o, o + HWo);
        for (let ci = 0; ci < cin; ci++) {
          const wv = wt[co * cin + ci], xi = ci * HWi;
          for (let p = 0; p < HWo; p++) out[o + p] += wv * x[xi + p];
        }
      }
    } else if (groups === cin && cin === cout) {               // 깊이별(depthwise)
      const kk = k * k;
      for (let ch = 0; ch < cout; ch++) {
        const xo = ch * HWi, wo0 = ch * kk, oo = ch * HWo, bv = b[ch];
        for (let oy = 0; oy < ho; oy++) {
          for (let ox = 0; ox < wo; ox++) {
            let s = bv;
            for (let ky = 0; ky < k; ky++) {
              const iy = oy * stride - pad + ky;
              if (iy < 0 || iy >= h) continue;
              const row = xo + iy * wd, wr = wo0 + ky * k;
              for (let kx = 0; kx < k; kx++) {
                const ix = ox * stride - pad + kx;
                if (ix >= 0 && ix < wd) s += wt[wr + kx] * x[row + ix];
              }
            }
            out[oo + oy * wo + ox] = s;
          }
        }
      }
    } else if (groups === 1) {                                  // 일반 conv (첫 층)
      const kk = k * k;
      for (let co = 0; co < cout; co++) {
        const oo = co * HWo, bv = b[co];
        for (let oy = 0; oy < ho; oy++) {
          for (let ox = 0; ox < wo; ox++) {
            let s = bv;
            for (let ci = 0; ci < cin; ci++) {
              const xo = ci * HWi, wr0 = (co * cin + ci) * kk;
              for (let ky = 0; ky < k; ky++) {
                const iy = oy * stride - pad + ky;
                if (iy < 0 || iy >= h) continue;
                for (let kx = 0; kx < k; kx++) {
                  const ix = ox * stride - pad + kx;
                  if (ix >= 0 && ix < wd) s += wt[wr0 + ky * k + kx] * x[xo + iy * wd + ix];
                }
              }
            }
            out[oo + oy * wo + ox] = s;
          }
        }
      }
    } else {
      throw new Error("지원하지 않는 conv 형태");
    }
    if (op.act === "silu") for (let i = 0; i < out.length; i++) out[i] = silu(out[i]);
    return { c: cout, h: ho, w: wo, d: out };
  }

  function se(t, op, W) {                                       // squeeze-and-excitation
    const { c, sq } = op, HW = t.h * t.w, x = t.d;
    const w1 = view(W, op.w1), b1 = view(W, op.b1), w2 = view(W, op.w2), b2 = view(W, op.b2);
    const pooled = new Float32Array(c);
    for (let i = 0; i < c; i++) {
      let s = 0;
      for (let p = 0, o = i * HW; p < HW; p++) s += x[o + p];
      pooled[i] = s / HW;
    }
    const s1 = new Float32Array(sq);
    for (let j = 0; j < sq; j++) {
      let s = b1[j];
      for (let i = 0; i < c; i++) s += w1[j * c + i] * pooled[i];
      s1[j] = silu(s);
    }
    for (let i = 0; i < c; i++) {
      let s = b2[i];
      for (let j = 0; j < sq; j++) s += w2[i * sq + j] * s1[j];
      const g = 1 / (1 + Math.exp(-s));
      for (let p = 0, o = i * HW; p < HW; p++) x[o + p] *= g;
    }
    return t;
  }

  function forward(model, input) {
    const { spec, w: W } = model;
    let t = { c: 3, h: spec.input[1], w: spec.input[2], d: input };
    for (const op of spec.ops) {
      if (op.op === "conv") t = conv(t, op, W);
      else if (op.op === "mbconv") {
        let y = t;
        for (const L of op.layers) y = L.op === "se" ? se(y, L, W) : conv(y, L, W);
        if (op.res) for (let i = 0; i < y.d.length; i++) y.d[i] += t.d[i];
        t = y;
      } else if (op.op === "linear") {
        const HW = t.h * t.w, v = new Float32Array(t.c);
        for (let i = 0; i < t.c; i++) {
          let s = 0;
          for (let p = 0, o = i * HW; p < HW; p++) s += t.d[o + p];
          v[i] = s / HW;
        }
        const wt = view(W, op.w), b = view(W, op.b), logits = new Float32Array(op.cout);
        for (let j = 0; j < op.cout; j++) {
          let s = b[j];
          for (let i = 0; i < op.cin; i++) s += wt[j * op.cin + i] * v[i];
          logits[j] = s;
        }
        return logits;
      }
    }
    throw new Error("linear 층이 없습니다");
  }

  // RGBA(224×224) → 정규화된 CHW 텐서
  function preprocess(rgba, spec) {
    const [, h, w] = spec.input, n = h * w, x = new Float32Array(3 * n);
    for (let p = 0; p < n; p++) {
      for (let ch = 0; ch < 3; ch++) x[ch * n + p] = (rgba[p * 4 + ch] / 255 - spec.mean[ch]) / spec.std[ch];
    }
    return x;
  }

  function softmax(logits) {
    const m = Math.max(...logits), e = Array.from(logits, (v) => Math.exp(v - m)), z = e.reduce((a, b) => a + b, 0);
    return e.map((v) => v / z);
  }

  const api = { load, forward, preprocess, softmax };
  if (typeof module !== "undefined" && module.exports) module.exports = api;
  else root.ClosetNet = api;
})(typeof self !== "undefined" ? self : this);
