# ============================================================
# A4: 生成测试向量 (使用【标准CNN】权重 v2)
# 与交付物①(标准CNN)一致 —— 给C的金标准必须来自同模型
#
# 修正: 旧版用 mnist_ref(固定核96.86%), 现改用标准CNN权重(weights_fp32/)
#       确保金标准输出与交付B的权重模型一致, R3验收才对得上。
#
# 输出(test_vectors/):
#   images.npy       - 20张 28x28 灰度图
#   labels.txt       - 真实标签
#   predictions.txt  - 标准CNN的预测+10类置信度(金标准)
# ============================================================

import gzip
import os
import numpy as np

# 复用标准CNN的前向(定义在 train_cnn_numpy.py)
import importlib.util
spec = importlib.util.spec_from_file_location("tcnn", "train_cnn_numpy.py")
tcnn = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tcnn)

def load_mnist_raw(images_path, labels_path, limit=None):
    with gzip.open(images_path, 'rb') as f:
        images = np.frombuffer(f.read()[16:], dtype=np.uint8)
    with gzip.open(labels_path, 'rb') as f:
        labels = np.frombuffer(f.read()[8:], dtype=np.uint8)
    images = images.reshape(-1, 28, 28)   # 原始 uint8 (0-255)
    if limit:
        images, labels = images[:limit], labels[:limit]
    return images, labels

def load_stdcnn_params():
    """从 weights_fp32/ 加载标准CNN权重(顺序: W1,b1,W2,b2,W3,b3,W4,b4)"""
    names = ["Conv1","Conv1_b","Conv2","Conv2_b","FC1","FC1_b","FC2","FC2_b"]
    params = [np.load(f"weights_fp32/{n}.npy") for n in names]
    return tuple(params)

def main():
    # 1) 读数据, 每数字取2张
    X, y = load_mnist_raw("train-images-idx3-ubyte.gz",
                          "train-labels-idx1-ubyte.gz")
    picked, plbl = [], []
    counts = {d:0 for d in range(10)}
    for i in range(len(X)):
        d = int(y[i])
        if counts[d] < 2:
            picked.append(X[i]); plbl.append(d); counts[d]+=1
        if all(v==2 for v in counts.values()):
            break
    picked = np.array(picked, np.uint8)
    plbl = np.array(plbl)

    # 2) 加载标准CNN权重
    params = load_stdcnn_params()
    print("已加载标准CNN权重(weights_fp32/):", end=" ")
    for n in ["Conv1","Conv2","FC1","FC2"]:
        print(f"{n}{np.load(f'weights_fp32/{n}.npy').shape}", end="  ")
    print()

    # 3) 用标准CNN前向跑20张
    xb = picked.astype(np.float32)[:, None, :, :] / 255.0
    cache = tcnn.forward_full(xb, params)
    logits = cache['logits']
    preds = logits.argmax(axis=1)

    # 4) 写文件
    os.makedirs("test_vectors", exist_ok=True)
    np.save("test_vectors/images.npy", picked)
    with open("test_vectors/labels.txt", "w") as f:
        for l in plbl: f.write(f"{l}\n")
    with open("test_vectors/predictions.txt", "w") as f:
        for i in range(len(plbl)):
            conf = " ".join(f"{logits[i,j]:.4f}" for j in range(10))
            f.write(f"img{i:02d} true={plbl[i]} pred={preds[i]}  {conf}\n")

    # 5) 报告
    hit = int((preds==plbl).sum())
    print("="*50)
    print(f"测试向量完成(标准CNN): {hit}/20 预测正确")
    print("\n每张【真标签 → 标准CNN预测】：")
    for i in range(len(plbl)):
        mark = "✓" if preds[i]==plbl[i] else "✗"
        print(f"  img{i:02d}: 真={plbl[i]} 预测={preds[i]} {mark}")

if __name__ == "__main__":
    main()
