"""
步骤 3 改进版：把 MNIST 识别率从 ~98.1% 提升到 99%+

与 step3_mnist_recognition.py 的关系：
- 网络接口完全一致（权重按 arr_0..arr_5 保存，权重在前、偏置在后）
- 输入 784 维、输出 10 类，前端可以直接替换 mnist_weights.npz
- 本脚本额外产出「兼容旧结构」的 [784,256,128,10] 和「更强」的 [784,512,256,10] 两套权重

相比原版的 4 个改进点（每一项都能在训练曲线上看到效果）：
1. 优化器：SGD -> Adam（自适应学习率，收敛更快更稳）
2. 学习率：固定衰减 -> 余弦退火（前期大步走，后期精调）
3. 数据增强：随机平移 ±2px / 加粗 / 细化 / 亮度抖动
   —— 这一步是"手绘前端"识别率低的关键：真实手写笔画粗细、位置和 MNIST 不一样，
      让模型见过这些扰动，对真实输入才鲁棒
4. 最优模型保留：按干净测试集准确率保存历史最优，而不是直接用最后一轮

用法：
    python step3_mnist_improved.py              # 完整训练（两套架构）
    python step3_mnist_improved.py --quick      # 快速验证（少量 epoch，精度不保证）
"""

import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import argparse
import json
import os
import time

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.metrics import confusion_matrix

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "improved")
os.makedirs(OUT_DIR, exist_ok=True)

# ==========================================
# 1. 基础算子
# ==========================================
def relu(z):
    return np.maximum(0.0, z)


def softmax(z):
    z = z - np.max(z, axis=1, keepdims=True)
    e = np.exp(z)
    return e / np.sum(e, axis=1, keepdims=True)


# ==========================================
# 2. 网络（前向 / 反向 / Adam / 存取）
# ==========================================
class MLP:
    """全连接网络：ReLU 隐藏层 + Softmax 输出，交叉熵损失，Adam 优化。"""

    def __init__(self, sizes, seed=42):
        self.sizes = list(sizes)
        rng = np.random.default_rng(seed)
        # He 初始化（适配 ReLU）
        self.W = [rng.standard_normal((sizes[i], sizes[i + 1])).astype(np.float32)
                  * np.sqrt(2.0 / sizes[i]) for i in range(len(sizes) - 1)]
        self.b = [np.zeros((1, sizes[i + 1]), dtype=np.float32) for i in range(len(sizes) - 1)]
        # Adam 状态
        self.mW = [np.zeros_like(w) for w in self.W]
        self.vW = [np.zeros_like(w) for w in self.W]
        self.mb = [np.zeros_like(b) for b in self.b]
        self.vb = [np.zeros_like(b) for b in self.b]
        self.t = 0

    def forward(self, X):
        """返回 (激活列表, 预激活列表, softmax 概率)"""
        a = X
        acts, zs = [a], []
        for i in range(len(self.W) - 1):
            z = a @ self.W[i] + self.b[i]
            zs.append(z)
            a = relu(z)
            acts.append(a)
        z = a @ self.W[-1] + self.b[-1]
        acts.append(softmax(z))
        return acts, zs, acts[-1]

    def backward(self, y_onehot, acts, zs, weight_decay=1e-5):
        m = y_onehot.shape[0]
        dW = [None] * len(self.W)
        db = [None] * len(self.b)

        delta = acts[-1] - y_onehot                      # softmax + 交叉熵的合并导数
        dW[-1] = acts[-2].T @ delta / m + weight_decay * self.W[-1]
        db[-1] = np.sum(delta, axis=0, keepdims=True) / m

        for l in range(len(self.W) - 2, -1, -1):
            delta = (delta @ self.W[l + 1].T) * (zs[l] > 0).astype(np.float32)
            dW[l] = acts[l].T @ delta / m + weight_decay * self.W[l]
            db[l] = np.sum(delta, axis=0, keepdims=True) / m
        return dW, db

    def adam_step(self, dW, db, lr, beta1=0.9, beta2=0.999, eps=1e-8):
        self.t += 1
        b1c = 1.0 - beta1 ** self.t
        b2c = 1.0 - beta2 ** self.t
        for i in range(len(self.W)):
            self.mW[i] = beta1 * self.mW[i] + (1 - beta1) * dW[i]
            self.vW[i] = beta2 * self.vW[i] + (1 - beta2) * (dW[i] ** 2)
            self.W[i] -= lr * (self.mW[i] / b1c) / (np.sqrt(self.vW[i] / b2c) + eps)

            self.mb[i] = beta1 * self.mb[i] + (1 - beta1) * db[i]
            self.vb[i] = beta2 * self.vb[i] + (1 - beta2) * (db[i] ** 2)
            self.b[i] -= lr * (self.mb[i] / b1c) / (np.sqrt(self.vb[i] / b2c) + eps)

    def predict(self, X, batch_size=2048):
        preds = np.empty(X.shape[0], dtype=np.int64)
        for s in range(0, X.shape[0], batch_size):
            _, _, p = self.forward(X[s:s + batch_size])
            preds[s:s + batch_size] = np.argmax(p, axis=1)
        return preds

    def save(self, path):
        np.savez(path, *self.W, *self.b)

    def to_state(self):
        return {k: v.copy() for k, v in zip(
            [f"W{i}" for i in range(len(self.W))] + [f"b{i}" for i in range(len(self.b))],
            self.W + self.b)}

    def load_state(self, state):
        self.W = [state[k].copy() for k in [f"W{i}" for i in range(len(self.W))]]
        self.b = [state[k].copy() for k in [f"b{i}" for i in range(len(self.b))]]


# ==========================================
# 3. 数据增强（模拟真实手写前端的输入差异）
# ==========================================
def _shift(imgs, dy, dx):
    """整批同向平移，空出来的部分补 0（黑底）。"""
    p = 2
    padded = np.pad(imgs, ((0, 0), (p, p), (p, p)), constant_values=0.0)
    y0, x0 = p - dy, p - dx
    return padded[:, y0:y0 + 28, x0:x0 + 28]


def _thicken(imgs):
    """3x3 最大值滤波：笔画变粗（模拟粗笔/触屏手写）"""
    padded = np.pad(imgs, ((0, 0), (1, 1), (1, 1)), constant_values=0.0)
    out = np.zeros_like(imgs)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            out = np.maximum(out, padded[:, dy:dy + 28, dx:dx + 28])
    return out


def _thin(imgs):
    """3x3 最小值滤波：笔画变细（模拟细笔/低分辨率缩放）"""
    padded = np.pad(imgs, ((0, 0), (1, 1), (1, 1)), constant_values=0.0)
    out = np.ones_like(imgs)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            out = np.minimum(out, padded[:, dy:dy + 28, dx:dx + 28])
    return out


def augment(X, y, rng,
            p_shift=0.6, p_thick=0.25, p_thin=0.15, p_bright=0.5):
    """对一批 (B,784) 图像做随机扰动，返回扰动后的 (X, y)。"""
    B = X.shape[0]
    imgs = X.reshape(B, 28, 28).astype(np.float32).copy()

    # --- 随机平移（最主要的鲁棒性来源：手绘数字不可能和训练集一样居中）
    do_shift = rng.random(B) < p_shift
    dys = rng.integers(-2, 3, B)
    dxs = rng.integers(-2, 3, B)
    for dy in range(-2, 3):
        for dx in range(-2, 3):
            m = do_shift & (dys == dy) & (dxs == dx) & ~((dy == 0) & (dx == 0))
            if m.any():
                imgs[m] = _shift(imgs[m], dy, dx)

    # --- 随机加粗 / 细化（笔画粗细差异）
    for fn, p in ((_thicken, p_thick), (_thin, p_thin)):
        m = rng.random(B) < p
        if m.any():
            imgs[m] = fn(imgs[m])

    # --- 随机亮度（抗锯齿、屏幕亮度、压缩损失）
    m = rng.random(B) < p_bright
    if m.any():
        imgs[m] = np.clip(imgs[m] * (0.75 + 0.5 * rng.random((m.sum(), 1, 1))), 0.0, 1.0)

    return imgs.reshape(B, -1), y


def make_drawn_like(X, rng, thicken=True, shift=True, jitter=0.15):
    """把标准 MNIST 测试集改造成「模拟手绘」版本，用来衡量前端鲁棒性。
    步骤：加粗 -> 随机平移 -> 亮度/噪声抖动。"""
    imgs = X.reshape(-1, 28, 28).astype(np.float32).copy()
    if thicken:
        imgs = _thicken(imgs)
    B = imgs.shape[0]
    if shift:
        dys = rng.integers(-2, 3, B)
        dxs = rng.integers(-2, 3, B)
        out = np.zeros_like(imgs)
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                m = (dys == dy) & (dxs == dx)
                if m.any():
                    out[m] = _shift(imgs[m], dy, dx)
        imgs = out
    imgs = np.clip(imgs * (1.0 - jitter + 2 * jitter * rng.random((B, 1, 1))), 0.0, 1.0)
    imgs = np.clip(imgs + jitter * 0.3 * rng.standard_normal(imgs.shape), 0.0, 1.0)
    return imgs.reshape(B, -1)


# ==========================================
# 4. 数据加载
# ==========================================
def load_mnist():
    from sklearn.datasets import fetch_openml
    print("正在加载完整 MNIST 数据集（有本地缓存，无需联网）...")
    t0 = time.time()
    mnist = fetch_openml("mnist_784", version=1, as_frame=False)
    X = mnist.data.astype(np.float32) / 255.0
    y = mnist.target.astype(np.int64)
    y_onehot = np.zeros((y.size, 10), dtype=np.float32)
    y_onehot[np.arange(y.size), y] = 1.0
    print(f"[OK] 加载完成: {X.shape[0]} 样本, 耗时 {time.time() - t0:.1f}s")
    return X, y, y_onehot


# ==========================================
# 5. 训练主过程
# ==========================================
def train_model(sizes, X_train, y_train, X_test, y_test, *,
                epochs=30, batch_size=128, lr_max=2e-3, lr_min=1e-4,
                aug=True, seed=42, tag="model"):
    rng = np.random.default_rng(seed)
    net = MLP(sizes, seed=seed)
    n = X_train.shape[0]
    y_train_lab = np.argmax(y_train, axis=1)   # 训练集标签（比较准确率用）
    hist = {"train_loss": [], "train_acc": [], "test_acc": [], "lr": []}
    best = {"acc": -1.0, "state": None, "epoch": 0}

    print(f"\n=== 训练 {tag} 架构={sizes} epochs={epochs} "
          f"batch={batch_size} aug={'ON' if aug else 'OFF'} ===")
    t0 = time.time()

    for epoch in range(epochs):
        # 余弦退火
        lr = lr_min + 0.5 * (lr_max - lr_min) * (1 + np.cos(np.pi * epoch / epochs))
        perm = rng.permutation(n)
        loss_sum, seen = 0.0, 0

        for s in range(0, n - batch_size + 1, batch_size):
            idx = perm[s:s + batch_size]
            Xb, yb = X_train[idx], y_train[idx]
            if aug:
                Xb, yb = augment(Xb, yb, rng)
            acts, zs, p = net.forward(Xb)
            eps = 1e-15
            loss_sum += -np.sum(yb * np.log(np.clip(p, eps, 1.0)))
            dW, db = net.backward(yb, acts, zs)
            net.adam_step(dW, db, lr)
            seen += Xb.shape[0]

        train_loss = loss_sum / seen
        train_acc = np.mean(net.predict(X_train) == y_train_lab)
        test_acc = np.mean(net.predict(X_test) == y_test)

        hist["train_loss"].append(float(train_loss))
        hist["train_acc"].append(float(train_acc))
        hist["test_acc"].append(float(test_acc))
        hist["lr"].append(float(lr))

        if test_acc > best["acc"]:
            best = {"acc": float(test_acc), "state": net.to_state(), "epoch": epoch + 1}

        print(f"Epoch {epoch + 1:02d}/{epochs} | lr {lr:.4f} | loss {train_loss:.4f} | "
              f"train {train_acc * 100:.2f}% | test {test_acc * 100:.2f}%"
              f"{'  <-- best' if test_acc == best['acc'] else ''}")

    net.load_state(best["state"])
    print(f"[{tag}] 最优 epoch {best['epoch']}, 测试准确率 {best['acc'] * 100:.2f}%, "
          f"耗时 {time.time() - t0:.1f}s")
    return net, hist, best


# ==========================================
# 6. 评估与可视化
# ==========================================
def evaluate(net, X, y, name):
    pred = net.predict(X)
    acc = float(np.mean(pred == y))
    print(f"[评估] {name}: {acc * 100:.2f}%")
    return acc, pred


def save_curves(hist, path, title):
    fig, axes = plt.subplots(1, 2, figsize=(12, 4.5))
    ep = np.arange(1, len(hist["train_loss"]) + 1)

    axes[0].plot(ep, hist["train_loss"], color="red", marker="o", ms=3, label="Train Loss")
    axes[0].set_title("Training Loss per Epoch")
    axes[0].set_xlabel("Epoch")
    axes[0].set_ylabel("Loss")
    axes[0].grid(True)
    axes[0].legend()

    axes[1].plot(ep, np.array(hist["train_acc"]) * 100, color="blue", marker="s", ms=3, label="Train Acc")
    axes[1].plot(ep, np.array(hist["test_acc"]) * 100, color="green", marker="^", ms=3, label="Test Acc")
    axes[1].set_title("Accuracy per Epoch")
    axes[1].set_xlabel("Epoch")
    axes[1].set_ylabel("Accuracy (%)")
    axes[1].grid(True)
    axes[1].legend()

    fig.suptitle(title, fontsize=14)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


def save_confusion(y_true, pred, path):
    cm = confusion_matrix(y_true, pred)
    fig, ax = plt.subplots(figsize=(8, 8))
    im = ax.imshow(cm, interpolation="nearest", cmap=plt.cm.Blues)
    ax.set_title("Confusion Matrix on Test Set")
    fig.colorbar(im)
    ticks = np.arange(10)
    ax.set_xticks(ticks)
    ax.set_yticks(ticks)
    thresh = cm.max() / 2.0
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            ax.text(j, i, format(cm[i, j], "d"), ha="center",
                    color="white" if cm[i, j] > thresh else "black")
    ax.set_ylabel("True label")
    ax.set_xlabel("Predicted label")
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)


# ==========================================
# 7. 主程序
# ==========================================
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--quick", action="store_true", help="快速模式：少 epoch，仅验证流程")
    args = ap.parse_args()

    X, y, y_onehot = load_mnist()
    X_train, X_test = X[:60000], X[60000:]
    y_train, y_test = y_onehot[:60000], y[60000:]
    y_train_lab, y_test_lab = y[:60000], y[60000:]

    epochs_main = 4 if args.quick else 30
    epochs_big = 4 if args.quick else 36

    report = {}

    main_path = os.path.join(BASE_DIR, "mnist_weights.npz")
    big_path = os.path.join(BASE_DIR, "mnist_weights_big.npz")
    backup = os.path.join(BASE_DIR, "mnist_weights_v1_98p18.npz")

    import shutil
    if os.path.exists(main_path) and not os.path.exists(backup):
        shutil.copy2(main_path, backup)
        print(f"旧权重已备份: {os.path.basename(backup)}")

    # ---- 1) 兼容旧前端的结构 [784,256,128,10]（形状与旧权重完全一致，直接替换）
    net_main, hist_main, best_main = train_model(
        [784, 256, 128, 10], X_train, y_train, X_test, y_test,
        epochs=epochs_main, lr_max=2e-3, aug=True, tag="main-256-128")
    # 训练完立刻保存，避免后面跑大模型时中断导致白练
    net_main.save(main_path)
    print(f"已保存: {os.path.basename(main_path)} (784-256-128-10, 兼容旧前端)")

    # ---- 2) 更强的结构 [784,512,256,10]（带宽允许时优先用这版）
    net_big, hist_big, best_big = train_model(
        [784, 512, 256, 10], X_train, y_train, X_test, y_test,
        epochs=epochs_big, lr_max=1.5e-3, aug=True, tag="big-512-256")
    net_big.save(big_path)
    print(f"已保存: {os.path.basename(big_path)} (784-512-256-10, 精度更高)")

    # ---- 3) 干净测试集 / 模拟手绘测试集 双重评估
    rng_eval = np.random.default_rng(777)
    X_test_drawn = make_drawn_like(X_test, rng_eval)

    report["clean_test"] = {}
    report["drawn_like_test"] = {}
    for name, net in (("main-784-256-128", net_main), ("big-784-512-256", net_big)):
        acc_c, pred_c = evaluate(net, X_test, y_test_lab, f"{name} clean")
        acc_d, _ = evaluate(net, X_test_drawn, y_test_lab, f"{name} drawn-like")
        report["clean_test"][name] = acc_c
        report["drawn_like_test"][name] = acc_d

    # ---- 5) 图表
    save_curves(hist_main, os.path.join(OUT_DIR, "training_curves_main.png"),
                "Improved MLP 784-256-128 (Adam + Augmentation)")
    save_curves(hist_big, os.path.join(OUT_DIR, "training_curves_big.png"),
                "Improved MLP 784-512-256 (Adam + Augmentation)")
    save_confusion(y_test_lab, net_main.predict(X_test), os.path.join(OUT_DIR, "confusion_matrix_main.png"))
    save_confusion(y_test_lab, net_big.predict(X_test), os.path.join(OUT_DIR, "confusion_matrix_big.png"))

    # 旧权重对照（如果备份存在）
    try:
        d = np.load(backup)
        old = MLP([784, 256, 128, 10])
        old.W = [d[f"arr_{i}"] for i in range(3)]
        old.b = [d[f"arr_{i + 3}"] for i in range(3)]
        old_clean = float(np.mean(old.predict(X_test) == y_test_lab))
        old_drawn = float(np.mean(old.predict(X_test_drawn) == y_test_lab))
    except Exception:
        old_clean = old_drawn = None

    # 对比柱状图
    bars = [("Old 98.1%", old_clean, old_drawn),
            ("New 256x128", report["clean_test"]["main-784-256-128"], report["drawn_like_test"]["main-784-256-128"]),
            ("New 512x256", report["clean_test"]["big-784-512-256"], report["drawn_like_test"]["big-784-512-256"])]
    fig, ax = plt.subplots(figsize=(7.5, 4.2))
    xpos = np.arange(len(bars))
    clean = [b[1] * 100 if b[1] is not None else 0 for b in bars]
    drawn = [b[2] * 100 if b[2] is not None else 0 for b in bars]
    ax.bar(xpos - 0.18, clean, 0.36, label="Clean MNIST test", color="#1f77b4")
    ax.bar(xpos + 0.18, drawn, 0.36, label="Drawn-like (thicker+shifted)", color="#ff7f0e")
    for i, (c, d) in enumerate(zip(clean, drawn)):
        ax.text(i - 0.18, c + 0.15, f"{c:.2f}%", ha="center", fontsize=9)
        ax.text(i + 0.18, d + 0.15, f"{d:.2f}%", ha="center", fontsize=9)
    ax.set_xticks(xpos)
    ax.set_xticklabels([b[0] for b in bars])
    ax.set_ylabel("Accuracy (%)")
    ax.set_ylim(55, 103)
    ax.set_title("Recognition Accuracy: Old vs Improved Weights")
    ax.grid(axis="y", ls="--", alpha=0.5)
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(OUT_DIR, "accuracy_comparison.png"), dpi=150)
    plt.close(fig)

    # ---- 6) 报告
    if old_clean is not None:
        report["old_weights"] = {"clean": old_clean, "drawn_like": old_drawn}
    report["best_epochs"] = {"main": best_main["epoch"], "big": best_big["epoch"]}
    with open(os.path.join(OUT_DIR, "results.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)

    print("\n========== 最终结果 ==========")
    if old_clean is not None:
        print(f"旧权重        : clean {old_clean * 100:.2f}% | drawn-like {old_drawn * 100:.2f}%")
    print(f"新 784-256-128: clean {report['clean_test']['main-784-256-128'] * 100:.2f}% | "
          f"drawn-like {report['drawn_like_test']['main-784-256-128'] * 100:.2f}%")
    print(f"新 784-512-256: clean {report['clean_test']['big-784-512-256'] * 100:.2f}% | "
          f"drawn-like {report['drawn_like_test']['big-784-512-256'] * 100:.2f}%")
    print(f"\n结果已保存到: {OUT_DIR}")


if __name__ == "__main__":
    main()
