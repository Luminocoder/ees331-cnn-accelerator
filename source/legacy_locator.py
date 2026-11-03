"""Locate components using our v2.9 foreground and stroke normalization.

No CNN/MLP score participates in finding, selecting or rejecting a component.
The original v2.9 prepare_digit remains in mnist_camera_input as the ROI oracle.
"""
import math
import cv2
import numpy as np

ALGORITHM = 'legacy-v29-local-background-stroke225-auto-v51'


def gray_image(image):
    if image.ndim == 2:
        return image.copy()
    if image.ndim == 3 and image.shape[2] in (3, 4):
        return cv2.cvtColor(image, cv2.COLOR_BGR2GRAY if image.shape[2] == 3 else cv2.COLOR_BGRA2GRAY)
    raise ValueError('只支持灰度或 BGR/BGRA 图像')


def normalize_component(mask):
    """Original v2.9 padded stroke2.25/fit20 normalization, isolated foreground."""
    x, y, width, height = cv2.boundingRect(mask)
    if not width or not height:
        raise ValueError('没有可用笔画')
    foreground = mask
    distance = cv2.distanceTransform(foreground, cv2.DIST_L2, 5)
    ridge = (distance > 0) & (distance >= cv2.dilate(distance, np.ones((3, 3), np.uint8)) - 1e-5)
    stroke = max(1.0, float(2 * np.median(distance[ridge]) - 1))
    scale = 20 / max(width, height)
    target_stroke = 2.25
    radius = max(0, math.ceil((target_stroke / scale - stroke) / 2))
    radius = min(radius, math.ceil(max(width, height) * .06))
    padding = radius + 2
    foreground = cv2.copyMakeBorder(foreground[y:y + height, x:x + width],
                                   padding, padding, padding, padding,
                                   cv2.BORDER_CONSTANT, value=0)
    if radius:
        foreground = cv2.dilate(foreground, cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, (2 * radius + 1, 2 * radius + 1)))
    bx, by, bw, bh = cv2.boundingRect(foreground)
    digit = foreground[by:by + bh, bx:bx + bw]
    scale = 20 / max(bw, bh)
    sw, sh = max(1, round(bw * scale)), max(1, round(bh * scale))
    resized = cv2.resize(digit, (sw, sh), interpolation=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR)
    canvas = np.zeros((28, 28), np.uint8)
    left, top = (28 - sw) // 2, (28 - sh) // 2
    canvas[top:top + sh, left:left + sw] = resized
    m = cv2.moments(canvas)
    if m['m00'] <= 0:
        raise ValueError('缩小后没有可用笔画')
    dx, dy = round(13.5 - m['m10'] / m['m00']), round(13.5 - m['m01'] / m['m00'])
    ys, xs = np.nonzero(canvas)
    dx = max(-int(xs.min()), min(dx, 27 - int(xs.max())))
    dy = max(-int(ys.min()), min(dy, 27 - int(ys.max())))
    canvas = cv2.warpAffine(canvas, np.float32([[1, 0, dx], [0, 1, dy]]),
                            (28, 28), flags=cv2.INTER_NEAREST, borderValue=0)
    return canvas, foreground, dict(stroke_width_source_px=stroke,
        stroke_dilation_radius_px=radius, target_stroke_width_28px=target_stroke,
        normalization_padding_px=padding, center_shift_xy=[dx, dy])


def find(image, roi=None, _threshold=None):
    gray = gray_image(image)
    x0, y0, rw, rh = roi or (0, 0, gray.shape[1], gray.shape[0])
    crop = gray[y0:y0+rh, x0:x0+rw]
    if not crop.size or int(crop.max())-int(crop.min()) < 8:
        raise ValueError('画面没有足够明暗差，未检测到数字')
    border = np.concatenate([crop[:2].ravel(), crop[-2:].ravel(), crop[:, :2].ravel(), crop[:, -2:].ravel()])
    # Our camera workflow is black ink on paper. Shadows must not flip polarity.
    # Only an actually dark canvas enables the inverted-input convention.
    polarity = 'light' if float(np.median(border)) < 48 and float(np.median(crop)) < 64 else 'dark'
    ink = 255-crop if polarity == 'dark' else crop.copy()
    kernel_size = max(31, min(61, min(crop.shape) // 4 | 1))
    background = cv2.morphologyEx(ink, cv2.MORPH_OPEN,
        cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (kernel_size, kernel_size)))
    contrast = cv2.subtract(ink, background)
    otsu, _ = cv2.threshold(contrast, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    median = float(np.median(contrast))
    noise_mad = float(np.median(np.abs(contrast.astype(np.float32)-median)))
    # A table edge can dominate full-frame Otsu. Detect strokes with the original
    # noise floor; normalization still consumes the isolated v2.9 binary label.
    if _threshold is None:
        proposals,rejected=[],[]
        levels=sorted(set([12.0,24.0,48.0,max(12.0,otsu,median+8*1.4826*noise_mad)]))
        for level in levels:
            found,discarded=find(image,roi,_threshold=level)
            proposals.extend(found)
            rejected.extend(discarded)
        # Prefer a complete connected stroke over its fragments from a higher
        # threshold. This decision uses geometry only, never model predictions.
        proposals.sort(key=lambda c:c['bbox'][2]*c['bbox'][3],reverse=True)
        unique=[]
        for candidate in proposals:
            a=candidate['bbox']
            duplicate=False
            for other in unique:
                b=other['bbox']
                iw=max(0,min(a[0]+a[2],b[0]+b[2])-max(a[0],b[0]))
                ih=max(0,min(a[1]+a[3],b[1]+b[3])-max(a[1],b[1]))
                if iw*ih/max(1,min(a[2]*a[3],b[2]*b[3]))>.70:
                    duplicate=True
                    break
            if not duplicate:
                unique.append(candidate)
        if unique:
            largest=max(unique,key=lambda c:c['area'])
            unique=[c for c in unique if not (c['area']<largest['area']*.20 and (
                c['bbox'][3]<largest['bbox'][3]*.45 or (c['bbox'][2]/c['bbox'][3]<.12
                and c['bbox'][2]<largest['bbox'][2]*.35)))]
        return sorted(unique,key=lambda c:c['bbox'][0]),rejected
    threshold = float(_threshold)
    mask = (contrast > threshold).astype(np.uint8)*255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    minimum_area = max(8, math.ceil(mask.size*.0001))
    candidates, rejected = [], []
    for label in range(1, count):
        x, y, w, h, area = map(int, stats[label])
        if area < minimum_area:
            continue
        reasons = []
        if x <= 1 or y <= 1 or x+w >= rw-1 or y+h >= rh-1:
            reasons.append('search_boundary')
        if h < max(9, rh*.10) or w < 2:
            reasons.append('too_small')
        if not .012 <= w/float(h) <= 2.0:
            reasons.append('aspect')
        # Solid patches have broad strokes; a narrow handwritten 1 is allowed.
        component = np.where(labels[y:y+h, x:x+w] == label, 255, 0).astype(np.uint8)
        distance = cv2.distanceTransform(component, cv2.DIST_L2, 5)
        broad = float(distance.max())*2/max(w,h)
        if broad > .55 or (area/float(w*h) > .86 and w/h > .25):
            reasons.append('solid_patch')
        if w/h < .25 and x > 0 and x+w < rw:
            margin=max(4,min(24,w))
            left=float(np.median(crop[y:y+h,max(0,x-margin):x]))
            right=float(np.median(crop[y:y+h,x+w:min(rw,x+w+margin)]))
            center=float(np.median(crop[y:y+h,x:x+w][component>0]))
            if polarity=='light':
                left,right,center=255-left,255-right,255-center
            if abs(left-right)>max(12.0,(max(left,right)-center)*.45):
                reasons.append('one_sided_paper_edge')
        if reasons:
            rejected.append(dict(bbox=[x+x0,y+y0,w,h],area=area,reasons=reasons))
            continue
        # Local Otsu avoids table edges setting the digit's threshold. Restrict
        # refinement to this detected label, so background cannot re-enter.
        pad=max(4,min(24,round(max(w,h)*.05)))
        px0,py0=max(0,x-pad),max(0,y-pad)
        px1,py1=min(rw,x+w+pad),min(rh,y+h+pad)
        local=contrast[py0:py1,px0:px1]
        local_otsu,_=cv2.threshold(local,0,255,cv2.THRESH_BINARY|cv2.THRESH_OTSU)
        local_median=float(np.median(local))
        local_mad=float(np.median(np.abs(local.astype(np.float32)-local_median)))
        local_threshold=max(12.0,local_otsu,local_median+8*1.4826*local_mad)
        isolated=((labels==label)&(contrast>local_threshold)).astype(np.uint8)*255
        isolated=cv2.morphologyEx(isolated,cv2.MORPH_CLOSE,np.ones((3,3),np.uint8))
        if cv2.countNonZero(isolated)<minimum_area:
            rejected.append(dict(bbox=[x+x0,y+y0,w,h],area=area,reasons=['weak_local_foreground']))
            continue
        lx,ly,lw,lh=cv2.boundingRect(isolated)
        if lw<=1 or lh<max(9,rh*.10):
            continue
        canvas, foreground, normalization = normalize_component(isolated)
        candidates.append(dict(bbox=[lx+x0,ly+y0,lw,lh],canvas=canvas,
            foreground=foreground,polarity=polarity,area=int(cv2.countNonZero(isolated)),
            ink_fraction=float((canvas>96).mean()),otsu_threshold=float(otsu),
            contrast_threshold=float(local_threshold),background_kernel_size=kernel_size,
            background_noise_mad=noise_mad,normalization=normalization))
    if candidates:
        # Ignore minor marks only when a clearly larger target exists.
        largest = max(candidates,key=lambda c:c['area'])
        major = []
        for c in candidates:
            if c['area'] < largest['area']*.20 and (c['bbox'][3] < largest['bbox'][3]*.45
                    or (c['bbox'][2]/c['bbox'][3]<.12 and c['bbox'][2]<largest['bbox'][2]*.35)):
                rejected.append(dict(bbox=c['bbox'],area=c['area'],reasons=['minor_mark']))
            else:
                major.append(c)
        candidates = major
    return sorted(candidates,key=lambda c:c['bbox'][0]), rejected
