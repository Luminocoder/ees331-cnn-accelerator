"""Original v2.9 input normalization with automatic single/two-digit localization."""
import hashlib
import cv2
import numpy as np
import legacy_locator as detector

ALGORITHM = detector.ALGORITHM


def prepare(frame, mode='single', roi=None):
    if frame is None or frame.dtype != np.uint8 or frame.size == 0:
        raise ValueError('需要非空 uint8 图像')
    if mode not in ('single','two'):
        raise ValueError('识别模式必须是一位或两位')
    gray=detector.gray_image(frame)
    if roi is not None:
        x,y,w,h=map(int,roi)
        if min(x,y)<0 or min(w,h)<12 or x+w>gray.shape[1] or y+h>gray.shape[0]:
            raise ValueError('辅助区域超出图像或太小')
        roi=(x,y,w,h)
    candidates,rejected=detector.find(frame,roi)
    expected=2 if mode=='two' else 1
    if len(candidates)!=expected:
        raise ValueError('检测到 %d 个完整目标，当前需要 %d 个数字；请检查纸卡、干扰或一位/两位模式'
                         %(len(candidates),expected))
    if mode=='two':
        a,b=(c['bbox'] for c in candidates)
        overlap_y=max(0,min(a[1]+a[3],b[1]+b[3])-max(a[1],b[1]))
        overlap_x=max(0,min(a[0]+a[2],b[0]+b[2])-max(a[0],b[0]))
        if overlap_y<min(a[3],b[3])*.35 or overlap_x>min(a[2],b[2])*.25 or not .4<=a[3]/b[3]<=2.5:
            raise ValueError('两个目标没有形成完整的左右数字，请分开摆放')
    items=[]
    for c in candidates:
        x,y,w,h=c['bbox']
        sharpness=float(cv2.Laplacian(gray[y:y+h,x:x+w],cv2.CV_64F).var())
        canvas=c['canvas']
        meta=dict(algorithm=ALGORITHM,bbox=c['bbox'],roi_xywh=list(roi) if roi else None,
            polarity=c['polarity'],foreground=c['foreground'],
            foreground_fraction=c['ink_fraction'],component_area=c['area'],
            otsu_threshold=c['otsu_threshold'],contrast_threshold=c['contrast_threshold'],
            background_kernel_size=c['background_kernel_size'],background_noise_mad=c['background_noise_mad'],
            candidate_count=len(candidates),rejected_components=rejected,
            laplacian_variance=sharpness,warnings=[],
            pixels_sha256=hashlib.sha256(canvas.tobytes()).hexdigest(),**c['normalization'])
        items.append(dict(canvas=canvas,meta=meta,variants=[dict(fit=20,canvas=canvas.copy())]))
    return items


def assess_logits(logits):
    values=[int(v) for v in logits]
    if len(values)!=10:
        raise ValueError('需要 10 路分数')
    order=sorted(range(10),key=lambda i:values[i],reverse=True)
    gap=values[order[0]]-values[order[1]]
    ratio=gap/float(max(1,max(abs(v) for v in values)))
    return dict(top1=order[0],top2=order[1],gap=gap,relative_gap=ratio,
                needs_review=ratio<.10,calibrated_probability=False)
