"""Extra full-network RTL inputs; reference is the unchanged Python INT8 model."""
from pathlib import Path
import hashlib
import json
import sys
import numpy as np

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'source'))
import offline_ref

def main():
    rng=np.random.default_rng(33120261101)
    border=np.zeros((28,28),np.uint8)
    border[0,:]=255;border[-1,:]=255;border[:,0]=255;border[:,-1]=255
    inputs=[('all_zero',np.zeros((28,28),np.uint8)),
            ('all_255',np.full((28,28),255,np.uint8)),('border',border),
            ('random_1',rng.integers(0,256,(28,28),np.uint8)),
            ('random_2',rng.integers(0,256,(28,28),np.uint8))]
    out=ROOT/'hardware/pl/ref_model/test_vectors'
    pixels=[];scores=[];labels=[];records=[]
    for name,image in inputs:
        digit,logits=offline_ref.infer(image,ROOT/'resources')
        pixels.extend(f'{int(v):02x}' for v in image.flat)
        scores.extend(f'{v&0xffffffff:08x}' for v in logits)
        labels.append(f'{digit:02x}')
        records.append({'name':name,'uint8_sha256':hashlib.sha256(image.tobytes()).hexdigest(),
                        'digit':digit,'logits':logits})
    for name,lines in [('conv_extra_images.hex',pixels),('conv_extra_logits.hex',scores),('conv_extra_labels.hex',labels)]:
        (out/name).write_text('\n'.join(lines)+'\n',encoding='ascii')
    (ROOT/'integration/extra-rtl-inputs.json').write_text(json.dumps({
        'reference':'unchanged Python integer model; no hardware or camera',
        'cases':records},ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print('Generated 5 independent full-network RTL inputs and 50 golden logits')

if __name__=='__main__':main()
