"""Image-only centering before the single CNN request; no logits or labels."""
import cv2
import numpy as np

ALGORITHM = 'subpixel-centroid14-v1'


def center_input(canvas):
    if canvas.shape != (28, 28) or canvas.dtype != np.uint8:
        raise ValueError('需要 28x28 uint8 输入')
    moments = cv2.moments(canvas)
    if moments['m00'] <= 0:
        raise ValueError('没有可居中的前景')
    cx, cy = moments['m10'] / moments['m00'], moments['m01'] / moments['m00']
    dx, dy = 14.0 - cx, 14.0 - cy
    ys, xs = np.nonzero(canvas)
    # Keep every foreground pixel inside the tensor, including unusual inputs.
    tx = float(np.clip(dx, -int(xs.min()), 27 - int(xs.max())))
    ty = float(np.clip(dy, -int(ys.min()), 27 - int(ys.max())))
    result = cv2.warpAffine(canvas, np.float32([[1, 0, tx], [0, 1, ty]]),
                            (28, 28), flags=cv2.INTER_LINEAR, borderValue=0)
    return result, dict(normalization_refinement=ALGORITHM,
        input_center_before_xy=[cx, cy], target_center_xy=[14.0, 14.0],
        subpixel_center_shift_xy=[tx, ty], center_shift_clamped=(tx != dx or ty != dy),
        center_interpolation='bilinear')
