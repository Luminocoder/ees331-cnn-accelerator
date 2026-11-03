# ============================================================
# A2: 对标准CNN(交付物①)做 INT8 量化 —— 产出给B的权重文件
#
# 输入: weights_fp32/ 下的 8 个 .npy (Conv1/Conv2/FC1/FC2 权重+bias)
# 输出: weights_int8/
#   Conv1_int8.txt  Conv2_int8.txt  FC1_int8.txt  FC2_int8.txt (INT8 十六进制, $readmemh友好)
#   scales.txt (每层 scale, 硬件反量化用)
#
# 对齐 v0.2 权重格式约定 (docs/interfaces/readme.md 第四节)。
# ============================================================

import os
import numpy as np

def quantize_int8(t):
    """对称量化: 返回 (int8数组, scale)"""
    t = t.astype(np.float32)
    max_abs = np.abs(t).max()
    if max_abs == 0:
        max_abs = 1e-6
    scale = max_abs / 127.0
    q = np.clip(np.round(t / scale), -128, 127).astype(np.int8)
    return q, scale

def write_int8_hex(int8_flat, path, bytes_per_line=16):
    """把 INT8 写成 $readmemh 友好的 16 进制文本(每字节2位)"""
    with open(path, "w") as f:
        for i in range(0, len(int8_flat), bytes_per_line):
            chunk = int8_flat[i:i+bytes_per_line]
            hexs = ["{:02x}".format(int(v) & 0xFF) for v in chunk]
            f.write(" ".join(hexs) + "\n")

def main():
    src = "weights_fp32"
    dst = "weights_int8"
    os.makedirs(dst, exist_ok=True)

    # 权重层顺序(与 v0.2 存储顺序一致)
    wlayers = {"Conv1": "Conv1.npy", "Conv2": "Conv2.npy",
               "FC1": "FC1.npy", "FC2": "FC2.npy"}
    print("=" * 60)
    print("INT8 量化: 标准CNN FP32 权重 -> INT8")
    print("=" * 60)

    total_bytes = 0
    scales = {}
    for name, fn in wlayers.items():
        w = np.load(os.path.join(src, fn))
        q, scale = quantize_int8(w)
        scales[name] = float(scale)
        q_flat = q.ravel()
        total_bytes += q_flat.nbytes
        path = os.path.join(dst, f"{name}_int8.txt")
        write_int8_hex(q_flat, path)
        # 打印量化质量
        deq = q.astype(np.float32) * scale
        mae = np.abs(w - deq).mean()
        print(f"  {name:6}: {w.size:6} 权重, scale={scale:.7f}, "
              f"量化误差(MAE)={mae:.6f}, -> {path}")

    print("-" * 60)
    print(f"INT8 权重总存储: {total_bytes} 字节 ≈ {total_bytes/1024:.1f} KB")
    print(f"文档目标 ~51KB / Zynq BRAM 560KB: "
          f"{'✅ 放得下' if total_bytes < 560*1024 else '⚠️ 超'}")
    # scale 表(硬件反量化用)
    with open(os.path.join(dst, "scales.txt"), "w") as f:
        for name, s in scales.items():
            f.write(f"{name} {s}\n")

    # ---- bias: 导出为文本(FP32, 每行一个), 便于硬件/驱动读取 ----
    blayers = {"Conv1_b": "Conv1_b.npy", "Conv2_b": "Conv2_b.npy",
               "FC1_b": "FC1_b.npy", "FC2_b": "FC2_b.npy"}
    print("-" * 60)
    for name, fn in blayers.items():
        b = np.load(os.path.join(src, fn))
        path = os.path.join(dst, f"{name}.txt")
        with open(path, "w") as f:
            for v in b.ravel():
                f.write(f"{float(v):.8f}\n")
        print(f"  {name}: {b.size} bias(FP32) -> {path}")

    # ---- bias_int32: ADR-0002 决定4 新布局导出 (52040起, 高字节低地址/大端) ----
    # bias32 = round( b_fp / (scale_x * scale_w) ), scale_x 含输入>>1
    scale_x = 1.0 / 127.0
    layout_start = {'Conv1': 52040, 'Conv2': 52072, 'FC1': 52136, 'FC2': 52392}
    bl_sizes = {'Conv1': 8, 'Conv2': 16, 'FC1': 64, 'FC2': 10}
    order = ['Conv1', 'Conv2', 'FC1', 'FC2']
    all_bytes = []
    print("-" * 60)
    print("bias_int32 布局导出 (ADR-0002 决定4):")
    for name in order:
        b32 = np.round(np.load(f"{src}/{name}_b.npy") / (scale_x * scales[name])).astype(np.int32)
        nbytes = 0
        for v in b32:
            v = int(v) & 0xFFFFFFFF
            for s in (24, 16, 8, 0):      # 高字节在低地址(大端)
                all_bytes.append((v >> s) & 0xFF)
            nbytes += 4
        print(f"  {name}_b: {bl_sizes[name]}×4B = {nbytes}B, 起始@{layout_start[name]}")
    # 写 bias_layout.txt
    bpath = os.path.join(dst, "bias_layout.txt")
    with open(bpath, "w") as f:
        for i in range(0, len(all_bytes), 16):
            row = all_bytes[i:i+16]
            f.write(" ".join(f"{bb:02x}" for bb in row) + "\n")
    end_all = 52040 + len(all_bytes)
    print(f"  尾部结束@{end_all} < 65536: {'✅' if end_all < 65536 else '⚠️'}")
    print(f"  -> {bpath} ({len(all_bytes)}B, 大端, 追加在权重区尾部)")

    print("\n已保存 scales.txt (硬件反量化用)。")
    print("→ 交付物② INT8 权重 + bias(F32/int32布局) 完成, 位置 weights_int8/")

if __name__ == "__main__":
    main()
