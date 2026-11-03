# ============================================================
# 导出 bias_int32 —— 按 ADR-0002 决定4 的新布局
#
# 布局(高字节在低地址, 大端):
#   Conv1_b: 权重区尾部 52040 起, 8×4=32B  -> [52040, 52072)
#   Conv2_b:                  52072, 16×4  -> [52072, 52136)
#   FC1_b:                   52136, 64×4   -> [52136, 52392)
#   FC2_b:                   52392, 10×4   -> [52392, 52432)
# 尾部 52432 < 65536 (weight_bram 深度无改动)
#
# bias32 = round( b_fp / (scale_x * scale_w) )
#   scale_w 来自 weights_int8/scales.txt
#   scale_x 含输入 >>1: 输入像素 0~255 >>1 -> 0~127, scale_x = 1/127
#   注意: 中间层激活的 scale 会随 S 右移变化, 此处按 ADR 给出的公式主算
# ============================================================

import numpy as np
import os

def export_bias_int32():
    # 各层 scale_w (来自 scales.txt)
    scales = {}
    with open("weights_int8/scales.txt") as f:
        for line in f:
            name, s = line.split()
            scales[name] = float(s)

    # scale_x: 输入量化 0~255>>1 -> 0~127, 采用固定近似
    scale_x = 1.0 / 127.0

    # 各层尺寸
    sizes = {'Conv1':8, 'Conv2':16, 'FC1':64, 'FC2':10}
    layout_start = {'Conv1':52040, 'Conv2':52072, 'FC1':52136, 'FC2':52392}

    print("="*60)
    print("导出 bias_int32（ADR-0002 布局）")
    print("="*60)

    total = 0
    bram = {}   # name -> list of bytes (大端4字节)
    for name, count in sizes.items():
        b = np.load(f"weights_fp32/{name}_b.npy").astype(np.float64)
        sw = scales[name]
        # bias32 = round( b / (scale_x * scale_w) )
        bias32 = np.round(b / (scale_x * sw)).astype(np.int32)
        # 转大端字节(高字节在低地址, 每个int32 4字节, 比特序: [31:24],[23:16],[15:8],[7:0])
        bytes_list = []
        for v in bias32:
            v = int(v) & 0xFFFFFFFF
            bytes_list.append((v>>24)&0xFF)
            bytes_list.append((v>>16)&0xFF)
            bytes_list.append((v>>8)&0xFF)
            bytes_list.append(v&0xFF)
        bram[name] = bytes_list
        total += len(bytes_list)
        print(f"  {name:6}: {count:3} × 4B = {len(bytes_list):3} B, "
              f"起始@{layout_start[name]:6}, 结束@{layout_start[name]+len(bytes_list):6}")
        print(f"         bias32前3: {bias32[:3]}")

    # 布局连续性验证
    print("-"*60)
    order=['Conv1','Conv2','FC1','FC2']
    prev_end = 52040
    ok = True
    for name in order:
        if layout_start[name] != prev_end:
            ok=False
            print(f"  ✗ {name} 起始@{layout_start[name]} != 上一块结束@{prev_end}")
        prev_end = layout_start[name] + len(bram[name])
    end_all = prev_end
    print(f"  布局连续性: {'✓ 连续' if ok else '✗ 断'}  尾部结束@{end_all}")
    print(f"  尾部 {end_all} < 65536: {'✓' if end_all<65536 else '✗'}")

    # 写文件(每行: 层名 偏移 或 直接 binary 文本, 供 B 的 $readmemh / driver)
    os.makedirs("weights_int8", exist_ok=True)
    # 输出为 汇编文本: 每行一个字节(十六进制), 从52040开始, 即给 B 追加到权重区尾部
    all_bytes = []
    for name in order:
        all_bytes.extend(bram[name])
    out = "weights_int8/bias_layout.txt"
    with open(out, "w") as f:
        for i in range(0, len(all_bytes), 16):
            row = all_bytes[i:i+16]
            f.write(" ".join(f"{b:02x}" for b in row) + "\n")
    print(f"\n已导出 {total} 字节 bias 到 {out} (追加在权重区尾部 52040 起)")
    print("说明: 高字节在低地址(大端)。B 的 engine 按4字节组装回 int32。")

if __name__ == "__main__":
    export_bias_int32()
