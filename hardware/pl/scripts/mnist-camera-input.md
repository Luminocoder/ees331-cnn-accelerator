# DECXIN 拍照、预处理与 PC 串口发送

当前阶段：PC 端实现及离线测试；不是相机直连 Zynq，也不是板端闭环已完成。
代码不修改 FPGA 寄存器、网络、权重或队友 A 的 ARM 驱动。

## 1. 文件与依赖

- `mnist_camera_input.py`：拍照、生成 MNIST 风格输入、发送命令。
- `mnist_serial.py`：实验性数据帧、CRC、握手、超时和结果校验。
- `requirements-camera.txt`：NumPy、带 GUI 的 OpenCV、pyserial。
- `tests/test_mnist_camera_input.py`：不访问硬件的自动化测试及合成示例。

从仓库根目录执行：

```powershell
python -m pip install -r scripts/requirements-camera.txt
```

本次环境已有 OpenCV 5.0.0、NumPy 2.5.3，尚缺 pyserial。拍照、预处理和 dry-run
不依赖 pyserial；真正打开串口前才需要它。脚本不会自动安装依赖或驱动。
不要同时安装互相冲突的 `opencv-python` 和 `opencv-python-headless`；交互式拍照/选框需要 GUI。

## 2. 拍一张数字照片

建议白纸黑笔，只放一个数字，背景简单、光照均匀。先关闭可能占用相机的软件。
相机索引必须显式指定，`0` 仅是此前成功记录，不保证重连后不变。
`first` 和 `second` 指拼接图的前半/后半，不代表已确定物理左/右镜头。

```powershell
python scripts/mnist_camera_input.py capture --camera-index 0 --eye first --output ../mnist-camera-runs/take01/photo
```

窗口显示选定一眼的实时图像，摆好数字后按空格拍照，ESC 或关窗口取消。
首次使用先遮挡一个镜头，确认当前画面确实是 DECXIN 及所选镜头。
脚本请求 MJPG、2560x800 和 30 FPS，只检查实际图像尺寸，不宣称达到该帧率或验证 USB 总线格式。
如果收到的不是 2560x800，会拒绝保存；检查相机索引、占用及实际支持模式。

无需预览时可以显式添加 `--headless`，预热若干帧后直接保存一张；不要在未对准数字时使用。
`--warmup-frames` 默认 8。驱动的 `VideoCapture.read()` 仍可能阻塞，当前未提供进程级相机超时保护。

输出：

- `raw-stereo.png`：相机解码后原尺寸拼接图，无额外 JPEG 重编码。
- `eye.png`：选定的一眼，1280x800。
- `capture.json`：索引、图像尺寸、请求设置及验证边界。

拍摄数据默认由命令写到仓库外的 `../mnist-camera-runs/`，避免把现场图像意外提交到共享仓库。
输出目录必须不存在；重拍请换 `take02` 等新名字，不会覆盖旧照片。

## 3. 选数字区域并预处理

```powershell
python scripts/mnist_camera_input.py prepare --input ../mnist-camera-runs/take01/photo/eye.png --select-roi --polarity dark --output ../mnist-camera-runs/take01/prepared
```

在静态照片上拖框，仅包含一个数字及少量周围背景，然后按 Enter 或空格确认。
不要框住纸边、手指或其他文字，也不要裁断笔画；取消选择不会生成输入。
也可以用 `--roi X Y W H` 代替交互选择，坐标相对于所输入图片，而不是原始双目拼接图。

`--polarity dark` 用于白底黑字；黑底亮字使用 `light`；`auto` 依据 ROI 边缘亮度猜测，
有阴影或复杂背景时应显式指定。`prepare` 也接受普通 PNG/JPEG，不局限于本脚本拍摄的文件。

流程：灰度化 -> 按指定极性提取亮笔画 -> Otsu 阈值定位最大连通数字 ->
裁剪 -> 保持比例缩放至最长边 20 像素 -> 放入 28x28 黑底 -> 质心居中。
空白、低对比度、前景过满、越界 ROI、多块大前景或紧贴 ROI 边缘会报错。
当前算法面向简单背景下的单个连通数字；断裂笔画、强阴影、纸框可能需要重新选框/拍摄。
这只是采集输入的预处理基线，没有通过真实手写照片准确率评测，不承诺与 MNIST 测试集相同准确率。

输出：

- `mnist28.png`：黑底亮字的 28x28 预览，**发送前先检查它是否仍像原数字**。
- `foreground.png`：提取出的数字前景，供排查裁剪/背景问题。
- `pixels-u8.bin`：按行排列的 784 字节，像素范围 0–255，尚未执行 `>> 1`。
- `sample.json`：输入语义、算法、来源及像素 SHA-256。

**量化只执行一次**：现有 `cnn_write_image()` 接收上述 uint8 像素并右移一位。
不要在 PC 端先右移再调用这个函数，也不要直接把此 uint8 负载原样写入现有有符号 MAC 输入。

## 4. 先验证发送数据，不连接板卡

```powershell
python scripts/mnist_camera_input.py send --sample ../mnist-camera-runs/take01/prepared --dry-run --packet-output ../mnist-camera-runs/take01/image-request.bin
```

会校验样本清单和像素哈希，生成 800 字节实验数据帧并打印摘要。
dry-run 不导入 pyserial、不打开任何串口，也不计算识别结果。
`--packet-output` 文件必须不存在；不指定它时仅打印摘要。

## 5. 真正发送前的条件

**当前 ARM 初稿没有接收此协议的程序；现阶段不要去掉 dry-run 对着旧固件发送。**
协议是 PC 端实验提案，尚未由三人确认，也未写入 `docs/interfaces/`。
接收端开发清单见
[UART 草案交接](../docs/handoffs/mnist-camera-uart-draft-2026-10-19.md)。
确认协议并部署匹配的 ARM 接收程序、完成基本 UART/DDR/PL 验证后才使用：

```powershell
python scripts/mnist_camera_input.py send --sample ../mnist-camera-runs/take01/prepared --port COM5 --baud 115200 --acknowledge-draft-protocol
```

COM5 是此前板载串口记录，不是永久标识；使用前确认实际端口并关闭 PuTTY 等占用者。
使用板载 USB 串口桥，不把摄像头插到串口桥，也不直接接未经确认电平的裸 UART 引脚。

发送器先发 HELLO，只有收到匹配版本、身份、序号和 CRC 的 ACK 后才发送图片。
返回值为十路有符号 INT32 logits 和 argmax 数字，校验成功后打印 JSON。
接收端错误、坏 CRC、错误序号、错误 argmax 或超时会返回非零退出码；不自动重发图片，避免重复推理。
默认每次请求/应答 3 秒，可通过 `--timeout` 设置，范围 (0,60]；串口设置为 8N1、无流控。
操作系统打开串口仍可能影响控制线，脚本不主动脉冲复位；首次联调保存启动状态并核对板载桥接线。
脚本不会下载 bit/ELF、执行 PS 初始化、改跳帽或检查当前固件是否已经通过所有硬件门禁。

## 6. 自动化回归

```powershell
python -m unittest discover -s scripts/tests -p test_mnist_camera_input.py -v
python scripts/tests/test_mnist_camera_input.py --artifacts metrics/logs/my-new-camera-test-run
```

第二条要求新的目录，保存原始测试日志、源码哈希、summary.json 和合成数字示例。
模拟串口返回固定分数，不运行 CNN，不可当作板端推理或真实照片识别证据。
不需要修改或运行现有 RTL/Vitis 构建即可完成这些 PC 测试。
