"""Fixed camera guides using the original v2.9 ROI preparation verbatim."""
import hashlib
import numpy as np
import mnist_camera_input as original

ALGORITHM='original-v29-fixed-guides-demo-v51'


def regions(shape,mode='single',roi=None):
    h,w=shape[:2]
    if mode not in ('single','two'):
        raise ValueError('模式必须是一位或两位')
    if roi is not None:
        x,y,rw,rh=map(int,roi)
        if min(x,y)<0 or min(rw,rh)<12 or x+rw>w or y+rh>h:
            raise ValueError('取景区域超出画面或太小')
    elif mode=='single':
        rw=rh=max(12,int(min(h,w)*.8))
        x,y=(w-rw)//2,(h-rh)//2
    else:
        rh=max(12,int(h*.8))
        rw=max(28,min(int(w*.84),int(h*1.6)))
        x,y=(w-rw)//2,(h-rh)//2
    if mode=='single':
        return [(x,y,rw,rh)]
    gap=max(4,int(rw*.025))
    side=(rw-gap)//2
    if side<12:
        raise ValueError('画面过小，无法分成两个取景框')
    return [(x,y,side,rh),(x+side+gap,y,rw-side-gap,rh)]


def prepare(frame,mode='single',roi=None):
    if frame is None or frame.dtype!=np.uint8 or frame.size==0:
        raise ValueError('需要非空 uint8 图像')
    items=[]
    for index,box in enumerate(regions(frame.shape,mode,roi)):
        try:
            canvas,foreground,meta=original.prepare_digit(frame,box,'dark')
        except ValueError as error:
            name='取景框' if mode=='single' else ('左框' if index==0 else '右框')
            raise ValueError(name+'：'+str(error)+'；每框放一个完整黑字，四周留白') from error
        x,y,w,h=meta['detected_bbox_in_roi_xywh']
        meta=dict(meta,algorithm=ALGORITHM,legacy_algorithm=meta['algorithm'],
            bbox=[box[0]+x,box[1]+y,w,h],foreground=foreground,
            guide_roi_xywh=list(box),guide_index=index,candidate_count=len(regions(frame.shape,mode,roi)),
            warnings=[],pixels_sha256=hashlib.sha256(canvas.tobytes()).hexdigest())
        items.append(dict(canvas=canvas,meta=meta,variants=[dict(fit=20,canvas=canvas.copy())]))
    return items
