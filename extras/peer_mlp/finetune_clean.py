"""
干净数据微调：在数据增强训练之后，用少量"无增强"的干净样本把干净测试集精度拉回来

为什么需要：
数据增强（平移/加粗/细化）能显著提升"真实手写"识别率，但会让模型在干净 MNIST 上的
准确率略低（约 -0.3~0.5pp）。工业界标准做法是：
    先用增强数据训练（学到鲁棒特征） -> 最后用干净数据、小学习率微调几轮（把精度拉回）
这样两边都不吃亏。

用法：
    python finetune_clean.py --weights mnist_weights.npz --epochs 6 --lr 2e-4
    python finetune_clean.py --weights mnist_weights_big.npz --epochs 6 --lr 1.5e-4
"""

import argparse
import os
import shutil
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from step3_mnist_improved import (  # noqa: E402
    MLP, load_mnist, make_drawn_like, evaluate, BASE_DIR, OUT_DIR,
)

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def load_weights_into(npz_path):
    d = np.load(npz_path)
    n = len(d.files) // 2
    sizes = [d["arr_0"].shape[0]] + [d[f"arr_{i}"].shape[1] for i in range(n)]
    net = MLP(sizes)
    net.W = [d[f"arr_{i}"].astype(np.float32) for i in range(n)]
    net.b = [d[f"arr_{i + n}"].astype(np.float32) for i in range(n)]
    return net, sizes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--weights", default=os.path.join(BASE_DIR, "mnist_weights.npz"))
    ap.add_argument("--epochs", type=int, default=6)
    ap.add_argument("--lr", type=float, default=2e-4)
    ap.add_argument("--batch", type=int, default=128)
    ap.add_argument("--no-save", action="store_true", help="只评估微调效果，不覆盖权重文件")
    args = ap.parse_args()

    X, y, y_onehot = load_mnist()
    X_train, X_test = X[:60000], X[60000:]
    y_train, y_test = y_onehot[:60000], y[60000:]
    y_train_lab, y_test_lab = y[:60000], y[60000:]

    net, sizes = load_weights_into(args.weights)
    print(f"载入权重: {os.path.basename(args.weights)} | 架构: {sizes}")

    rng_eval = np.random.default_rng(777)
    X_drawn = make_drawn_like(X_test, rng_eval)
    before_clean = float(np.mean(net.predict(X_test) == y_test_lab))
    before_drawn = float(np.mean(net.predict(X_drawn) == y_test_lab))
    print(f"微调前: clean {before_clean * 100:.2f}% | drawn-like {before_drawn * 100:.2f}%")

    rng = np.random.default_rng(123)
    n = X_train.shape[0]
    best = {"acc": before_clean, "state": net.to_state(), "epoch": 0}

    for epoch in range(1, args.epochs + 1):
        lr = args.lr * (0.5 ** (epoch // 3))       # 每 3 轮减半
        perm = rng.permutation(n)
        loss_sum, seen = 0.0, 0
        for s in range(0, n - args.batch + 1, args.batch):
            idx = perm[s:s + args.batch]
            Xb, yb = X_train[idx], y_train[idx]     # 干净数据，不做增强
            acts, zs, p = net.forward(Xb)
            loss_sum += -np.sum(yb * np.log(np.clip(p, 1e-15, 1.0)))
            dW, db = net.backward(yb, acts, zs)
            net.adam_step(dW, db, lr)
            seen += Xb.shape[0]

        train_loss = loss_sum / seen
        test_acc = float(np.mean(net.predict(X_test) == y_test_lab))
        drawn_acc = float(np.mean(net.predict(X_drawn) == y_test_lab))
        if test_acc > best["acc"]:
            best = {"acc": test_acc, "state": net.to_state(), "epoch": epoch}
        print(f"Fine-tune epoch {epoch}/{args.epochs} | lr {lr:.5f} | loss {train_loss:.4f} | "
              f"clean {test_acc * 100:.2f}% | drawn-like {drawn_acc * 100:.2f}%")

    net.load_state(best["state"])
    after_clean = float(np.mean(net.predict(X_test) == y_test_lab))
    after_drawn = float(np.mean(net.predict(X_drawn) == y_test_lab))
    print(f"\n微调后: clean {after_clean * 100:.2f}% | drawn-like {after_drawn * 100:.2f}% "
          f"(最优轮 {best['epoch']})")

    if not args.no_save and best["epoch"] > 0 and after_clean >= before_clean:
        backup = args.weights.replace(".npz", "_augonly.npz")
        if not os.path.exists(backup):
            shutil.copy2(args.weights, backup)
            print(f"增强版权重已备份: {os.path.basename(backup)}")
        net.save(args.weights)
        print(f"已覆盖保存: {os.path.basename(args.weights)}")
    else:
        print("未保存（微调没有超过微调前，或指定了 --no-save）")


if __name__ == "__main__":
    main()
