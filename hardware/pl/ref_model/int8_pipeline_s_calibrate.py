# ============================================================
# int8_pipeline_S_calibrate.py —— 整数精确管线 + S 标定 (完整可运行)
#
# ADR-0002 决定3: A 负责标定 S, 使整数模型 top-1 相对 float 掉点 ≤1%
#
# 整数管线 (与硬件一致):
#   conv/fc: int8 权重 × int8 激活 → int32 累加
#   中间层:  out8 = clamp( (accum32 + bias32) >>> S_layer, 0, 127 )
#   FC2:     不移位, result = accum32 + bias32 (INT32)
#
# S 标定: 扫 S_conv1/S_conv2/S_fc1, 用验证集(1000张)统计 top-1 相对 float 掉点
#
# 输入量化: x_int8 = uint8_pixel >> 1  (0~127)
# bias32 = round( b_fp / (scale_x * scale_w) ), scale_x 含 >>1 因子
# ============================================================

import numpy as np
import gzip
import itertools

def load_mnist(ip, lp, limit=None):
    with gzip.open(ip,'rb') as f: im=np.frombuffer(f.read()[16:],dtype=np.uint8)
    with gzip.open(lp,'rb') as f: lb=np.frombuffer(f.read()[8:],dtype=np.uint8)
    im=im.reshape(-1,28,28)
    return (im[:limit], lb[:limit]) if limit else (im,lb)

def read_signed(path):
    vals=[]
    for line in open(path):
        for h in line.split():
            if h.strip():
                u=int(h,16); vals.append(u-256 if u>=128 else u)
    return np.array(vals, np.int64)

def load_weights():
    W={}
    for n in ["Conv1","Conv2","FC1","FC2"]:
        W[n]=read_signed(f"weights_int8/{n}_int8.txt")
    return W

def load_scales():
    sc={}
    for line in open("weights_int8/scales.txt"):
        name,s=line.split(); sc[name]=float(s)
    return sc

# ---------- 整数卷积(带pad=1, int32累加) ----------
def int_conv(x, w8, mid_bias, S, k=3, clamp=True, nchn_in=1):
    """单输出通道卷积. x:(H,W) int8激活, w8:(k*k) int8核
       mid_bias:int32 该输出通道bias, S:右移位数
       返回量化后激活 (H,W) int8"""
    H,Wd=x.shape
    out=np.zeros((H,Wd), np.int64)
    for i in range(H):
        for j in range(Wd):
            acc=0
            for di in range(k):
                for dj in range(k):
                    si,sj=i-1+di, j-1+dj
                    xv=int(x[si,sj]) if 0<=si<H and 0<=sj<Wd else 0
                    acc += xv*int(w8[di*k+dj])
            out[i,j]=acc+int(mid_bias)
    if clamp:
        return np.clip(out>>S, 0, 127).astype(np.int64)
    else:
        return (out>>S)

def pool2(x):
    h,wd=x.shape
    return np.max(x.reshape(h//2,2,wd//2,2),axis=(1,3))

def int_forward(img, W, scales, bias32, S_conv1, S_conv2, S_fc1):
    """整图整数前向, 返回10类INT32得分"""
    W1=W["Conv1"].reshape(8,1,3,3)
    W2=W["Conv2"].reshape(16,8,3,3)
    Wf1=W["FC1"].reshape(784,64)
    Wf2=W["FC2"].reshape(64,10)
    # 输入量化
    x0=(img.astype(np.int64)>>1)
    # Conv1 1->8ch
    act1=[]
    for oc in range(8):
        a=int_conv(x0, W1[oc,0].ravel(), bias32["Conv1"][oc], S_conv1, k=3)
        act1.append(a)
    a1=np.stack(act1)                     # (8,28,28)
    p1=np.stack([pool2(a1[oc]) for oc in range(8)])  # (8,14,14)
    # Conv2 8->16ch
    act2=[]
    for oc in range(16):
        acc=np.zeros((14,14),np.int64)
        for ic in range(8):
            acc += int_conv_nobias(p1[ic], W2[oc,ic].ravel(), k=3)
        r=np.clip((acc+bias32["Conv2"][oc])>>S_conv2, 0, 127).astype(np.int64)
        act2.append(r)
    a2=np.stack(act2)                     # (16,14,14)
    p2=np.stack([pool2(a2[oc]) for oc in range(16)])  # (16,7,7)=784
    flat=p2.reshape(784)
    # FC1 784->64
    h3=flat @ Wf1.astype(np.int64)        # 784*64
    a3=np.clip((h3+bias32["FC1"])>>S_fc1, 0, 127).astype(np.int64)  # 64
    # FC2 64->10, 不移位
    logits=(a3 @ Wf2.astype(np.int64)) + bias32["FC2"]              # 10 INT32
    return logits

def int_conv_nobias(x, w8, k=3):
    """无bias纯累加(供多通道累加)"""
    H,Wd=x.shape
    out=np.zeros((H,Wd),np.int64)
    for i in range(H):
        for j in range(Wd):
            acc=0
            for di in range(k):
                for dj in range(k):
                    si,sj=i-1+di,j-1+dj
                    xv=int(x[si,sj]) if 0<=si<H and 0<=sj<Wd else 0
                    acc+=xv*int(w8[di*k+dj])
            out[i,j]=acc
    return out

def main():
    print("加载权重与验证集(1000张)...")
    W=load_weights()
    scales=load_scales()
    img,lab=load_mnist("train-images-idx3-ubyte.gz","train-labels-idx1-ubyte.gz",limit=1000)

    # bias32 = round(b / (scale_x*scale_w))
    # 为标定, 这里用简化: bias直接取 fp32 的整数化近似; 正式用 export_bias_int32 布局
    scale_x=1.0/127.0
    bias32={}
    for n,c in [("Conv1",8),("Conv2",16),("FC1",64),("FC2",10)]:
        b=np.load(f"weights_fp32/{n}_b.npy")
        bias32[n]=np.round(b/(scale_x*scales[n])).astype(np.int64)

    # float 基准
    import importlib.util
    spec=importlib.util.spec_from_file_location("tcnn","train_cnn_numpy.py")
    tcnn=importlib.util.module_from_spec(spec); spec.loader.exec_module(tcnn)
    fp_params=tuple(np.load(f"weights_fp32/{n}.npy") for n in
                    ["Conv1","Conv1_b","Conv2","Conv2_b","FC1","FC1_b","FC2","FC2_b"])
    Xv=img.astype(np.float32)[:,None,:,:]/255.0
    f_acc=(tcnn.forward_full(Xv,fp_params)['logits'].argmax(1)==lab).mean()
    print(f"float 基准验证集准确率: {f_acc*100:.2f}%")

    # 扫几组 S
    candidates=[(6,7,7),(6,6,7),(7,7,7),(5,6,6),(6,7,6),(5,6,7)]
    print("\n=== S 标定扫描(验证集1000张) ===")
    best=None
    for (s1,s2,sf) in candidates:
        correct=0
        for n in range(1000):
            logits=int_forward(img[n],W,scales,bias32,s1,s2,sf)
            if int(np.argmax(logits))==int(lab[n]): correct+=1
        acc=correct/1000
        drop=f_acc-acc
        print(f"  S=({s1},{s2},{sf}): 整数准确率={acc*100:.2f}%  掉点={drop*100:.2f}pp "
              f"{'✓' if drop<=0.01 else '✗'}")
        if best is None or acc>best[1]:
            best=((s1,s2,sf),acc)
    print(f"\n最佳 S: {best[0]}, 准确率 {best[1]*100:.2f}%")

if __name__=="__main__":
    main()
