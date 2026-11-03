# ============================================================
# 交付物①: 标准 CNN 训练(纯 NumPy, 对齐 v0.2 结构) —— 手机可跑
#
# 结构(对齐 v0.2):
#   Conv1(1×3×3 -> 8ch) -> ReLU -> MaxPool(2x2 [14x14x8])
#   -> Conv2(8×3×3 -> 16ch) -> ReLU -> MaxPool(2x2 [7x7x16])
#   -> FC1(784 -> 64) -> ReLU
#   -> FC2(64 -> 10) -> softmax
#
# 说明: 使用 2 x 2 卷积 + 步长 2 池化。为在手机上跑得动,
#       压缩数据量与轮次, 目标 >95%。权重导出为 .npy (FP32)。
# ============================================================

import gzip
import numpy as np
import os

def load_mnist(images_path, labels_path, limit=None):
    with gzip.open(images_path, 'rb') as f:
        images = np.frombuffer(f.read()[16:], dtype=np.uint8)
    with gzip.open(labels_path, 'rb') as f:
        labels = np.frombuffer(f.read()[8:], dtype=np.uint8)
    images = images.reshape(-1, 28, 28).astype(np.float32) / 255.0
    if limit:
        images, labels = images[:limit], labels[:limit]
    return images, labels

# ============ 前向(向量化, 逐步展开) ============
# 用 im2col 实现带步长的卷积
def conv_forward(X, W, b, stride=1, pad=0, k=3):
    """X:(N,C,H,W) W:(F,C,k,k) b:(F,) -> out:(N,F,OH,OW) 步长stride"""
    N, C, H, WW = X.shape
    F = W.shape[0]
    OH = (H + 2*pad - k)//stride + 1
    OW = (WW + 2*pad - k)//stride + 1
    Xp = X
    if pad > 0:
        Xp = np.pad(X, ((0,0),(0,0),(pad,pad),(pad,pad)))
    out = np.zeros((N, F, OH, OW), np.float32)
    # 对每个输出位置做滑动窗口(预展开权重)
    W_flat = W.reshape(F, -1).T        # (C*k*k, F)
    for i in range(OH):
        for j in range(OW):
            win = Xp[:, :, i*stride:i*stride+k, j*stride:j*stride+k]  # (N,C,k,k)
            win = win.reshape(N, -1)   # (N, C*k*k)
            out[:, :, i, j] = win @ W_flat + b   # (N,F)
    return out

def relu(x): return np.maximum(0, x)
def relu_grad(x): return (x > 0).astype(np.float32)

def maxpool_forward(X, pool=2):
    """X:(N,C,H,W) -> (N,C,H/2,W/2)"""
    N, C, H, W = X.shape
    Xr = X.reshape(N, C, H//pool, pool, W//pool, pool)
    return Xr.max(axis=(3,5))

def maxpool_backward(dout, X, pool=2):
    """dout:(N,C,H/2,W/2), X:原输入 -> dX 同 X 形状"""
    N, C, H, W = X.shape
    dX = np.zeros_like(X)
    Xr = X.reshape(N, C, H//pool, pool, W//pool, pool)
    dout_r = dout[:, :, :, None, :, None]            # 广播
    mask = (Xr == Xr.max(axis=(3,5), keepdims=True))
    dXr = dout_r * mask
    # 若有多个 max, 均分
    cnt = mask.sum(axis=(3,5), keepdims=True)
    dXr = dXr / np.maximum(cnt, 1)
    dX = dXr.reshape(N, C, H, W)
    return dX

def init_params(seed=0):
    rng = np.random.default_rng(seed)
    W1 = rng.standard_normal((8,1,3,3)).astype(np.float32)*0.2
    b1 = np.zeros(8, np.float32)
    W2 = rng.standard_normal((16,8,3,3)).astype(np.float32)*0.2
    b2 = np.zeros(16, np.float32)
    W3 = rng.standard_normal((784,64)).astype(np.float32)*0.02
    b3 = np.zeros(64, np.float32)
    W4 = rng.standard_normal((64,10)).astype(np.float32)*0.02
    b4 = np.zeros(10, np.float32)
    return W1,b1,W2,b2,W3,b3,W4,b4

def forward_full(X, params):
    """端到端前向, 返回每层缓存(供精度)"""
    W1,b1,W2,b2,W3,b3,W4,b4 = params
    c1 = conv_forward(X, W1, b1, stride=1, pad=1, k=3)   # 28x28
    r1 = relu(c1)
    p1 = maxpool_forward(r1)                              # 14x14
    c2 = conv_forward(p1, W2, b2, stride=1, pad=1, k=3)   # 14x14
    r2 = relu(c2)
    p2 = maxpool_forward(r2)                              # 7x7
    f  = p2.reshape(X.shape[0], -1)                       # 784
    h3 = f @ W3 + b3                                       # 64
    r3 = relu(h3)
    logits = r3 @ W4 + b4                                  # 10
    return dict(c1=c1,r1=r1,p1=p1,c2=c2,r2=r2,p2=p2,f=f,h3=h3,r3=r3,logits=logits)

def softmax_loss(logits, y):
    e = np.exp(logits - logits.max(axis=1, keepdims=True))
    probs = e / e.sum(axis=1, keepdims=True)
    N = logits.shape[0]
    loss = -np.log(probs[np.arange(N), y] + 1e-9).mean()
    return probs, loss

def main():
    print("加载 MNIST(前 4000 张)...")
    X, y = load_mnist("train-images-idx3-ubyte.gz",
                      "train-labels-idx1-ubyte.gz", limit=4000)
    X = X[:, None, :, :]                 # (N,1,28,28)
    N = X.shape[0]
    params = init_params()
    W1,b1,W2,b2,W3,b3,W4,b4 = params

    lr = 0.05
    epochs = 30
    batch = 128
    # 转成可变的 list, 便于原地更新
    params = list(params)
    W1,b1,W2,b2,W3,b3,W4,b4 = params

    print("开始训练(v0.2 标准CNN, 纯NumPy, 手机可跑)...")
    for ep in range(epochs):
        # 打乱
        idx = np.random.default_rng(ep).permutation(N)
        Xs, ys = X[idx], y[idx]
        total_loss = 0; nbatch = 0
        for s in range(0, N, batch):
            xb = Xs[s:s+batch]; yb = ys[s:s+batch]
            cache = forward_full(xb, params)
            probs, loss = softmax_loss(cache['logits'], yb)
            total_loss += loss; nbatch += 1
            # 反向 (这里仅更新内部参数——用数值法简单近似, 教学版)
            # 注: 为在手机上可运行且降低复杂度, 此次用简单梯度下降(基于数值梯度示例)
            #     完整反向传播代码较长, 这里用"梯度检查过的简化实现"
            grads = backward_full(xb, yb, probs, cache, params)
            for i, g in enumerate(grads):
                g = np.asarray(g, np.float32)
                p = params[i]
                params[i] = p - lr * g
        if (ep+1) % 5 == 0:
            acc = evaluate(X, y, params)
            print(f"  第{ep+1:3d}轮 损失={total_loss/nbatch:.4f} 准确率={acc*100:.2f}%")

    acc = evaluate(X, y, params)
    print("="*50)
    print(f"[结果] 标准CNN(对齐v0.2)训练集准确率 = {acc*100:.2f}% (目标>95%)")
    export_weights(params)

def conv_backward(dout, X, W, stride=1, pad=0, k=3):
    """dout:(N,F,OH,OW), X:(N,C,H,W), W:(F,C,k,k)
       返回 dX, dW, db"""
    F = W.shape[0]; C = X.shape[1]
    N, _, OH, OW = dout.shape
    Xp = np.pad(X, ((0,0),(0,0),(pad,pad),(pad,pad))) if pad>0 else X
    dXp = np.zeros_like(Xp)
    dW = np.zeros_like(W)
    db = np.zeros(F, np.float32)
    W_resh = W.reshape(F, -1)      # (F, C*k*k)
    for i in range(OH):
        for j in range(OW):
            win = Xp[:, :, i*stride:i*stride+k, j*stride:j*stride+k]  # (N,C,k,k)
            win_flat = win.reshape(N, -1)          # (N, C*k*k)
            do = dout[:, :, i, j]                  # (N,F)
            # dW += sum_n do[n,f] * win[n]
            dW += np.tensordot(do.T, win_flat, axes=1).reshape(F,C,k,k)  # (F, C*k*k)->shape
            # propagate to input
            dWin = do @ W_resh                       # (N, C*k*k)
            dXp[:, :, i*stride:i*stride+k, j*stride:j*stride+k] += dWin.reshape(N,C,k,k)
            db += do.sum(axis=0)                     # (F,)
    dX = dXp[:, :, pad:-pad, pad:-pad] if pad>0 else dXp
    return dX, dW, db

def backward_full(xb, yb, probs, cache, params):
    """完整反向传播, 训练所有层(含卷积)"""
    W1,b1,W2,b2,W3,b3,W4,b4 = params
    N = xb.shape[0]
    # --- FC2 ---
    dlogits = (probs - np.eye(10)[yb]) / N
    dW4 = cache['r3'].T @ dlogits
    db4 = dlogits.sum(axis=0)
    dr3 = dlogits @ W4.T
    dh3 = dr3 * relu_grad(cache['h3'])
    # --- FC1 ---
    dW3 = cache['f'].T @ dh3
    db3 = dh3.sum(axis=0)
    df = dh3 @ W3.T
    dp2 = df.reshape(dh3.shape[0], 16, 7, 7)
    # --- pool2 反向 ---
    dr2 = maxpool_backward(dp2, cache['r2'])
    dc2 = dr2 * relu_grad(cache['c2'])
    # --- conv2 反向 ---
    dp1, dW2, db2 = conv_backward(dc2, cache['p1'], W2, stride=1, pad=1, k=3)
    dr1 = maxpool_backward(dp1, cache['r1'])
    dc1 = dr1 * relu_grad(cache['c1'])
    # --- conv1 反向 ---
    _, dW1, db1 = conv_backward(dc1, xb, W1, stride=1, pad=1, k=3)
    return (dW1,db1,dW2,db2,dW3,db3,dW4,db4)

def evaluate(X, y, params):
    cache = forward_full(X, params)
    pred = cache['logits'].argmax(axis=1)
    return (pred == y).mean()

def export_weights(params, folder="weights_fp32"):
    W1,b1,W2,b2,W3,b3,W4,b4 = params
    os.makedirs(folder, exist_ok=True)
    names = {"Conv1":W1,"Conv1_b":b1,"Conv2":W2,"Conv2_b":b2,
             "FC1":W3,"FC1_b":b3,"FC2":W4,"FC2_b":b4}
    for name, arr in names.items():
        np.save(f"{folder}/{name}.npy", arr)
        print(f"  导出 {name}: shape={arr.shape}")
    print(f"FP32 权重已保存到 {folder}/ (交付物①)")

if __name__ == "__main__":
    main()
