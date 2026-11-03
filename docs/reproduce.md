# Windows复现与构建

## 环境

Windows x64，建议标准Python3.13（含Tcl/Tk8.6），NumPy、OpenCV及Pillow。`requirements-verified.txt`记录原构建实际版本；`requirements.txt`给出安装范围，其他版本需重新执行回归。PyInstaller6.22.3为原构建版本。Vivado2025.1及XSDB/hw_server、LLVM22.1.8、USB/JTAG/虚拟串口驱动需另外安装，不随包分发。

先将本目录复制到可写ASCII路径，如`C:\ees331\project`；不要直接在唯一原件运行会更新integration的脚本。Vivado工作目录也须为ASCII。所有相对路径以此代码目录为根。

## 源运行与离线验证

```powershell
python -m pip install -r requirements.txt
python -B -X utf8 source/ees331_studio.py --offline
python -B -X utf8 scripts/verify_release.py
python -B -X utf8 scripts/validate_release.py --output validation
```

`--offline`明确禁止从界面初始化硬件。源码regression会使用真实C固件的主机DLL模拟协议，但时间/MMIO是模拟的，不产生硬件加速比。若DLL未随Git克隆提供，验证脚本按本机LLVM重建。

## PL重建

```powershell
python -B -X utf8 hardware/generate_conv_vectors.py
python -B -X utf8 hardware/build_board.py --work-root C:/ees331/build --vivado-bin D:/2025.1/Vivado/bin
python -B -X utf8 hardware/install_pl.py
```

先运行4项RTL仿真，再综合布局布线和生成bit/XSA。只验证RTL可加`--sim-only`，但它不会生成可安装位流。检查每项PASS、200路固定分数/50路额外分数、28,833个标量卷积输出、复位恢复、数码管扫描和时序/DRC；不能只看返回码。

原配套XSA位于`prebuilt/system.xsa`；也可从Tcl重建，不需要原机器缓存或旧版本目录。

## ARM重建

```powershell
python -B -X utf8 hardware/build_firmware.py --llvm-bin 'C:/Program Files/LLVM/bin'
python -B -X utf8 hardware/install_firmware.py
```

先冻结同一模型/20输入/金标准，使用Cortex-A9单核标量C、-O2、无LTO/NEON构建。安装bit或ELF后必须重新打包EXE，不能仅替换外层资源。

## EXE重建

```powershell
python -m pip install -r requirements-build.txt
python -B -X utf8 build_candidate.py
python -B -X utf8 scripts/validate_release.py --output validation
```

原打包器支持`--pyinstaller-deps`与`--tk-root`用于隔离依赖。输出`EES331_Studio.exe`及`_internal/`，Windows可同名访问本目录小写EXE。会新建临时`_build_v5`并更新build-manifest.json；核对后可删除临时构建。新构建需重新生成交付完整性清单；不能靠改历史证据哈希让新程序伪装成旧实测程序。

## 真实板卡验收

接USB供电/数据并打开开关，选择枚举出的COM口。在免安装程序目录运行GUI，初始化成功后先板子自检，再性能对比；以下命令也在免安装程序目录执行，必须先关闭GUI占口：

```powershell
.\ees331_studio.exe --initialize --port COM5
.\ees331_studio.exe --board-test --port COM5
.\ees331_studio.exe --benchmark --port COM5
```

COM5仅是示例；失败查新会话日志。断电后重新初始化。性能应有20校验、10预热、100正式配对、CRC/分数/时基/环境及恢复通过。相机不是测速必需依赖。最后回归相机单字、双字与连拍，目视检查空位全灭、末页及5秒分页；ACK不能代替无残影的实物观察。

## 模型来源与重新训练

`hardware/pl/ref_model/train_cnn_numpy.py`、量化/偏置导出和标定脚本保留原样。训练数据需另从MNIST官方来源取得，四份IDX gzip不随包附带。从`hardware/pl/ref_model`目录运行，`weights_fp32/`已补入同一冻结参数。重新训练、量化或调移位会改变模型，必须重建金标准、ARM常量与PL，再全链路验收；不能继续引用当前2.1011倍实测结果。

`extras/peer_mlp/`是独立MLP实验，依赖及历史结果见其说明，不得将其准确率或浮点权重作为FPGA CNN成绩。

本说明经当前整理副本回归，但尚未由第二台干净电脑从零重建并上板。其他工具版本和其他操作系统未验证。
