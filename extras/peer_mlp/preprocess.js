/**
 * MNIST 预处理（JavaScript 版，浏览器/Node 通用）
 *
 * 与 Python 版 mnist_preprocess.py 完全等价，供网页前端直接调用：
 *   1. 转灰度 + 按边框判断底色，必要时反色（统一成"黑底白字"）
 *   2. 按墨迹阈值裁剪外接矩形
 *   3. 等比缩放到 20x20（双线性插值）
 *   4. 放进 28x28 中央
 *   5. 按重心平移居中
 *
 * 用法（浏览器，配合 <canvas> 手写板）：
 *   const ctx = canvas.getContext('2d');
 *   const imgData = ctx.getImageData(0, 0, canvas.width, canvas.height);
 *   const vec = canvasImageDataToMnist(imgData);   // Float32Array(784), 0~1
 *   // 再把 vec 送进模型：logits = vec · W0 + b0 ...
 *
 * 注意：如果你直接用 28x28 的小画布并已经手工反色居中，本函数依然适用（幂等）。
 */

const SIZE = 28;
const BOX = 20;

/** ImageData(RGBA) -> {gray: Float32Array(w*h), w, h}，已做 alpha 合成 */
function imageDataToGray(imgData) {
  const { width: w, height: h, data } = imgData;
  const gray = new Float32Array(w * h);
  for (let i = 0; i < w * h; i++) {
    const r = data[i * 4] / 255;
    const g = data[i * 4 + 1] / 255;
    const b = data[i * 4 + 2] / 255;
    const a = data[i * 4 + 3] / 255;
    // 透明像素当黑底处理，避免 PNG 透明区被误当成"白纸"
    gray[i] = ((r + g + b) / 3) * a;
  }
  return { gray, w, h };
}

/** 用最外圈像素判断底色：>0.5 认为白底黑字 */
function isLightBackground(gray, w, h) {
  let sum = 0;
  let n = 0;
  for (let x = 0; x < w; x++) {
    sum += gray[x] + gray[(h - 1) * w + x];
    n += 2;
  }
  for (let y = 0; y < h; y++) {
    sum += gray[y * w] + gray[y * w + (w - 1)];
    n += 2;
  }
  return sum / n > 0.5;
}

/** 双线性采样：把 src(sw x sh) 放大/缩小到 dw x dh */
function resizeBilinear(src, sw, sh, dw, dh) {
  const out = new Float32Array(dw * dh);
  for (let y = 0; y < dh; y++) {
    const fy = ((y + 0.5) * sh) / dh - 0.5;
    const y0 = Math.max(0, Math.min(sh - 1, Math.floor(fy)));
    const y1 = Math.max(0, Math.min(sh - 1, y0 + 1));
    const wy = Math.max(0, Math.min(1, fy - y0));
    for (let x = 0; x < dw; x++) {
      const fx = ((x + 0.5) * sw) / dw - 0.5;
      const x0 = Math.max(0, Math.min(sw - 1, Math.floor(fx)));
      const x1 = Math.max(0, Math.min(sw - 1, x0 + 1));
      const wx = Math.max(0, Math.min(1, fx - x0));
      const v =
        src[y0 * sw + x0] * (1 - wx) * (1 - wy) +
        src[y0 * sw + x1] * wx * (1 - wy) +
        src[y1 * sw + x0] * (1 - wx) * wy +
        src[y1 * sw + x1] * wx * wy;
      out[y * dw + x] = v;
    }
  }
  return out;
}

/**
 * 核心：ImageData -> Float32Array(784)，取值 0~1，行优先（与 np.reshape(28,28) 一致）
 */
function canvasImageDataToMnist(imgData) {
  const { gray, w, h } = imageDataToGray(imgData);
  const g = Float32Array.from(gray);

  // (1) 统一成黑底白字
  if (isLightBackground(g, w, h)) {
    for (let i = 0; i < g.length; i++) g[i] = 1 - g[i];
  }

  // (2) 找墨迹外接矩形（阈值 = 最大亮度的 20%）
  let maxV = 0;
  for (let i = 0; i < g.length; i++) maxV = Math.max(maxV, g[i]);
  if (maxV < 1e-6) return new Float32Array(SIZE * SIZE); // 空画布

  const th = maxV * 0.2;
  let minX = w, maxX = -1, minY = h, maxY = -1;
  for (let y = 0; y < h; y++) {
    for (let x = 0; x < w; x++) {
      if (g[y * w + x] > th) {
        if (x < minX) minX = x;
        if (x > maxX) maxX = x;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
  }
  if (maxX < 0) return new Float32Array(SIZE * SIZE);

  const cw = maxX - minX + 1;
  const ch = maxY - minY + 1;
  const crop = new Float32Array(cw * ch);
  for (let y = 0; y < ch; y++) {
    for (let x = 0; x < cw; x++) {
      crop[y * cw + x] = g[(minY + y) * w + (minX + x)];
    }
  }

  // (3) 等比缩放到最长边 20
  const scale = BOX / Math.max(cw, ch);
  const nw = Math.max(1, Math.round(cw * scale));
  const nh = Math.max(1, Math.round(ch * scale));
  const small = resizeBilinear(crop, cw, ch, nw, nh);

  // (4) 放进 28x28 中央
  const canvas = new Float32Array(SIZE * SIZE);
  const offX = Math.floor((SIZE - nw) / 2);
  const offY = Math.floor((SIZE - nh) / 2);
  for (let y = 0; y < nh; y++) {
    for (let x = 0; x < nw; x++) {
      canvas[(offY + y) * SIZE + (offX + x)] = small[y * nw + x];
    }
  }

  // (5) 按重心平移居中
  let total = 0, sy = 0, sx = 0;
  for (let y = 0; y < SIZE; y++) {
    for (let x = 0; x < SIZE; x++) {
      const v = canvas[y * SIZE + x];
      total += v;
      sy += v * y;
      sx += v * x;
    }
  }
  if (total > 1e-6) {
    const cy = sy / total;
    const cx = sx / total;
    const dy = Math.round(SIZE / 2 - 0.5 - cy);
    const dx = Math.round(SIZE / 2 - 0.5 - cx);
    if (dy !== 0 || dx !== 0) return shift2D(canvas, dy, dx);
  }
  return canvas;
}

/** 环移（与 Python 的 np.roll 一致：超出边界的部分从另一侧回来） */
function shift2D(src, dy, dx) {
  const out = new Float32Array(SIZE * SIZE);
  for (let y = 0; y < SIZE; y++) {
    for (let x = 0; x < SIZE; x++) {
      const sy = ((y - dy) % SIZE + SIZE) % SIZE;
      const sx = ((x - dx) % SIZE + SIZE) % SIZE;
      out[y * SIZE + x] = src[sy * SIZE + sx];
    }
  }
  return out;
}

/** 推理辅助：给定权重（Float32Array/Array 的数组）和 vec，返回 10 类概率 */
function mlpPredict(vec, weights, biases) {
  let a = vec;
  for (let l = 0; l < weights.length; l++) {
    const W = weights[l];      // shape (in, out) 行优先展平
    const b = biases[l];
    const inN = a.length;
    const outN = b.length;
    const z = new Float32Array(outN);
    for (let j = 0; j < outN; j++) {
      let s = b[j];
      for (let i = 0; i < inN; i++) s += a[i] * W[i * outN + j];
      z[j] = l === weights.length - 1 ? s : Math.max(0, s); // 隐藏层 ReLU
    }
    a = z;
  }
  // softmax
  let maxZ = -Infinity;
  for (const v of a) maxZ = Math.max(maxZ, v);
  let sum = 0;
  const p = new Float32Array(a.length);
  for (let i = 0; i < a.length; i++) {
    p[i] = Math.exp(a[i] - maxZ);
    sum += p[i];
  }
  for (let i = 0; i < a.length; i++) p[i] /= sum;
  return p;
}

// 导出（同时兼容浏览器 <script> 和 Node require）
if (typeof module !== "undefined" && module.exports) {
  module.exports = { canvasImageDataToMnist, mlpPredict };
} else {
  window.canvasImageDataToMnist = canvasImageDataToMnist;
  window.mlpPredict = mlpPredict;
}
