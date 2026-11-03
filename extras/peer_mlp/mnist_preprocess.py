"""
MNIST 前端输入预处理（画布/图片 → 784 维模型输入）

为什么需要这一步：
MNIST 训练图的特点非常"苛刻"：
  1. 黑底白字（数值 1 = 笔画，0 = 背景）
  2. 数字被裁剪到外接矩形后，缩放到 20x20，再"按重心"居中放进 28x28
  3. 笔画粗细约 2~3 像素（抗锯齿灰度）

而网页/画布上手写的数字通常是：白底黑字、居中方式靠鼠标位置、笔画粗得多、尺寸不定。
如果把画布原图直接拉成 28x28 送进网络，准确率会从 98% 掉到 60% 以下 —— 这不是模型不行，
而是"喂进去的东西和训练时的分布不一样"。

本模块的 prepare_canvas_digit() 把任意画布灰度图变成和 MNIST 同分布的张量，
配合训练时做过平移/加粗增强的权重，手写输入才稳。

用法（Python 后端）：
    from mnist_preprocess import prepare_canvas_digit
    vec = prepare_canvas_digit(png_or_array)   # -> shape (784,), float32, 0~1
    logits = ...                               # 喂给模型

用法（前端自己算）：直接照抄 prepare_canvas_digit 里的 5 步算法即可，与语言无关。
"""

import numpy as np

SIZE = 28
BOX = 20  # MNIST 官方做法：先缩放数字到 20x20，留 4 像素边距


# ----------------------------------------------------------------------
# 1. 转灰度 + 自动判断极性（白底黑字 -> 黑底白字）
# ----------------------------------------------------------------------
def _to_gray_float01(img):
    """把 HxW / HxWx3 / HxWx4 的 uint8 或 0~1 float 图像转成 HxW float32 (0~1)。"""
    arr = np.asarray(img)
    if arr.dtype == np.uint8 or arr.max() > 1.5:
        arr = arr.astype(np.float32) / 255.0
    else:
        arr = arr.astype(np.float32)

    if arr.ndim == 3:
        if arr.shape[2] == 4:      # RGBA：先按 alpha 合成到黑底
            rgb, a = arr[..., :3], arr[..., 3:4]
            arr = rgb * a
        if arr.shape[2] >= 3:      # RGB 取平均
            arr = arr[..., :3].mean(axis=2)
    return arr


def _is_light_background(gray):
    """用四周边框像素判断背景是亮还是暗。

    画布常见的两种情况：
      - 白底黑字：边框接近 1.0
      - 黑底白字：边框接近 0.0
    用边框而不是全图均值，避免"数字占满画布"时误判。
    """
    h, w = gray.shape
    border = np.concatenate([
        gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]
    ])
    return float(border.mean()) > 0.5


def preprocess(img):
    """核心流程：极性统一 -> 裁剪 -> 缩放 -> 重心居中。返回 (28,28) float32。"""
    gray = _to_gray_float01(img)

    # (1) 统一成"黑底白字"
    if _is_light_background(gray):
        gray = 1.0 - gray

    # (2) 按阈值裁掉空白边（阈值取最大亮度的 20%，兼容抗锯齿的灰边）
    ink = gray > (gray.max() * 0.2) if gray.max() > 1e-6 else gray > 0
    if not ink.any():                     # 全空画布：返回全 0
        return np.zeros((SIZE, SIZE), dtype=np.float32)
    ys, xs = np.where(ink)
    crop = gray[ys.min():ys.max() + 1, xs.min():xs.max() + 1]

    # (3) 等比缩放到最长边 = 20 像素（用 PIL 的高质量重采样）
    crop = _resize_to_box(crop, BOX)

    # (4) 放进 28x28 中央
    canvas = np.zeros((SIZE, SIZE), dtype=np.float32)
    h, w = crop.shape
    y0 = (SIZE - h) // 2
    x0 = (SIZE - w) // 2
    canvas[y0:y0 + h, x0:x0 + w] = crop

    # (5) 按重心平移居中（MNIST 的官方预处理，比几何居中更稳）
    canvas = _shift_by_center_of_mass(canvas)
    return canvas


def _resize_to_box(crop, box):
    """把裁剪后的数字等比缩放到最长边 = box，返回 float32。"""
    h, w = crop.shape
    scale = box / max(h, w)
    nh, nw = max(1, int(round(h * scale))), max(1, int(round(w * scale)))
    try:
        from PIL import Image
        im = Image.fromarray((crop * 255).astype(np.uint8), mode="L")
        im = im.resize((nw, nh), Image.LANCZOS)
        return np.asarray(im, dtype=np.float32) / 255.0
    except Exception:
        # 没有 PIL 时退化为最近邻
        yi = (np.arange(nh) * h / nh).astype(int).clip(0, h - 1)
        xi = (np.arange(nw) * w / nw).astype(int).clip(0, w - 1)
        return crop[yi][:, xi]


def _shift_by_center_of_mass(canvas):
    """计算墨迹重心，平移使重心落在 (14,14) 附近。"""
    total = canvas.sum()
    if total < 1e-6:
        return canvas
    ys, xs = np.mgrid[0:SIZE, 0:SIZE]
    cy = float((ys * canvas).sum() / total)
    cx = float((xs * canvas).sum() / total)
    dy = int(round(SIZE / 2.0 - 0.5 - cy))
    dx = int(round(SIZE / 2.0 - 0.5 - cx))
    return np.roll(np.roll(canvas, dy, axis=0), dx, axis=1)


def prepare_canvas_digit(img):
    """对外接口：任意画布图片 -> (784,) float32 向量（0~1，行优先展平）。"""
    return preprocess(img).reshape(-1).astype(np.float32)


def prepare_batch(imgs):
    """批量版：list/np.ndarray of images -> (N,784)。"""
    return np.stack([prepare_canvas_digit(im) for im in imgs]).astype(np.float32)


# ----------------------------------------------------------------------
# 2. 与前端对齐时的自检工具
# ----------------------------------------------------------------------
def visualize_pipeline(img, save_path=None):
    """把"原始画布 -> 裁剪 -> 缩放 -> 居中"四步画出来，方便调试前端。
    需要 matplotlib；不传 save_path 时直接弹窗。"""
    import matplotlib.pyplot as plt

    gray = _to_gray_float01(img)
    if _is_light_background(gray):
        gray = 1.0 - gray
    ink = gray > (gray.max() * 0.2) if gray.max() > 1e-6 else gray > 0
    ys, xs = np.where(ink)
    crop = gray[ys.min():ys.max() + 1, xs.min():xs.max() + 1] if ink.any() else gray
    small = _resize_to_box(crop, BOX)
    final = prepare_canvas_digit(img).reshape(SIZE, SIZE)

    fig, axes = plt.subplots(1, 4, figsize=(12, 3.2))
    for ax, data, title in zip(
            axes,
            [gray, gray[ys.min():ys.max() + 1, xs.min():xs.max() + 1] if ink.any() else gray, small, final],
            ["1. Raw (inverted if needed)", "2. Crop to ink", f"3. Scale to {BOX}x{BOX}", "4. Center in 28x28"]):
        ax.imshow(data, cmap="gray", vmin=0, vmax=1)
        ax.set_title(title, fontsize=9)
        ax.axis("off")
    fig.tight_layout()
    if save_path:
        fig.savefig(save_path, dpi=150)
        plt.close(fig)
    else:
        plt.show()


if __name__ == "__main__":
    # 自检：造一个"白底黑字、粗笔画、偏右下"的画布数字，看看处理后的效果
    from PIL import Image
    from sklearn.datasets import fetch_openml

    mnist = fetch_openml("mnist_784", version=1, as_frame=False)
    img = mnist.data[0].reshape(28, 28).astype(np.float32) / 255.0  # 黑底白字

    # 模拟画布：放大到 112x112 -> 加粗 -> 白底黑字 -> 贴到 160x160 画布的偏右下位置
    up = np.asarray(Image.fromarray((img * 255).astype(np.uint8)).resize((112, 112), Image.LANCZOS),
                    dtype=np.float32) / 255.0
    padded = np.pad(up, 1)
    thick = np.zeros_like(up)
    for dy in (0, 1, 2):
        for dx in (0, 1, 2):
            thick = np.maximum(thick, padded[dy:dy + 112, dx:dx + 112])

    canvas = np.ones((170, 170), dtype=np.float32)          # 白底
    canvas[45:45 + 112, 40:40 + 112] = 1.0 - thick          # 黑字，偏右下

    vec = prepare_canvas_digit(canvas)
    print("输出形状:", vec.shape, "| 取值范围:", float(vec.min()), "~", float(vec.max()))
    print("预处理后的 28x28 图（# 表示墨迹）:")
    for row in (vec.reshape(28, 28) > 0.3):
        print("".join("#" if v else "." for v in row))

    visualize_pipeline(canvas, save_path="preprocess_demo.png")
    print("\n已保存预处理流程示意图: preprocess_demo.png")
