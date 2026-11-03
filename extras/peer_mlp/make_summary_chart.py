"""
汇总对比图：旧权重 vs 两套新权重，在三种输入场景下的表现

数据来源（跑完训练 + verify 后自动生成）：
    improved/results.json          -> clean / drawn-like
    improved/canvas_results.json   -> 画布输入三种预处理路径

用法：python make_summary_chart.py
"""

import json
import os

import numpy as np
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, "improved")


def main():
    with open(os.path.join(OUT_DIR, "results.json"), encoding="utf-8") as f:
        res = json.load(f)
    with open(os.path.join(OUT_DIR, "canvas_results.json"), encoding="utf-8") as f:
        canvas = json.load(f)

    series = [
        ("Old v1 (98.18%)", res["old_weights"]["clean"],
         res["old_weights"]["drawn_like"], canvas["old v1 (98.18%)"]["C. full preprocess (ours)"]),
        ("New 256x128", res["clean_test"]["main-784-256-128"],
         res["drawn_like_test"]["main-784-256-128"], canvas["new 256x128"]["C. full preprocess (ours)"]),
        ("New 512x256", res["clean_test"]["big-784-512-256"],
         res["drawn_like_test"]["big-784-512-256"], canvas["new 512x256"]["C. full preprocess (ours)"]),
    ]

    metrics = ["Clean MNIST test", "Drawn-like input\n(thicker + shifted)", "Canvas input\n+ correct preprocess"]
    colors = ["#8c8c8c", "#1f77b4", "#2ca02c"]

    fig, ax = plt.subplots(figsize=(10, 5.2))
    x = np.arange(len(metrics))
    width = 0.8 / len(series)

    for i, (name, *vals) in enumerate(series):
        xs = x + (i - (len(series) - 1) / 2) * width
        bars = ax.bar(xs, np.array(vals) * 100, width, label=name, color=colors[i])
        for xi, v in zip(xs, vals):
            ax.text(xi, v * 100 + 0.6, f"{v * 100:.2f}%", ha="center", fontsize=9)

    ax.set_xticks(x)
    ax.set_xticklabels(metrics)
    ax.set_ylabel("Accuracy (%)")
    ax.set_ylim(55, 103)
    ax.set_title("MNIST Recognition: Old vs Improved Weights (bigger is better)")
    ax.grid(axis="y", ls="--", alpha=0.4)
    ax.legend(loc="lower right")
    fig.tight_layout()
    out = os.path.join(OUT_DIR, "summary_comparison.png")
    fig.savefig(out, dpi=160)
    plt.close(fig)
    print(f"已保存: {out}")


if __name__ == "__main__":
    main()
