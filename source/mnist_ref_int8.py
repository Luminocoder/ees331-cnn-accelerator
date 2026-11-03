# ============================================================
# mnist_ref_int8.py —— INT8 硬件参考模型（金标准, ADR-0002 整数管线）
#
# 按 ADR-0002 决定1/3 重写为【纯整数管线】, 与硬件逐位一致:
#
#   输入量化:  x_int8 = uint8_pixel >> 1         (0~127, 决定3)
#   中间层:    out8  = clamp( (accum32+bias32) >>> S_layer, 0, 127 )  (决定1)
#   FC2:       result = accum32 + bias32          (不移位, INT32, 决定1)
#
#   S 标定:    S_conv1=9, S_conv2=9, S_fc1=10     (见 docs/design/quantization-calibration.md)
#   bias32:    从 weights_int8/ 新布局读入
#               bias32 = round( b_fp / (scale_x * scale_w) )
#
# 用途: R3 集成金标准。硬件输出应与本脚本输出 bit-exact。
#
# 用法: python3 mnist_ref_int8.py  (输出 test_vectors/ 的整数金标准)
# ============================================================

import numpy as np
import os

# ---- 标定的 S 值 (见 docs/design/quantization-calibration.md) ----
S_conv1 = 9
S_conv2 = 9
S_fc1   = 10

def read_signed(path):
    vals=[]
    with open(path) as f:
        for line in f:
            for h in line.split():
                if h.strip():
                    u=int(h,16); vals.append(u-256 if u>=128 else u)
    return np.array(vals, dtype=np.int64)

def load_scales():
    sc={}
    for line in open("weights_int8/scales.txt"):
        name,s=line.split(); sc[name]=float(s)
    return sc

def load_int8_weights():
    W={}
    for n in ["Conv1","Conv2","FC1","FC2"]:
        W[n]=read_signed(f"weights_int8/{n}_int8.txt")
    return W

def compute_bias32():
    """bias32 = round( b_fp / (scale_x * scale_w) ), 每层"""
    sc=load_scales()
    scale_x=1.0/127.0
    b32={}
    for n,c in [("Conv1",8),("Conv2",16),("FC1",64),("FC2",10)]:
        b=np.load(f"weights_fp32/{n}_b.npy")
        b32[n]=np.round(b/(scale_x*sc[n])).astype(np.int64)
    return b32

# ---------- 整数卷积(带pad=1, int32累加, 无bias返回纯累加) ----------
def int_conv_accum(x, w8, k=3):
    """x:(H,W) int8激活, w8:(k*k) int8核 -> (H,W) int32 原始累加"""
    H,Wd=x.shape
    out=np.zeros((H,Wd),np.int64)
    for i in range(H):
        for j in range(Wd):
            acc=np.int64(0)
            for di in range(k):
                for dj in range(k):
                    si,sj=i-1+di, j-1+dj
                    xv=np.int64(x[si,sj]) if 0<=si<H and 0<=sj<Wd else 0
                    acc += xv * np.int64(w8[di*k+dj])
            out[i,j]=acc
    return out

def pool2(x):
    h,wd=x.shape
    return np.max(x.reshape(h//2,2,wd//2,2),axis=(1,3))

def int_forward(img, W, b32, S1, S2, Sf1):
    """单张图整数推理, 返回 10 个 INT32 logits"""
    W1=W["Conv1"].reshape(8,1,3,3)
    W2=W["Conv2"].reshape(16,8,3,3)
    Wf1=W["FC1"].reshape(784,64)
    Wf2=W["FC2"].reshape(64,10)

    # 输入量化 uint8>>1
    x0=(img.astype(np.int64)>>1)

    # Conv1: 1->8ch, 中间层 (accum+bias)>>>S1 clamp
    a1=[]
    for oc in range(8):
        conv=int_conv_accum(x0, W1[oc,0].ravel(), k=3)
        out=np.clip((conv+b32["Conv1"][oc])>>S1, 0, 127).astype(np.int64)
        a1.append(out)
    p1=np.stack([pool2(a1[oc]) for oc in range(8)])   # (8,14,14)

    # Conv2: 8->16ch, >>>S2
    a2=[]
    for oc in range(16):
        acc=np.zeros((14,14),np.int64)
        for ic in range(8):
            acc += int_conv_accum(p1[ic], W2[oc,ic].ravel(), k=3)
        out=np.clip((acc+b32["Conv2"][oc])>>S2, 0, 127).astype(np.int64)
        a2.append(out)
    p2=np.stack([pool2(a2[oc]) for oc in range(16)])  # (16,7,7)=784
    flat=p2.reshape(784)

    # FC1: 784->64, >>>Sf1
    h3=flat @ Wf1.astype(np.int64)
    a3=np.clip((h3+b32["FC1"])>>Sf1, 0, 127).astype(np.int64)

    # FC2: 64->10, 不移位, +bias 保留 INT32
    logits=(a3 @ Wf2.astype(np.int64)) + b32["FC2"]
    return logits

def main():
    print("加载 INT8 权重与 bias32 ...")
    W=load_int8_weights()
    b32=compute_bias32()
    print(f"使用标定 S: S_conv1={S_conv1}, S_conv2={S_conv2}, S_fc1={S_fc1}")

    imgs=np.load("test_vectors/images.npy")   # (20,28,28) uint8
    labels=np.loadtxt("test_vectors/labels.txt",dtype=np.int64)

    print("="*60)
    print(f"INT8 整数金标准推理 (20张, S={S_conv1},{S_conv2},{S_fc1})")
    print("="*60)
    logits_all=[]
    correct=0
    for i in range(imgs.shape[0]):
        lg=int_forward(imgs[i], W, b32, S_conv1, S_conv2, S_fc1)
        pred=int(np.argmax(lg))
        logits_all.append(lg)
        ok=(pred==int(labels[i]))
        correct+=1 if ok else 0
        mark="✓" if ok else "✗"
        print(f"  img{i:02d}: 真={int(labels[i])} 预测={pred} {mark}  "
              f"top_logit={int(lg.max())}")
    print("-"*60)
    print(f"准确率: {correct}/{imgs.shape[0]} ({correct/imgs.shape[0]*100:.1f}%)")

    os.makedirs("test_vectors", exist_ok=True)
    np.save("test_vectors/int8_logits.npy", np.array(logits_all, np.int32))
    # 同步更新 predictions.txt (整数模型 argmax, 给 B/C 参考)
    with open("test_vectors/predictions.txt", "w") as f:
        for i in range(imgs.shape[0]):
            pred=int(np.argmax(logits_all[i]))
            conf=" ".join(f"{int(v)}" for v in logits_all[i])
            f.write(f"img{i:02d} true={int(labels[i])} pred={pred}  {conf}\n")
    print("已保存整数金标准 10 类 INT32 得分 -> test_vectors/int8_logits.npy")
    print("已更新 predictions.txt (整数模型 argmax)")
    print("(FC2 不移位, 与硬件 RESULT 逐位一致；R3 用此 bit-exact 验收)")

if __name__=="__main__":
    main()
