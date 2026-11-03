"""
单张图片识别工具（前端后端可直接抄这个文件）

用法：
    python predict_canvas.py some_digit.png            # 用默认权重 (784-256-128)
    python predict_canvas.py some_digit.png --big      # 用更强权重 (784-512-256)
    python predict_canvas.py --demo                    # 用它自己合成一张画布图做演示

作为库调用：
    from predict_canvas import DigitRecognizer
    rec = DigitRecognizer("mnist_weights.npz")
    digit, probs = rec.predict_image("canvas.png")

输入：任意尺寸的灰度/彩色图片（支持白底黑字或黑底白字，自动判断）
输出：0~9 的预测类别 + 10 类概率
"""

import argparse
import os
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np

from mnist_preprocess import prepare_canvas_digit

BASE_DIR = os.path.dirname(os.path.abspath(__file__))


def softmax(z):
    z = z - z.max(axis=1, keepdims=True)
    e = np.exp(z)
    return e / e.sum(axis=1, keepdims=True)


class DigitRecognizer:
    """把 npz 权重 + 预处理封装成一个好用的类。"""

    def __init__(self, weight_path=None, big=False):
        if weight_path is None:
            weight_path = os.path.join(BASE_DIR, "mnist_weights_big.npz" if big else "mnist_weights.npz")
        d = np.load(weight_path)
        n = len(d.files) // 2
        self.W = [d[f"arr_{i}"].astype(np.float32) for i in range(n)]
        self.b = [d[f"arr_{i + n}"].astype(np.float32) for i in range(n)]
        self.layers = [self.W[0].shape[0]] + [w.shape[1] for w in self.W]
        self.path = weight_path

    def predict_vector(self, x_784):
        """输入 (784,) 或 (1,784)，返回 (类别, 10 类概率)。"""
        x = np.asarray(x_784, dtype=np.float32).reshape(1, -1)
        a = x
        for i in range(len(self.W) - 1):
            a = np.maximum(0.0, a @ self.W[i] + self.b[i])
        z = a @ self.W[-1] + self.b[-1]
        p = softmax(z)[0]
        return int(np.argmax(p)), p

    def predict_image(self, image):
        """输入图片路径或 numpy 图像数组，返回 (类别, 10 类概率)。"""
        if isinstance(image, str):
            from PIL import Image
            image = np.asarray(Image.open(image).convert("RGBA"), dtype=np.float32) / 255.0
        vec = prepare_canvas_digit(image)
        return self.predict_vector(vec)

    def predict_proba(self, images_784):
        """批量：输入 (N,784)，返回 (N,10) 概率。"""
        x = np.asarray(images_784, dtype=np.float32)
        a = x
        for i in range(len(self.W) - 1):
            a = np.maximum(0.0, a @ self.W[i] + self.b[i])
        z = a @ self.W[-1] + self.b[-1]
        return softmax(z)


def _make_demo_canvas(seed=0):
    """合成一张"手绘画布"用于演示（白底黑字、粗笔画、偏右下）。"""
    from PIL import Image
    from sklearn.datasets import fetch_openml

    mnist = fetch_openml("mnist_784", version=1, as_frame=False)
    idx = seed % 10
    img = mnist.data[idx].reshape(28, 28).astype(np.float32) / 255.0
    up = np.asarray(Image.fromarray((img * 255).astype(np.uint8)).resize((112, 112), Image.LANCZOS),
                    dtype=np.float32) / 255.0
    padded = np.pad(up, 1)
    thick = np.zeros_like(up)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            thick = np.maximum(thick, padded[dy:dy + 112, dx:dx + 112])
    canvas = np.ones((200, 200), dtype=np.float32)
    canvas[50:50 + 112, 45:45 + 112] = 1.0 - thick
    return canvas, int(mnist.target[idx])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("image", nargs="?", help="图片路径（png/jpg）")
    ap.add_argument("--big", action="store_true", help="使用 784-512-256 权重")
    ap.add_argument("--weights", default=None, help="自定义 npz 权重路径")
    ap.add_argument("--demo", action="store_true", help="合成一张演示画布图再识别")
    args = ap.parse_args()

    rec = DigitRecognizer(args.weights, big=args.big)
    print(f"权重: {os.path.basename(rec.path)} | 架构: {rec.layers}")

    if args.demo:
        canvas, true_label = _make_demo_canvas()
        digit, probs = rec.predict_image(canvas)
        print(f"（合成画布输入，真实标签 {true_label}）")
        img = canvas
    elif args.image:
        digit, probs = rec.predict_image(args.image)
        img = args.image
    else:
        ap.print_help()
        return

    print(f"\n预测结果: {digit}")
    top3 = np.argsort(probs)[::-1][:3]
    print("置信度 Top-3:")
    for k in top3:
        print(f"  {k}: {probs[k] * 100:5.2f}%  {'█' * int(probs[k] * 40)}")

    # 可选：保存预处理后的 28x28 图，方便前端核对
    if args.demo:
        vec = prepare_canvas_digit(img)
        out = os.path.join(BASE_DIR, "improved", "demo_preprocessed.png")
        from PIL import Image
        os.makedirs(os.path.dirname(out), exist_ok=True)
        Image.fromarray((vec.reshape(28, 28) * 255).astype(np.uint8)).resize((280, 280), Image.NEAREST).save(out)
        print(f"\n预处理后的输入已保存: {out}")


if __name__ == "__main__":
    main()
