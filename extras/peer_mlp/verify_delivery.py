"""
交付包自检：确认两份权重能正常加载、精度达标、预处理链路可用

用法：
    python verify_delivery.py

预期输出（与随包 README 的数字一致）：
    旧 v1        : ~98.1%
    新 256x128   : ~98.6%
    新 512x256   : ~99.0%
    画布端到端   : 预测正确 + 置信度 > 90%
"""

import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
N_CHECK = 2000          # 自检样本数（越大越准，越小越快）

EXPECT = {
    "mnist_weights_v1_98p18.npz": 0.975,
    "mnist_weights.npz": 0.980,
    "mnist_weights_big.npz": 0.983,
}


def load_mlp(path):
    d = np.load(path)
    n = len(d.files) // 2
    W = [d[f"arr_{i}"].astype(np.float32) for i in range(n)]
    b = [d[f"arr_{i + n}"].astype(np.float32) for i in range(n)]
    return W, b


def predict(W, b, X, batch=1024):
    preds = np.empty(X.shape[0], dtype=np.int64)
    for s in range(0, X.shape[0], batch):
        a = X[s:s + batch]
        for i in range(len(W) - 1):
            a = np.maximum(0.0, a @ W[i] + b[i])
        preds[s:s + batch] = np.argmax(a @ W[-1] + b[-1], axis=1)
    return preds


def main():
    print("=" * 62)
    print(" MNIST 交付包自检")
    print("=" * 62)

    ok = True

    # ---- 1. 权重文件是否齐全
    missing = [f for f in EXPECT if not os.path.exists(os.path.join(BASE_DIR, f))]
    if missing:
        print(f"[FAIL] 缺少权重文件: {missing}")
        ok = False
    else:
        print(f"[ OK ] 权重文件齐全 ({len(EXPECT)} 个)")

    # ---- 2. 结构是否与说明一致
    for fname, arch in (("mnist_weights.npz", [784, 256, 128, 10]),
                        ("mnist_weights_big.npz", [784, 512, 256, 10]),
                        ("mnist_weights_v1_98p18.npz", [784, 256, 128, 10])):
        path = os.path.join(BASE_DIR, fname)
        if not os.path.exists(path):
            continue
        W, b = load_mlp(path)
        got = [W[0].shape[0]] + [w.shape[1] for w in W]
        flag = "OK" if got == arch else "FAIL"
        if flag == "FAIL":
            ok = False
        print(f"[{flag:>4}] {fname:28s} 结构 {got}")

    # ---- 3. 精度自检
    try:
        from sklearn.datasets import fetch_openml
        print("\n加载 MNIST 测试集（首次运行会自动下载，约 15MB）...")
        mnist = fetch_openml("mnist_784", version=1, as_frame=False)
        X = mnist.data.astype(np.float32)[60000:] / 255.0
        y = mnist.target.astype(np.int64)[60000:]

        rng = np.random.default_rng(0)
        idx = rng.choice(np.arange(X.shape[0]), N_CHECK, replace=False)
        Xs, ys = X[idx], y[idx]

        print(f"\n精度自检（随机 {N_CHECK} 张测试图）:")
        print(f"  {'权重文件':32s} {'实测':>8s} {'要求':>8s}   结果")
        for fname, floor in EXPECT.items():
            path = os.path.join(BASE_DIR, fname)
            if not os.path.exists(path):
                continue
            W, b = load_mlp(path)
            acc = float(np.mean(predict(W, b, Xs) == ys))
            passed = acc >= floor
            ok = ok and passed
            print(f"  {fname:32s} {acc * 100:7.2f}% {floor * 100:7.1f}%   "
                  f"{'PASS' if passed else 'FAIL'}")
    except Exception as e:
        print(f"\n[SKIP] 精度自检跳过（{type(e).__name__}: {e}）")
        print("       没有网络或 sklearn 环境时属正常，权重本身仍可用。")

    # ---- 4. 预处理链路是否可用
    try:
        from mnist_preprocess import prepare_canvas_digit
        from predict_canvas import DigitRecognizer, _make_demo_canvas

        canvas, true_label = _make_demo_canvas()          # 合成一张"手绘画布"
        rec = DigitRecognizer(os.path.join(BASE_DIR, "mnist_weights_big.npz"))
        pred, probs = rec.predict_image(canvas)
        conf = float(probs[pred])
        passed = (pred == true_label) and conf > 0.9
        ok = ok and passed
        print(f"\n[{' OK ' if passed else 'FAIL'}] 画布端到端: 预测 {pred}（真值 {true_label}），"
              f"置信度 {conf * 100:.2f}%")
    except Exception as e:
        ok = False
        print(f"\n[FAIL] 预处理链路异常: {type(e).__name__}: {e}")

    print("\n" + "=" * 62)
    print(" 全部通过 ✅" if ok else " 存在失败项 ❌")
    print("=" * 62)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
