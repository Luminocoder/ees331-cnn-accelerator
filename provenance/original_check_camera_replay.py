"""Replay the captured development set without opening a camera or serial port."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import cv2
import numpy as np

ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT/'source'))
import vision_pipeline as vp
import mnist_camera_input as io
import offline_ref

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--logs',type=Path,default=ROOT.parent/'EES331_Studio_v5.0_20261030/logs/vision')
    parser.add_argument('--output',type=Path,default=ROOT/'integration/camera-replay')
    args=parser.parse_args()
    paths=sorted(args.logs.glob('*/run.json'))
    # Exploratory labels from the preceding visual audit, not independent truth.
    observations={4:'7',5:'7',15:'6',16:'3',17:'8',18:'8',19:'2',20:'2',21:'2',
        22:'1',23:'1',24:'1',25:'1',26:'1',28:'7',29:'7',30:'7',31:'7',32:'7',
        33:'6',34:'6',35:'9',36:'9',37:'9',7:'45',8:'45',9:'45',10:'45',11:'67',12:'67',27:'21'}
    report=dict(hardware_accessed=False,camera_opened=False,labels='unconfirmed visual development labels',records=[])
    args.output.mkdir(parents=True,exist_ok=True)
    for index,truth in observations.items():
        if index>=len(paths):
            raise RuntimeError('Expected original audit logs; supply --logs pointing to those records')
        path=paths[index]
        run=json.loads(path.read_text(encoding='utf-8'))
        frame=io.read_image(path.parent/'frame.png')
        record=dict(index=index,id=path.parent.name,observed=truth,
            frame_sha256=hashlib.sha256((path.parent/'frame.png').read_bytes()).hexdigest(),methods={})
        for name,roi in [('recorded_search',run['context'].get('roi_xywh')),('full_frame',None)]:
            method={}
            try:
                items=vp.prepare(frame,mode='single' if len(truth)==1 else 'two',roi=roi)
                predictions=[offline_ref.infer(item['canvas'],base=ROOT/'resources')[0] for item in items]
                method=dict(prediction=''.join(map(str,predictions)),bboxes=[i['meta']['bbox'] for i in items],
                    pixels_sha256=[i['meta']['pixels_sha256'] for i in items],correct=''.join(map(str,predictions))==truth)
                out=args.output/path.parent.name/name
                out.mkdir(parents=True,exist_ok=True)
                for n,item in enumerate(items):
                    io.write_png(out/('input%d.png'%n),item['canvas'])
                if len(truth)==1:
                    try:
                        original,_,meta=io.prepare_digit(frame,roi,'dark')
                        method['exact_v29_tensor']=np.array_equal(original,items[0]['canvas'])
                    except ValueError:
                        method['exact_v29_tensor']=None
            except ValueError as error:
                method=dict(error=str(error))
            record['methods'][name]=method
        report['records'].append(record)
        print(index,truth,{k:v.get('prediction',v.get('error')) for k,v in record['methods'].items()},flush=True)
    report['summary']={}
    for name in ['recorded_search','full_frame']:
        for count in [1,2]:
            rows=[r for r in report['records'] if len(r['observed'])==count]
            valid=[r for r in rows if 'error' not in r['methods'][name]]
            report['summary'][name+'_'+str(count)]=dict(total=len(rows),accepted=len(valid),
                correct=sum(r['methods'][name]['correct'] for r in valid),
                incorrect=sum(not r['methods'][name]['correct'] for r in valid),rejected=len(rows)-len(valid),
                exact_legacy_tensors=sum(r['methods'][name].get('exact_v29_tensor') is True for r in valid))
    (args.output/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report['summary'],ensure_ascii=False,indent=2))

if __name__=='__main__':
    main()
