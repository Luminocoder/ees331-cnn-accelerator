"""
端到端验证：模拟"手绘前端"的画布输入，比较朴素输入 vs 预处理输入

很多前端 demo 的做法是：把画布直接缩放到 28x28 丢进模型。MNIST 训练图是
"黑底白字 + 裁剪后缩放到 20x20 + 重心居中"，两者分布差得很远，所以手写识别率会从
98% 掉到 60% 上下。

本脚本做三件事：
1. 用 MNIST 测试图合成"画布风格"输入（白底黑字、放大、加粗、随机偏移、抗锯齿）
2. 对比三种喂法在同一批数据上的准确率：
      A. 朴素缩放（常见前端写法）
      B. 只做极性反转
      C. 本项目的 mnist_preprocess.prepare_canvas_digit（裁剪 -> 20x20 -> 重心居中）
3. 用旧权重 / 新权重分别跑，输出两张对比图表

用法：
    python verify_canvas_pipeline.py
"""

import json
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from PIL import Image

from mnist_preprocess import prepare_canvas_digit, preprocess

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "improved")
os.makedirs(OUT_DIR, exist_ok=True)

N_SAMPLES = 2000


# ----------------------------------------------------------------------
# 1. 权重加载与推理（与 step3 的 npz 格式一致：先 W 后 b）
# ----------------------------------------------------------------------
def load_mlp(npz_path):
    d = np.load(npz_path)
    n = len(d.files) // 2
    W = [d[f"arr_{i}"] for i in range(n)]
    b = [d[f"arr_{i + n}"] for i in range(n)]
    return W, b


def predict(W, b, X, batch=1024):
    preds = np.empty(X.shape[0], dtype=np.int64)
    for s in range(0, X.shape[0], batch):
        a = X[s:s + batch]
        for i in range(len(W) - 1):
            a = np.maximum(0.0, a @ W[i] + b[i])
        z = a @ W[-1] + b[-1]
        preds[s:s + batch] = np.argmax(z, axis=1)
    return preds


# ----------------------------------------------------------------------
# 2. 合成"画布风格"输入
# ----------------------------------------------------------------------
def digit_to_canvas(img28, rng):
    """把 28x28 黑底白字的标准图，变成一张 280x280 白底黑字的"手绘照片"。

    模拟真实前端会引入的差异：
      - 放大（低分辨率 -> 高分辨率，线条出现抗锯齿）
      - 笔画变粗（鼠标/手指比 MNIST 采样笔粗）
      - 随机位置偏移（用户不会画在正中间）
      - 白底黑字（网页画布默认）
    """
    scale = int(rng.integers(80, 140))          # 数字放大后的边长
    up = np.asarray(
        Image.fromarray((img28 * 255).astype(np.uint8)).resize((scale, scale), Image.BICUBIC),
        dtype=np.float32) / 255.0

    # 加粗 1~2 像素
    times = int(rng.integers(1, 3))
    for _ in range(times):
        up = np.pad(up, 1)
        acc = np.zeros((up.shape[0] - 2, up.shape[1] - 2), dtype=np.float32)
        for dy in (0, 1, 2):
            for dx in (0, 1, 2):
                acc = np.maximum(acc, up[dy:dy + acc.shape[0], dx:dx + acc.shape[1]])
        up = acc

    # 贴到 280x280 白底画布上的随机位置
    canvas = np.ones((280, 280), dtype=np.float32)
    oy = int(rng.integers(10, 280 - up.shape[0] - 10))
    ox = int(rng.integers(10, 280 - up.shape[1] - 10))
    canvas[oy:oy + up.shape[0], ox:ox + up.shape[1]] = 1.0 - up   # 黑字
    return canvas


def canvas_to_naive(canvas):
    """朴素做法：直接缩放到 28x28，不做极性/裁剪/居中处理。"""
    im = Image.fromarray((canvas * 255).astype(np.uint8)).resize((28, 28), Image.LANCZOS)
    return np.asarray(im, dtype=np.float32).reshape(-1) / 255.0


def canvas_to_invert_only(canvas):
    """只反色（知道 MNIST 是黑底白字），但没裁剪/缩放/居中。"""
    im = Image.fromarray((canvas * 255).astype(np.uint8)).resize((28, 28), Image.LANCZOS)
    return 1.0 - np.asarray(im, dtype=np.float32).reshape(-1) / 255.0


# ----------------------------------------------------------------------
# 3. 主流程
# ----------------------------------------------------------------------
def main():
    from sklearn.datasets import fetch_openml
    mnist = fetch_openml("mnist_784", version=1, as_frame=False)
    X = mnist.data.astype(np.float32) / 255.0
    y = mnist.target.astype(np.int64)

    rng = np.random.default_rng(2024)
    idx = rng.choice(np.arange(60000, 70000), N_SAMPLES, replace=False)
    X_te, y_te = X[idx], y[idx]

    canvases = [digit_to_canvas(X_te[i].reshape(28, 28), rng) for i in range(N_SAMPLES)]
    X_naive = np.stack([canvas_to_naive(c) for c in canvases])
    X_invert = np.stack([canvas_to_invert_only(c) for c in canvases])
    X_prep = np.stack([prepare_canvas_digit(c) for c in canvases])

    paths = {
        "A. naive resize (no invert/crop)": X_naive,
        "B. invert only": X_invert,
        "C. full preprocess (ours)": X_prep,
    }

    weight_sets = {}
    for name, path in (("old v1 (98.18%)", os.path.join(BASE_DIR, "mnist_weights_v1_98p18.npz")),
                       ("new 256x128", os.path.join(BASE_DIR, "mnist_weights.npz")),
                       ("new 512x256", os.path.join(BASE_DIR, "mnist_weights_big.npz"))):
        if os.path.exists(path):
            weight_sets[name] = load_mlp(path)
        else:
            print(f"[跳过] 未找到 {os.path.basename(path)}")

    results = {}
    for wname, (W, b) in weight_sets.items():
        results[wname] = {}
        print(f"\n===== 权重: {wname} (arch={[w.shape[0] for w in W] + [W[-1].shape[1]]}) =====")
        for pname, Xin in paths.items():
            acc = float(np.mean(predict(W, b, Xin) == y_te))
            results[wname][pname] = acc
            print(f"  {pname:38s} -> {acc * 100:5.2f}%")

    # ---- 图表 1：三种输入路径 × 新旧权重的准确率
    fig, ax = plt.subplots(figsize=(9, 4.4))
    path_names = list(paths.keys())
    wnames = list(results.keys())
    width = 0.8 / max(1, len(wnames))
    for i, wname in enumerate(wnames):
        vals = [results[wname][p] * 100 for p in path_names]
        xs = np.arange(len(path_names)) + (i - (len(wnames) - 1) / 2) * width
        bars = ax.bar(xs, vals, width, label=wname)
        for xi, v in zip(xs, vals):
            ax.text(xi, v + 0.8, f"{v:.1f}%", ha="center", fontsize=8)
    ax.set_xticks(np.arange(len(path_names)))
    ax.set_xticklabels(["naive resize\n(no invert/crop)", "invert only", "full preprocess\n(ours)"])
    ax.set_ylabel("Accuracy on drawn-like inputs (%)")
    ax.set_ylim(0, 105)
    ax.set_title("Same model, different input pipelines (synthetic 'frontend canvas')")
    ax.grid(axis="y", ls="--", alpha=0.4)
    ax.legend()
    fig.tight_layout()
    out1 = os.path.join(OUT_DIR, "canvas_pipeline_comparison.png")
    fig.savefig(out1, dpi=150)
    plt.close(fig)
    print(f"\n已保存: {out1}")

    # ---- 图表 2：10 个样例的画布 -> 预处理 -> 预测
    W, b = weight_sets[list(weight_sets.keys())[-1]]
    preds = predict(W, b, X_prep)
    show = rng.choice(np.arange(N_SAMPLES), 10, replace=False)

    fig, axes = plt.subplots(2, 10, figsize=(16, 3.8))
    for k, i in enumerate(show):
        axes[0, k].imshow(canvases[i], cmap="gray", vmin=0, vmax=1)
        axes[0, k].set_title("canvas", fontsize=8)
        axes[0, k].axis("off")
        axes[1, k].imshow(X_prep[i].reshape(28, 28), cmap="gray", vmin=0, vmax=1)
        ok = preds[i] == y_te[i]
        axes[1, k].set_title(f"pred {preds[i]} (true {y_te[i]})",
                             fontsize=8, color="green" if ok else "red")
        axes[1, k].axis("off")
    fig.suptitle("Canvas input -> preprocessed 28x28 -> prediction", fontsize=13)
    fig.tight_layout()
    out2 = os.path.join(OUT_DIR, "canvas_examples.png")
    fig.savefig(out2, dpi=150)
    plt.close(fig)
    print(f"已保存: {out2}")

    with open(os.path.join(OUT_DIR, "canvas_results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\n结果已保存: improved/canvas_results.json")


if __name__ == "__main__":
    main()
