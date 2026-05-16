# 微信账号状态检查工具 v1.2

## 功能
自动检查微信号状态（是否存在头像和昵称），发现异常时弹窗+警告音并停止。

## 运行环境
- Windows 10+
- 微信 PC 客户端 (当前版本: 4.1.9.4)
- Python 3.12+（如使用源码运行）

## 使用方法

### 方式一：直接运行 exe（推荐）
1. 从 `dist/WeChatChecker.exe` 运行程序
2. 编辑 `wechat_ids.txt`，每行一个微信号
3. 打开微信并登录
4. 运行程序，配置参数后点击"开始检查"

### 方式二：源码运行
```bash
pip install -r requirements.txt
python main.py
```

### 自定义打包
```bash
build_exe.bat
```

## 文件结构
```
自动检查v/
├── main.py                  # GUI主程序
├── wechat_controller.py     # 微信自动化操作
├── checker_engine.py        # 检查引擎
├── config_manager.py        # 配置管理
├── logger_setup.py          # 日志模块
├── config.json              # 配置文件
├── wechat_ids.txt           # 微信号列表
├── generate_icon.py         # 图标生成
├── requirements.txt         # Python依赖
├── build_exe.bat            # 打包脚本
└── logs/                    # 日志目录
```

## 注意事项
- 运行前确保微信已登录且窗口未最小化
- 微信窗口可以被其他窗口遮挡
- 每个微信号检查间隔 3-5 秒随机
- 超过每批数量后，批次间间隔 30-50 分钟随机
- 所有号检查完成后自动进入下一轮循环
