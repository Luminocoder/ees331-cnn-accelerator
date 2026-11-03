"""Demo framing, exact original preprocessing and single/two-digit flow."""
import sys
from pathlib import Path
import unittest
import numpy as np
import cv2

ROOT=Path(__file__).resolve().parent.parent
sys.path[:0]=[str(ROOT/'source'),str(ROOT/'tests')]
import guided_capture
import mnist_camera_input as original
import offline_ref
import test_v42 as fixtures
import vision_pipeline
from camera_normalization import center_input

INDEX={5:0,0:1,4:2,1:3,9:4,2:5,7:12,3:9,6:11,8:14}


def demo_frame(text='5'):
    frame=np.full((800,1280),255,np.uint8)
    boxes=guided_capture.regions(frame.shape,'single' if len(text)==1 else 'two')
    for digit,(x,y,w,h) in zip(text,boxes):
        size=min(420,w-80,h-100)
        image=cv2.resize(255-fixtures.image(INDEX[int(digit)]),(size,size),interpolation=cv2.INTER_CUBIC)
        left,top=x+(w-size)//2,y+(h-size)//2
        frame[top:top+size,left:left+size]=image
    return frame


class Demo(unittest.TestCase):
    def test_guided_extraction_is_original_with_explicit_center_refinement(self):
        frame=demo_frame('5')
        roi=guided_capture.regions(frame.shape)[0]
        canvas,_,_=original.prepare_digit(frame,roi,'dark')
        actual=vision_pipeline.prepare(frame,capture_mode='guided')[0]
        self.assertTrue(np.array_equal(actual['canvas'],center_input(canvas)[0]))
        self.assertEqual(actual['meta']['target_center_xy'],[14.0,14.0])
        self.assertEqual(offline_ref.infer(actual['canvas'])[0],5)

    def test_two_guides_are_left_to_right_and_classify_independently(self):
        frame=demo_frame('50')
        items=vision_pipeline.prepare(frame,mode='two',capture_mode='guided')
        self.assertEqual([offline_ref.infer(item['canvas'])[0] for item in items],[5,0])
        self.assertLess(items[0]['meta']['bbox'][0],items[1]['meta']['bbox'][0])

    def test_empty_second_guide_rejects_whole_pair(self):
        frame=demo_frame('50')
        x,y,w,h=guided_capture.regions(frame.shape,'two')[1]
        frame[y:y+h,x:x+w]=255
        with self.assertRaises(ValueError):
            vision_pipeline.prepare(frame,mode='two',capture_mode='guided')

    def test_clipped_stroke_at_guide_edge_is_rejected(self):
        frame=np.full((800,1280),255,np.uint8)
        x,y,w,h=guided_capture.regions(frame.shape)[0]
        cv2.line(frame,(x,y+100),(x+200,y+400),0,12)
        with self.assertRaises(ValueError):
            vision_pipeline.prepare(frame,capture_mode='guided')

    def test_background_outside_guide_does_not_enter_tensor(self):
        frame=demo_frame('5')
        before=vision_pipeline.prepare(frame,capture_mode='guided')[0]['canvas']
        frame[:,:180]=0
        frame[:,1100:]=60
        after=vision_pipeline.prepare(frame,capture_mode='guided')[0]['canvas']
        self.assertTrue(np.array_equal(before,after))

    def test_demo_retains_original_camera_roi_and_disjoint_pair(self):
        self.assertEqual(guided_capture.regions((800,1280,3)),[(320,80,640,640)])
        a,b=guided_capture.regions((800,1280,3),'two')
        self.assertLess(a[0]+a[2],b[0])
        for x,y,w,h in (a,b):
            self.assertTrue(x>=0 and y>=0 and x+w<=1280 and y+h<=800)


if __name__=='__main__':
    unittest.main(verbosity=2)
