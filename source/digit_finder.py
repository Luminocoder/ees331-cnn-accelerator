"""自动找数字：不要求用户拖紧框，自己在画面里定位候选数字并给出 28x28 画布。

原来是 prepare_digit(image, roi, polarity) —— 要求 roi 已经紧紧框住一个数字，
于是只要画面里还有别的东西就报 "Multiple large components" / "Digit touches ROI
boundary"，用户点一下识别必然失败。这里把"找"和"归一化"拆开：

    find_digits(frame, roi)   -> 候选列表（每个带 bbox / 极性 / 得分 / canvas）
    render_canvas(ink, bbox)  -> 28x28 uint8（fit-20 + 质心居中）

三个设计都是压测（20 张 MNIST 渲染成纸面照片，easy/mid/hard 三档）逼出来的：

1. **只跑判出来的那一种极性**。两种都跑再按分数择优，错误极性会把纸面当墨迹，
   自适应阈值在纸纹上织出网格，那张网格的墨迹占比恰好也像数字，于是以 0.94 分
   压过真数字（img_12 的 7 被认成 2）。
2. **先渲染成 28x28，再拿这个张量本身打分**。早期版本用二值 mask 的统计量打分，
   但 render_canvas 会自己再做一次 Otsu，两者可能完全不是一回事 —— 一块橡皮的
   mask 统计很漂亮，渲染出来却是一个实心方块，而"实心方块"恰恰会被打成 3。
   现在两者走同一条路，打的就是最后要喂给 CNN 的那 784 个字节。
3. **墨迹占比的门槛要能给细笔画留活路**。`1` 这种细数字渲染后墨迹占比只有
   0.04~0.06，下限设 0.055 会把它整条丢掉，于是触发"换极性兜底"，
   兜出一个空心伪数字。

另外 render_canvas 裁剪后先做对比度拉伸再 Otsu：不拉伸的话，光照渐变会把
笔画的一侧压暗、被 Otsu 切掉，典型症状是 5 掉成 2（顶横没了）。
"""

import math

import cv2
import numpy as np

MIN_AREA_FRAC = 6.0e-4      # 小于画面这个比例的连通块当噪点
MAX_AREA_FRAC = 0.55        # 大于这个当背景/整片纸
MIN_SIDE = 9                # 太小的框缩到 20 像素后糊成一团
# 非对称：宽的不能超过 2.0（扁条是桌沿/灯管），窄的可以到 0.20（1 本来就很瘦）
MAX_ASPECT_WIDE = 2.0
MIN_ASPECT_NARROW = 0.20
MIN_FILL = 0.09             # bbox 填充率下限：木纹、纸边那种稀疏结构填空
BORDER_FRAC = 0.004
MAX_SOLID_FILL = 0.62       # bbox 填充率超过这个 = 实心块，不是笔画
MAX_STROKE_RATIO = 0.38     # 最粗处 / 短边：数字是笔画，橡皮/色块是板（0.38 是压测扫出来的拐点）
INK_TARGET = 0.19           # MNIST 28x28 的墨迹占比中位数
INK_LO, INK_HI = 0.028, 0.42   # 下限必须容得下细长的 1


def _to_gray(image):
    if image is None:
        raise ValueError("没有输入图像")
    if image.ndim == 2:
        return image.copy()
    if image.ndim == 3 and image.shape[2] == 3:
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    if image.ndim == 3 and image.shape[2] == 4:
        return cv2.cvtColor(image, cv2.COLOR_BGRA2GRAY)
    raise ValueError("只支持灰度或 BGR/BGRA 图像")


def pick_polarity(gray):
    """判"墨迹是深的还是浅的"。两票制，任一票支持深色就按深色。

    光靠 Otsu 少数派会翻车：blur + 光照渐变之后直方图不再是干净的双峰，
    少数派可能落错边（实测有 2 张），于是去试错误极性、兜出空心伪数字。
    """
    border = np.concatenate([gray[:2].ravel(), gray[-2:].ravel(),
                             gray[:, :2].ravel(), gray[:, -2:].ravel()])
    border_med = float(np.median(border))
    thr, _ = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    dark_frac = float((gray <= float(thr)).mean())

    votes_dark = 0
    if border_med >= 120:
        votes_dark += 1          # 画面边缘亮 = 纸/桌面 = 深色笔迹
    if dark_frac <= 0.5:
        votes_dark += 1          # 少数派是暗的
    return "dark" if votes_dark >= 1 else "light"


def _otsu_mask(ink):
    _thr, mask = cv2.threshold(ink, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    return mask


def _adaptive_mask(ink):
    """对光照渐变免疫。blockSize 跟着画面大小走，但不小于 15。"""
    h, w = ink.shape[:2]
    block = max(15, (min(h, w) // 12) | 1)
    return cv2.adaptiveThreshold(ink, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                 cv2.THRESH_BINARY, block, -12)


def render_canvas(ink_gray, bbox, out_size=28, fit=20):
    """把 ink 图上 bbox 那块做成 MNIST 那样 28x28（fit-20 + 质心居中）。"""
    x, y, w, h = (int(v) for v in bbox)
    pad = 2
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1 = min(ink_gray.shape[1], x + w + pad)
    y1 = min(ink_gray.shape[0], y + h + pad)
    patch = ink_gray[y0:y1, x0:x1]
    if patch.size == 0:
        raise ValueError("裁剪区域是空的")

    lo, hi = int(patch.min()), int(patch.max())
    if hi - lo < 8:
        raise ValueError("这块区域几乎没有明暗差")
    stretched = np.clip((patch.astype(np.float32) - lo) * (255.0 / (hi - lo)),
                        0, 255).astype(np.uint8)

    _thr, mask = cv2.threshold(stretched, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    ys, xs = np.nonzero(mask)
    if xs.size == 0:
        raise ValueError("这块区域里没有笔画")
    mx0, mx1 = int(xs.min()), int(xs.max()) + 1
    my0, my1 = int(ys.min()), int(ys.max()) + 1
    digit = np.where(mask[my0:my1, mx0:mx1] > 0,
                     stretched[my0:my1, mx0:mx1], 0).astype(np.uint8)
    dh, dw = digit.shape
    if max(dw, dh) < 4:
        raise ValueError("笔画太细，识别不了")

    scale = fit / float(max(dw, dh))
    tw, th = max(1, int(round(dw * scale))), max(1, int(round(dh * scale)))
    interp = cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR
    resized = cv2.resize(digit, (tw, th), interpolation=interp)
    canvas = np.zeros((out_size, out_size), np.uint8)
    left, top = (out_size - tw) // 2, (out_size - th) // 2
    canvas[top:top + th, left:left + tw] = resized

    m = cv2.moments(canvas)
    if m["m00"] <= 0:
        raise ValueError("缩到 28x28 后笔画消失了")
    dx = int(round((out_size / 2 - 0.5) - m["m10"] / m["m00"]))
    dy = int(round((out_size / 2 - 0.5) - m["m01"] / m["m00"]))
    ys, xs = np.nonzero(canvas)
    dx = max(-int(xs.min()), min(dx, out_size - 1 - int(xs.max())))
    dy = max(-int(ys.min()), min(dy, out_size - 1 - int(ys.max())))
    canvas = cv2.warpAffine(canvas, np.float32([[1, 0, dx], [0, 1, dy]]),
                            (out_size, out_size), flags=cv2.INTER_NEAREST,
                            borderValue=0)
    return canvas


def _raw_boxes(mask, min_area, max_area, border_x, border_y):
    """返回 (labels 图, 候选元组列表)。caller 必须拿 labels 图取像素，
    拿二值 mask 去比 label_id 会永远是空的（这个坑踩过一次）。"""
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    h, w = mask.shape[:2]
    out = []
    for i in range(1, count):
        x, y, bw, bh, area = (int(v) for v in stats[i])
        if area < min_area or area > max_area:
            continue
        if bw < MIN_SIDE or bh < MIN_SIDE:
            continue
        aspect = bw / float(bh)
        if aspect > MAX_ASPECT_WIDE or aspect < MIN_ASPECT_NARROW:
            continue
        if area / float(bw * bh) < MIN_FILL:
            continue
        touches = (x <= border_x) + (y <= border_y) + (
            x + bw >= w - border_x) + (y + bh >= h - border_y)
        if touches >= 3:
            continue                      # 自己就是一整幅 → 纸/背景
        out.append((i, x, y, bw, bh, area, touches))
    return labels, out


def _judge(ink_full, bbox_full, labels, label_id, box_small):
    """候选的最终裁决：渲染成 28x28，只看这个张量像不像 MNIST 数字。"""
    x, y, bw, bh, area, touches = box_small
    fill = area / float(bw * bh)
    if fill > MAX_SOLID_FILL:
        return None

    sub = np.where(labels[y:y + bh, x:x + bw] == label_id, 255, 0).astype(np.uint8)
    ys, xs = np.nonzero(sub)
    if xs.size == 0:
        return None
    tw = int(xs.max()) - int(xs.min()) + 1
    th = int(ys.max()) - int(ys.min()) + 1
    dist = cv2.distanceTransform(sub, cv2.DIST_L2, 5)
    stroke_ratio = float(dist.max()) / float(max(1, min(tw, th)))
    if stroke_ratio > MAX_STROKE_RATIO:
        return None

    try:
        canvas = render_canvas(ink_full, bbox_full)
    except ValueError:
        return None
    binary = (canvas > 96).astype(np.uint8)
    ink_frac = float(cv2.countNonZero(binary)) / float(binary.size)
    if not INK_LO <= ink_frac <= INK_HI:
        return None
    fy, fx = np.nonzero(binary)
    fw, fh = int(fx.max()-fx.min()+1), int(fy.max()-fy.min()+1)
    final_fill = int(binary.sum()) / float(fw*fh)
    if final_fill > 0.82 and fw / float(fh) > 0.25:
        return None  # Adaptive masks can turn solid objects into plausible outlines.

    aspect = bbox_full[2] / float(bbox_full[3])
    sq_ness = min(aspect, 1.0 / aspect)
    ink_pref = math.exp(-((ink_frac - INK_TARGET) ** 2) / (2 * 0.10 ** 2))
    stroke_pref = max(0.0, 1.0 - stroke_ratio / MAX_STROKE_RATIO)
    score = (0.38 * sq_ness + 0.34 * ink_pref + 0.16 * stroke_pref
             + 0.12 * min(fill / 0.30, 1.0) - 0.18 * touches)
    if score <= 0:
        return None
    return {"score": float(score), "bbox": bbox_full, "ink_fraction": ink_frac,
            "bbox_fill": fill, "stroke_ratio": stroke_ratio, "canvas": canvas}


def find_digits(image, roi=None, polarity="auto", max_side=720):
    """在画面（或 ROI 内）找像数字的块，按得分降序返回。"""
    gray_full = _to_gray(image)
    offset = (0, 0)
    gray = gray_full
    if roi is not None:
        x, y, w, h = (int(v) for v in roi)
        x, y = max(0, x), max(0, y)
        w = max(1, min(w, gray_full.shape[1] - x))
        h = max(1, min(h, gray_full.shape[0] - y))
        gray = gray_full[y:y + h, x:x + w]
        offset = (x, y)

    if gray.size == 0 or min(gray.shape[:2]) < 12:
        raise ValueError("ROI 太小，什么也放不下")
    if int(gray.max()) - int(gray.min()) < 10:
        raise ValueError("画面几乎没有明暗差（mean=%.0f），先开灯或调亮" % float(gray.mean()))

    scale = 1.0
    longest = max(gray.shape[:2])
    if longest > max_side:
        scale = max_side / float(longest)
    small = cv2.resize(gray, (max(1, int(gray.shape[1] * scale)),
                              max(1, int(gray.shape[0] * scale))),
                       interpolation=cv2.INTER_AREA)
    small = cv2.GaussianBlur(small, (3, 3), 0)

    h, w = small.shape[:2]
    frame_area = float(h * w)
    min_area = max(24.0, frame_area * MIN_AREA_FRAC)
    max_area = frame_area * MAX_AREA_FRAC
    border_x = max(2, int(w * BORDER_FRAC))
    border_y = max(2, int(h * BORDER_FRAC))

    if polarity == "auto":
        first = pick_polarity(small)
        order = [first]  # Do not reinterpret background as a digit after rejection.
    else:
        order = [polarity]

    kernel = np.ones((3, 3), np.uint8)
    tried = []
    for pol in order:
        ink_small = cv2.subtract(255, small) if pol == "dark" else small
        # bbox_full is in frame coordinates, including a nonzero ROI offset.
        ink_full = cv2.subtract(255, gray_full) if pol == "dark" else gray_full
        scored, seen = [], set()
        for maker in (_otsu_mask, _adaptive_mask):
            mask = maker(ink_small)
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, kernel, iterations=1)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=2)
            frac = cv2.countNonZero(mask) / frame_area
            if not 2.0e-4 <= frac <= 0.80:
                continue
            labels, boxes = _raw_boxes(mask, min_area, max_area, border_x, border_y)
            for box in boxes:
                label_id, x, y, bw, bh, area, touches = box
                bbox_full = (int(round(offset[0] + x / scale)),
                             int(round(offset[1] + y / scale)),
                             max(1, int(round(bw / scale))),
                             max(1, int(round(bh / scale))))
                key = (bbox_full[0] // 6, bbox_full[1] // 6,
                       bbox_full[2] // 6, bbox_full[3] // 6)
                if key in seen:
                    continue
                item = _judge(ink_full, bbox_full, labels, label_id, box[1:])
                if item is None:
                    continue
                # 只在"通过判据"之后才登记去重。登记在判据之前的话，Otsu 版本
                # 被判掉会把紧随其后、指标合格的 adaptive 版本当重复跳过 ——
                # 细长的 1 就是这么被整条丢掉的（Otsu 0.348 卡线，adaptive 0.296 合格）。
                seen.add(key)
                item["polarity"] = pol
                scored.append(item)
        if scored:
            scored.sort(key=lambda c: c["score"], reverse=True)
            # Otsu and adaptive thresholding can describe the same digit with
            # slightly different boxes. Keep one physical target, not two digits.
            unique = []
            for item in scored:
                a = item["bbox"]
                duplicate = False
                for other in unique:
                    b = other["bbox"]
                    iw = max(0, min(a[0]+a[2], b[0]+b[2])-max(a[0], b[0]))
                    ih = max(0, min(a[1]+a[3], b[1]+b[3])-max(a[1], b[1]))
                    intersection = iw * ih
                    if intersection / float(min(a[2]*a[3], b[2]*b[3])) > 0.65:
                        duplicate = True
                        break
                if not duplicate:
                    unique.append(item)
            return unique
        tried.append(pol)

    raise ValueError(
        "画面里没找到像手写数字的东西（试过极性 %s）。把数字写大一点、靠近镜头、"
        "白纸黑字并让纸占画面一半以上；或直接用『打开图片…』" % "/".join(tried))


def ink_image(gray, polarity):
    """dark=白纸黑字（要反相），light=黑底白字（不反相）。"""
    return cv2.subtract(255, gray) if polarity == "dark" else gray.copy()


def prepare_one(image, roi=None, polarity="auto"):
    """一步到位：找最像数字的那个，做成 28x28。返回 (canvas, meta)。"""
    cands = find_digits(image, roi, polarity)
    best = cands[0]
    meta = {
        "polarity": best["polarity"],
        "bbox": best["bbox"],
        "score": best["score"],
        "ink_fraction": best["ink_fraction"],
        "stroke_ratio": best["stroke_ratio"],
        "candidates": len(cands),
        "roi_xywh": roi,
        "runner_up": cands[1]["score"] if len(cands) > 1 else None,
    }
    return best["canvas"].copy(), meta


def prepare_two(image, roi=None, polarity="auto"):
    """两位数：在候选里挑两个横向分开、尺寸相近的。"""
    cands = find_digits(image, roi, polarity)
    best = cands[0]
    b0w, b0h = best["bbox"][2], best["bbox"][3]
    height_ref = max(b0h, 1)
    second = None
    for c in cands[1:]:
        cw, ch = c["bbox"][2], c["bbox"][3]
        if cw * ch < (b0w * b0h) * 0.25:
            continue
        if not 0.45 <= ch / float(height_ref) <= 2.2:
            continue
        if abs(c["score"] - best["score"]) > 0.45:
            continue
        # 必须横向错开，不能是同一个数字里的两个部件
        gap = abs(c["bbox"][0] - best["bbox"][0])
        if gap < 0.3 * max(b0w, cw):
            continue
        second = c
        break
    if second is None:
        raise ValueError("只找到一个数字；两位数模式需要两个并排的数字")
    pair = sorted([best, second], key=lambda c: c["bbox"][0])
    return [{"canvas": c["canvas"].copy(), "bbox": c["bbox"],
             "score": c["score"]} for c in pair]
