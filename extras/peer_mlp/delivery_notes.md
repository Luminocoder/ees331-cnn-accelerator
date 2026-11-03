# MNIST 识别率提升 —— 交付包说明

> 交付日期：2026-10-29
> 场景：MNIST 手写数字识别前端（原训练权重测试集 98.18%，实际手写识别率偏低）
> 结论：**识别率低的主因是前端输入未做 MNIST 同分布预处理；修正后配合新权重，画布输入识别率 95.75% → 97.60%，模拟手绘场景 70.26% → 88.79%。**

---

## 一、包内容

```
MNIST_improved_v2/
├── README_交付说明.md          ← 本文件（先看这个）
├── README_改进说明.md          ← 技术细节：诊断过程、改动点、结果
├── verify_delivery.py          ← 自检脚本（拿到包先跑它）
│
├── mnist_weights.npz           ← 新权重 784-256-128-10（与旧文件格式完全一致，可零改动替换）
├── mnist_weights_big.npz       ← 新权重 784-512-256-10（精度更高，需改层宽）
├── mnist_weights_v1_98p18.npz  ← 旧权重备份（98.18%，用于对比/回退）
│
├── mnist_preprocess.py         ← ★ 前端输入预处理（Python）
├── preprocess.js               ← ★ 前端输入预处理（JavaScript，与 Python 版等价）
├── predict_canvas.py           ← 单张图片识别工具，也可当库调用
│
├── step3_mnist_improved.py     ← 改进版训练脚本（Adam + 余弦退火 + 数据增强）
├── step3_mnist_recognition.py  ← 原版教学脚本（SGD，保留作对照）
├── finetune_clean.py           ← 可选：干净数据微调（可再 +0.1~0.3pp）
├── verify_canvas_pipeline.py   ← 端到端对比：朴素输入 vs 正确预处理
├── make_summary_chart.py       ← 生成汇总对比图
│
├── preprocess_demo.png         ← 预处理四步可视化
└── improved/                   ← 训练曲线、混淆矩阵、对比图、结果 JSON
```

---

## 二、拿到包后先自检（约 1 分钟）

```bash
python verify_delivery.py
```

预期输出（随机 2000 张测试图）：

| 权重 | 实测 | 要求 | 结果 |
|---|---|---|---|
| mnist_weights_v1_98p18.npz | ~98.2% | ≥97.5% | PASS |
| mnist_weights.npz | ~98.3% | ≥98.0% | PASS |
| mnist_weights_big.npz | ~98.9% | ≥98.3% | PASS |
| 画布端到端 | 预测正确、置信度 > 90% | — | PASS |

> 首次运行会自动下载 MNIST（约 15MB）。没有网络时精度自检会跳过，权重仍可用。

---

## 三、核心数字

### 1. 三种输入场景

| 权重 | 干净 MNIST 测试集 | 模拟手绘（加粗+偏移） | 画布输入 + 正确预处理 |
|---|---|---|---|
| 旧 v1 | 98.18% | 70.26% | 95.75% |
| 新 784-256-128 | 98.63% | 86.15% | 97.00% |
| **新 784-512-256** | **98.98%** | **88.79%** | **97.60%** |

### 2. 同一批画布图，不同预处理方式（说明预处理是第一因素）

| 输入处理 | 旧 v1 | 新 256×128 | 新 512×256 |
|---|---|---|---|
| 直接缩放 28×28 | 9.60% | 9.30% | 9.60% |
| 只反色 | 11.50% | 13.95% | 14.10% |
| **完整预处理** | **95.75%** | **97.00%** | **97.60%** |

> 不做预处理时换任何权重都是瞎猜 —— 所以前端必须加上预处理这一步。

---

## 四、前端怎么接（三步）

### 第 1 步：选权重

| 前端现状 | 选择 |
|---|---|
| 自己实现了矩阵乘法、层宽写死 256/128 | 用 `mnist_weights.npz`，**代码零改动** |
| 层宽可配置 / 用 Python 读 npz | 推荐 `mnist_weights_big.npz`（784-512-256） |

权重文件格式保持不变：`arr_0..arr_n` 是各层权重（形状 `(in, out)`），`arr_{n+1}..` 是对应偏置。

### 第 2 步：加预处理（必须）

```python
from mnist_preprocess import prepare_canvas_digit
vec = prepare_canvas_digit(canvas_image)   # (784,) float32, 0~1
```

网页前端用 JS 版：

```js
const vec = canvasImageDataToMnist(ctx.getImageData(0, 0, canvas.width, canvas.height));
const probs = mlpPredict(vec, weights, biases);   // 10 类概率
```

预处理五步：**反色（统一黑底白字）→ 按墨迹裁剪 → 等比缩放到 20×20 → 放进 28×28 中央 → 按重心平移居中**。

### 第 3 步：验证

```bash
python predict_canvas.py 你的图.png --big
```

会打印预测类别、Top-3 置信度，并把预处理后的 28×28 图存下来供核对。

---

## 五、想重新训练 / 继续提升

```bash
python step3_mnist_improved.py     # 完整训练，约 1 小时（两份权重都会重新生成）
python finetune_clean.py --weights mnist_weights_big.npz --epochs 6 --lr 1.5e-4   # 可选微调
python verify_canvas_pipeline.py   # 复现三场景对比数字
python make_summary_chart.py       # 重新生成汇总图
```

**已知上限**：纯 NumPy 的 MLP（784-512-256）在 MNIST 上大约 99.0~99.2%；
再往上需要 CNN（本项目演示用的就是 MLP，保持可解释、无框架依赖）。
如果前端真实手写数据仍不理想，**优先检查预处理是否与 `mnist_preprocess.py` 完全一致**，
其次再考虑收集真实手写样本做针对性微调。

---

## 六、版本与兼容性

| 项 | 说明 |
|---|---|
| 权重格式 | 与旧文件一致（npz，arr_0..arr_5，权重在前偏置在后），旧代码可直接读 |
| 输入约定 | 784 维 float32，0~1，行优先（`reshape(28,28)` 一致），**黑底白字** |
| 旧权重 | 已备份为 `mnist_weights_v1_98p18.npz`，可随时回退 |
| 依赖 | Python: numpy / scikit-learn / pillow / matplotlib；JS 版无依赖 |
