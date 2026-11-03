"""Public image pipeline: our original input normalization and automatic locator."""
from legacy_pipeline import ALGORITHM, prepare as automatic_prepare, assess_logits
import guided_capture
import hashlib
from camera_normalization import center_input


def prepare(frame,mode='single',roi=None,capture_mode='auto'):
    if capture_mode=='guided':
        items = guided_capture.prepare(frame,mode,roi)
    elif capture_mode=='auto':
        items = automatic_prepare(frame,mode,roi)
    else:
        raise ValueError('拍摄方式必须是 guided 或 auto')
    for item in items:
        canvas, refinement = center_input(item['canvas'])
        item['canvas'] = canvas
        item['variants'] = [dict(fit=20, canvas=canvas.copy())]
        item['meta'].update(refinement)
        item['meta']['algorithm'] += '+' + refinement['normalization_refinement']
        item['meta']['pixels_sha256'] = hashlib.sha256(canvas.tobytes()).hexdigest()
    return items
