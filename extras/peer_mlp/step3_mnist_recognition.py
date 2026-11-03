"""
步骤 3：手写数字识别（MNIST）实战
本脚本使用纯 NumPy 从头实现一个多层感知机（MLP），用于对 MNIST 手写数字进行分类。

网络架构：
- 输入层：784 个神经元（28x28 像素展平）
- 隐藏层 1：256 个神经元，ReLU 激活函数
- 隐藏层 2：128 个神经元，ReLU 激活函数
- 输出层：10 个神经元（对应 0-9），Softmax 激活函数

损失函数：交叉熵损失（Cross-Entropy Loss）
优化算法：小批量随机梯度下降（Mini-batch SGD）
"""

import sys
# Windows 终端默认 GBK 编码，强制使用 UTF-8 输出，避免中文/特殊字符报错
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

import numpy as np
import matplotlib
matplotlib.use("Agg")  # 非交互后端：所有图都保存为文件
import matplotlib.pyplot as plt
from sklearn.metrics import confusion_matrix
import time
import os

# ==========================================
# 1. 激活函数及其导数定义
# ==========================================
def relu(z):
    """ReLU 激活函数"""
    return np.maximum(0, z)

def relu_derivative(z):
    """ReLU 的导数"""
    return (z > 0).astype(float)

def softmax(z):
    """
    Softmax 激活函数
    减去每行的最大值以防止数值溢出（数值稳定性）
    """
    # z 的形状为 (batch_size, num_classes)
    exp_z = np.exp(z - np.max(z, axis=1, keepdims=True))
    return exp_z / np.sum(exp_z, axis=1, keepdims=True)

# ==========================================
# 2. 神经网络类定义
# ==========================================
class NeuralNetwork:
    def __init__(self, layer_sizes):
        """
        初始化神经网络
        :param layer_sizes: 列表，包含每层的神经元个数，例如 [784, 256, 128, 10]
        """
        self.num_layers = len(layer_sizes)
        self.sizes = layer_sizes
        
        # He 权重初始化 (He Initialization)
        # 适用于 ReLU 激活函数，帮助缓解梯度消失/爆炸问题
        self.weights = []
        self.biases = []
        
        for i in range(self.num_layers - 1):
            # 权重矩阵大小：(输入神经元数, 输出神经元数)
            w = np.random.randn(self.sizes[i], self.sizes[i+1]) * np.sqrt(2.0 / self.sizes[i])
            # 偏置向量大小：(1, 输出神经元数)
            b = np.zeros((1, self.sizes[i+1]))
            
            self.weights.append(w)
            self.biases.append(b)
            
    def forward(self, X):
        """
        前向传播
        :param X: 输入数据，形状 (batch_size, input_size)
        :return: 每层的激活值(a_s)和未激活值(z_s)
        """
        a = X
        a_s = [a] # 存储所有的激活值
        z_s = []  # 存储所有的未激活值(z = a * W + b)
        
        # 遍历前 N-1 个隐藏层（使用 ReLU）
        for i in range(self.num_layers - 2):
            z = np.dot(a, self.weights[i]) + self.biases[i]
            z_s.append(z)
            a = relu(z)
            a_s.append(a)
            
        # 最后一层（输出层，使用 Softmax）
        z = np.dot(a, self.weights[-1]) + self.biases[-1]
        z_s.append(z)
        a = softmax(z)
        a_s.append(a)
        
        return z_s, a_s
        
    def backward(self, X, y_true, z_s, a_s):
        """
        反向传播（计算梯度）
        :param X: 输入数据，形状 (batch_size, input_size)
        :param y_true: 真实标签（One-hot 编码），形状 (batch_size, num_classes)
        :param z_s: 前向传播得到的未激活值列表
        :param a_s: 前向传播得到的激活值列表
        :return: 权重梯度列表和偏置梯度列表
        """
        m = X.shape[0] # 批量大小
        dw_s = [np.zeros_like(w) for w in self.weights]
        db_s = [np.zeros_like(b) for b in self.biases]
        
        # 1. 计算输出层的误差 (Softmax + Cross-Entropy 结合的导数特别简单: a - y)
        delta = a_s[-1] - y_true
        
        # 输出层的梯度
        dw_s[-1] = np.dot(a_s[-2].T, delta) / m
        db_s[-1] = np.sum(delta, axis=0, keepdims=True) / m
        
        # 2. 从后向前计算隐藏层的误差和梯度
        for l in range(2, self.num_layers):
            z = z_s[-l]
            sp = relu_derivative(z)
            # 当前层的误差 = (上一层的误差 乘 上一层权重的转置) 逐元素乘 当前层激活函数的导数
            delta = np.dot(delta, self.weights[-l+1].T) * sp
            
            # 计算当前层的梯度
            dw_s[-l] = np.dot(a_s[-l-1].T, delta) / m
            db_s[-l] = np.sum(delta, axis=0, keepdims=True) / m
            
        return dw_s, db_s
        
    def update_params(self, dw_s, db_s, learning_rate):
        """
        使用 SGD 更新网络参数
        """
        for i in range(len(self.weights)):
            self.weights[i] -= learning_rate * dw_s[i]
            self.biases[i] -= learning_rate * db_s[i]
            
    def compute_loss(self, y_pred, y_true):
        """
        计算交叉熵损失
        """
        m = y_true.shape[0]
        # 添加极小值 epsilon 防止 log(0) 导致的数值错误
        epsilon = 1e-15
        y_pred = np.clip(y_pred, epsilon, 1 - epsilon)
        loss = -np.sum(y_true * np.log(y_pred)) / m
        return loss

    def predict(self, X):
        """
        预测样本的类别
        """
        _, a_s = self.forward(X)
        return np.argmax(a_s[-1], axis=1)
        
    def save(self, filepath):
        """保存模型权重"""
        np.savez(filepath, *self.weights, *self.biases)
        print(f"模型权重已保存至 {filepath}")
        
    def load(self, filepath):
        """加载模型权重"""
        data = np.load(filepath)
        num_w = len(self.weights)
        for i in range(num_w):
            self.weights[i] = data[f'arr_{i}']
            self.biases[i] = data[f'arr_{i+num_w}']
        print(f"成功加载模型权重：{filepath}")


# ==========================================
# 3. 辅助函数：数据处理与可视化
# ==========================================
def to_one_hot(y, num_classes=10):
    """将标签转换为 One-hot 编码"""
    y_one_hot = np.zeros((y.size, num_classes))
    y_one_hot[np.arange(y.size), y] = 1.0
    return y_one_hot

def get_batches(X, y, batch_size):
    """生成小批量数据"""
    indices = np.arange(X.shape[0])
    np.random.shuffle(indices)
    
    for start_idx in range(0, X.shape[0] - batch_size + 1, batch_size):
        batch_idx = indices[start_idx:start_idx + batch_size]
        yield X[batch_idx], y[batch_idx]

def plot_samples(X, y, title="Sample Digits"):
    """显示一些样本数字 (4x5 区域)"""
    img_size = int(np.sqrt(X.shape[1]))  # 动态计算图像尺寸（28 或 8）
    fig, axes = plt.subplots(4, 5, figsize=(10, 8))
    fig.suptitle(title, fontsize=16)
    
    for i, ax in enumerate(axes.flat):
        ax.imshow(X[i].reshape(img_size, img_size), cmap='gray')
        ax.set_title(f"Label: {y[i]}")
        ax.axis('off')
        
    plt.tight_layout()
    plt.savefig(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'sample_digits.png'), dpi=150)
    plt.close()

# ==========================================
# 主程序
# ==========================================
def main():
    # 确保保存模型的目录存在
    save_dir = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(save_dir, exist_ok=True)
    
    # ---------------------------------------------------------
    # 第一步：数据加载和预处理
    # 支持两种模式：
    #   模式 A：完整 MNIST (28x28=784维, 70000样本) — 需要联网下载
    #   模式 B：sklearn 内置 digits (8x8=64维, 1797样本) — 无需下载，秒级加载
    # ---------------------------------------------------------
    use_full_mnist = False
    
    # 尝试加载完整 MNIST
    try:
        from sklearn.datasets import fetch_openml
        print("正在尝试加载完整 MNIST 数据集 (28x28)...")
        print("（如果下载超过 30 秒将自动切换到内置 digits 数据集）")
        
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(fetch_openml, 'mnist_784', version=1, as_frame=False)
            try:
                mnist = future.result(timeout=30)  # 30 秒超时
                X = mnist.data.astype('float32')
                y = mnist.target.astype('int64')
                X = X / 255.0
                use_full_mnist = True
                print("[OK] 完整 MNIST 数据集加载成功！")
            except concurrent.futures.TimeoutError:
                print("[X] MNIST 下载超时，切换到内置 digits 数据集...")
    except Exception as e:
        print(f"[X] MNIST 加载失败 ({e})，切换到内置 digits 数据集...")
    
    # 如果 MNIST 加载失败，使用内置 digits 数据集
    if not use_full_mnist:
        from sklearn.datasets import load_digits
        digits = load_digits()
        X = digits.data.astype('float32')
        y = digits.target.astype('int64')
        # 归一化到 [0, 1]
        X = X / X.max()
        print(f"[OK] 内置 digits 数据集加载成功！(8x8 图像, {X.shape[0]} 个样本)")
        print("  注：这是简化版数据集，用于快速验证算法流程。")
        print("  如需完整 MNIST，请确保网络畅通后重新运行。")
    
    input_size = X.shape[1]  # 784 (MNIST) 或 64 (digits)
    
    # One-hot 编码标签
    y_one_hot = to_one_hot(y)
    
    # 划分训练集和测试集
    if use_full_mnist:
        X_train, X_test = X[:60000], X[60000:]
        y_train, y_test = y_one_hot[:60000], y_one_hot[60000:]
        y_train_labels, y_test_labels = y[:60000], y[60000:]
    else:
        # digits 数据集较小，用 80/20 划分
        split = int(0.8 * X.shape[0])
        indices = np.random.permutation(X.shape[0])
        train_idx, test_idx = indices[:split], indices[split:]
        X_train, X_test = X[train_idx], X[test_idx]
        y_train, y_test = y_one_hot[train_idx], y_one_hot[test_idx]
        y_train_labels, y_test_labels = y[train_idx], y[test_idx]
    
    print(f"\n训练集大小: {X_train.shape[0]}, 输入维度: {input_size}")
    print(f"测试集大小: {X_test.shape[0]}")
    
    # 随机展示 20 个样本
    random_indices = np.random.choice(X_train.shape[0], 20, replace=False)
    plot_samples(X_train[random_indices], y_train_labels[random_indices], "Training Samples")

    # ---------------------------------------------------------
    # 第二步：构建神经网络和配置训练参数
    # 根据数据集自动选择网络架构
    # ---------------------------------------------------------
    if use_full_mnist:
        layer_sizes = [784, 256, 128, 10]  # 完整 MNIST 架构
    else:
        layer_sizes = [64, 128, 64, 10]    # digits 简化架构
    
    nn = NeuralNetwork(layer_sizes)
    print(f"\n网络架构: {layer_sizes}")
    
    # 计算图像尺寸（用于后续可视化）
    img_size = int(np.sqrt(input_size))  # 28 (MNIST) 或 8 (digits)
    
    # 训练参数
    epochs = 20
    batch_size = 64
    initial_lr = 0.1
    
    # 记录训练过程中的指标
    history = {
        'train_loss': [],
        'train_acc': [],
        'test_acc': []
    }
    
    # ---------------------------------------------------------
    # 第三步：模型训练 (Mini-batch SGD)
    # ---------------------------------------------------------
    print("\n开始训练网络...")
    start_time = time.time()
    
    for epoch in range(epochs):
        # 学习率衰减（可选）：每 10 个 epoch 减半
        lr = initial_lr * (0.5 ** (epoch // 10))
        
        train_loss = 0.0
        batches = 0
        
        # 遍历所有小批量
        for X_batch, y_batch in get_batches(X_train, y_train, batch_size):
            # 1. 前向传播
            z_s, a_s = nn.forward(X_batch)
            
            # 2. 反向传播（计算梯度）
            dw_s, db_s = nn.backward(X_batch, y_batch, z_s, a_s)
            
            # 3. 参数更新
            nn.update_params(dw_s, db_s, learning_rate=lr)
            
            # 累加损失
            train_loss += nn.compute_loss(a_s[-1], y_batch)
            batches += 1
            
        # 计算该 Epoch 的平均训练损失
        avg_train_loss = train_loss / batches
        history['train_loss'].append(avg_train_loss)
        
        # 计算训练集和测试集准确率
        train_preds = nn.predict(X_train)
        train_acc = np.mean(train_preds == y_train_labels)
        history['train_acc'].append(train_acc)
        
        test_preds = nn.predict(X_test)
        test_acc = np.mean(test_preds == y_test_labels)
        history['test_acc'].append(test_acc)
        
        print(f"Epoch {epoch+1:02d}/{epochs} | LR: {lr:.4f} | Loss: {avg_train_loss:.4f} | "
              f"Train Acc: {train_acc*100:.2f}% | Test Acc: {test_acc*100:.2f}%")
              
    end_time = time.time()
    print(f"\n训练结束！总耗时: {end_time - start_time:.2f} 秒")
    
    # 保存模型权重
    model_path = os.path.join(save_dir, 'mnist_weights.npz')
    nn.save(model_path)
    
    # ---------------------------------------------------------
    # 第四步：评估与可视化
    # ---------------------------------------------------------
    # 1. 绘制训练损失和准确率曲线
    plt.figure(figsize=(12, 5))
    
    plt.subplot(1, 2, 1)
    plt.plot(history['train_loss'], label='Train Loss', color='red', marker='o')
    plt.title('Training Loss per Epoch')
    plt.xlabel('Epoch')
    plt.ylabel('Loss')
    plt.grid(True)
    plt.legend()
    
    plt.subplot(1, 2, 2)
    plt.plot(history['train_acc'], label='Train Acc', color='blue', marker='s')
    plt.plot(history['test_acc'], label='Test Acc', color='green', marker='^')
    plt.title('Accuracy per Epoch')
    plt.xlabel('Epoch')
    plt.ylabel('Accuracy')
    plt.grid(True)
    plt.legend()
    
    plt.tight_layout()
    plt.savefig(os.path.join(save_dir, 'training_curves.png'), dpi=150)
    plt.close()
    print("训练曲线已保存: training_curves.png")
    
    # 2. 混淆矩阵
    cm = confusion_matrix(y_test_labels, test_preds)
    plt.figure(figsize=(8, 8))
    plt.imshow(cm, interpolation='nearest', cmap=plt.cm.Blues)
    plt.title('Confusion Matrix on Test Set')
    plt.colorbar()
    tick_marks = np.arange(10)
    plt.xticks(tick_marks, tick_marks)
    plt.yticks(tick_marks, tick_marks)
    
    # 在混淆矩阵中添加数字标签
    thresh = cm.max() / 2.
    for i in range(cm.shape[0]):
        for j in range(cm.shape[1]):
            plt.text(j, i, format(cm[i, j], 'd'),
                     horizontalalignment="center",
                     color="white" if cm[i, j] > thresh else "black")
                     
    plt.ylabel('True label')
    plt.xlabel('Predicted label')
    plt.tight_layout()
    plt.savefig(os.path.join(save_dir, 'confusion_matrix.png'), dpi=150)
    plt.close()
    print("混淆矩阵已保存: confusion_matrix.png")
    
    # 3. 按类别（每个数字）计算准确率
    print("\n各数字类别准确率：")
    for i in range(10):
        digit_indices = (y_test_labels == i)
        digit_acc = np.mean(test_preds[digit_indices] == y_test_labels[digit_indices])
        print(f"数字 {i}: {digit_acc*100:.2f}%")
        
    print(f"\n最终测试集总准确率: {history['test_acc'][-1]*100:.2f}%")
    
    # 4. 展示 20 个测试集预测结果，高亮错误预测
    fig, axes = plt.subplots(4, 5, figsize=(12, 10))
    fig.suptitle('Test Set Predictions (Red=Incorrect)', fontsize=16)
    
    test_sample_indices = np.random.choice(X_test.shape[0], 20, replace=False)
    for i, ax in enumerate(axes.flat):
        idx = test_sample_indices[i]
        img = X_test[idx].reshape(img_size, img_size)
        true_label = y_test_labels[idx]
        pred_label = test_preds[idx]
        
        ax.imshow(img, cmap='gray')
        color = 'green' if true_label == pred_label else 'red'
        ax.set_title(f"Pred: {pred_label} | True: {true_label}", color=color)
        ax.axis('off')
        
    plt.tight_layout()
    plt.savefig(os.path.join(save_dir, 'test_predictions.png'), dpi=150)
    plt.close()
    print("预测结果已保存: test_predictions.png")

    # ---------------------------------------------------------
    # 第五步：交互式预测演示（展示预测置信度）
    # ---------------------------------------------------------
    print("\n展示交互式预测演示 (图像 + 置信度分布)...")
    demo_indices = np.random.choice(X_test.shape[0], 10, replace=False)
    
    for i, idx in enumerate(demo_indices):
        x_input = X_test[idx:idx+1] # 保持二维形状 (1, 784)
        true_label = y_test_labels[idx]
        
        # 前向传播得到概率分布
        _, a_s = nn.forward(x_input)
        probs = a_s[-1][0]
        pred_label = np.argmax(probs)
        
        fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(10, 4))
        
        # 左侧显示数字图像
        ax1.imshow(x_input.reshape(img_size, img_size), cmap='gray')
        color = 'green' if true_label == pred_label else 'red'
        ax1.set_title(f"Prediction: {pred_label} (True: {true_label})", color=color)
        ax1.axis('off')
        
        # 右侧显示各个类别的概率（置信度）条形图
        classes = np.arange(10)
        bars = ax2.bar(classes, probs, color='gray')
        bars[pred_label].set_color('red' if true_label != pred_label else 'green')
        
        ax2.set_xticks(classes)
        ax2.set_title("Network Confidence (Softmax Probabilities)")
        ax2.set_ylim(0, 1.1)
        ax2.grid(axis='y', linestyle='--', alpha=0.7)
        
        plt.tight_layout()
        plt.savefig(os.path.join(save_dir, f'confidence_demo_{i}.png'), dpi=100)
        plt.close()
    
    print(f"置信度演示图已保存: confidence_demo_0.png ~ confidence_demo_9.png")

if __name__ == "__main__":
    main()
