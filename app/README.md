# AI驱动的业务高效运转系统

基于 Python + Flask + SQLite 的轻量级业务管理系统，适用于内网 Windows 环境部署。

## 系统功能

系统覆盖通信模组生产物料管理的完整业务流程，包含五大核心模块：

| 模块 | 功能说明 |
|------|----------|
| **需求数据库** | 多部门需求录入、真实性验证、优先级评审、状态跟踪 |
| **产品规划** | 产品定义、BOM物料清单管理（关键料/通用料标注）、备货策略、生命周期管理 |
| **研发生产** | 库存管理（公司库房/供应商暂存）、物料领用审批、生产订单管理、WIP进度跟踪 |
| **质量检测** | IQC进料检验、IPQC生产巡检、OQC回货测试、良品率自动计算、不合格品8D追溯 |
| **验证收益** | 入库审批、多市场团队领用交付、客户反馈记录、数据闭环 |

附加功能：系统仪表盘（统计概览+业务流程图）、操作日志审计、低库存预警。

## 技术栈

- **后端**: Python 3.8+ / Flask 3.0
- **数据库**: SQLite 3（单文件，无需额外安装）
- **前端**: 原生 HTML + CSS + JavaScript（无框架依赖）
- **部署**: 纯 Python 环境，支持 Windows / Linux / macOS

## 目录结构

```
business-system/
├── app.py                  # Flask主应用（路由+业务逻辑）
├── init_db.py              # 数据库初始化脚本（含示例数据）
├── schema.sql              # 数据库表结构定义
├── requirements.txt        # Python依赖
├── README.md               # 部署说明
├── business.db             # SQLite数据库文件（运行后自动生成）
├── templates/              # HTML模板
│   ├── base.html           # 基础布局（侧边栏+导航）
│   ├── index.html          # 仪表盘首页
│   ├── requirements.html   # 需求列表
│   ├── requirement_form.html
│   ├── planning.html       # 产品列表
│   ├── product_form.html   # 产品&BOM表单
│   ├── production.html     # 研发生产（订单/库存/领用三Tab）
│   ├── inventory_form.html
│   ├── requisition_form.html
│   ├── production_order_form.html
│   ├── quality.html        # 质量检测（检验/缺陷两Tab）
│   ├── quality_form.html
│   ├── delivery.html       # 验证收益（入库/交付两Tab）
│   ├── receipt_form.html
│   ├── deliver_form.html
│   └── logs.html           # 操作日志
└── static/
    ├── css/style.css       # 样式文件
    └── js/app.js           # 前端交互脚本
```

## 快速开始（推荐：双击启动）

只需 2 步，无需敲任何命令：

### 第 1 步：安装 Python（仅首次需要）

下载 Python 3.8+：https://www.python.org/downloads/windows/
安装时**务必勾选 "Add Python to PATH"**，然后一路下一步即可。

### 第 2 步：双击启动

解压后进入 `business-system` 文件夹，**双击 `启动系统.bat`**：

- 脚本会自动检查 Python 环境
- 自动安装依赖包（首次运行约 1-2 分钟）
- 自动初始化数据库（含示例数据）
- 自动启动系统并打开浏览器

启动成功后浏览器会自动打开 `http://127.0.0.1:5000`，关闭黑色命令行窗口即可停止系统。

**两种启动方式可选：**

| 文件 | 说明 | 适用场景 |
|------|------|----------|
| `启动系统.bat` | 显示命令行窗口，可看到运行日志 | 日常使用、调试 |
| `后台启动(无窗口).vbs` | 隐藏窗口，后台静默运行 | 长期运行、服务器部署 |

> **修改端口**：用记事本打开 `config.py`，修改 `PORT = 5000` 为其他端口即可。

---

## Windows 内网部署步骤（手动方式，供参考）

### 第一步：安装 Python 环境

1. 下载 Python 3.8+ 安装包：https://www.python.org/downloads/windows/
2. 运行安装程序，**务必勾选 "Add Python to PATH"**
3. 安装完成后，打开命令提示符（CMD），验证安装：
   ```cmd
   python --version
   pip --version
   ```

### 第二步：部署应用文件

1. 将 `business-system` 文件夹复制到目标服务器，例如：`C:\business-system\`
2. 打开 CMD，进入项目目录：
   ```cmd
   cd C:\business-system
   ```

### 第三步：安装依赖

```cmd
pip install -r requirements.txt
```

> 内网环境无法联网时，先在有网机器上下载依赖包：
> ```cmd
> pip download -r requirements.txt -d packages
> ```
> 将 `packages` 文件夹复制到内网服务器，然后：
> ```cmd
> pip install --no-index --find-links=packages -r requirements.txt
> ```

### 第四步：初始化数据库

```cmd
python init_db.py
```

该命令会创建 `business.db` 数据库文件，并插入示例数据（可直接体验系统功能）。

> 如需清空数据重新初始化，删除 `business.db` 文件后重新运行 `python init_db.py`。

### 第五步：启动应用

```cmd
python app.py
```

启动成功后会显示：
```
============================================================
  AI驱动的业务高效运转系统
  本机访问: http://127.0.0.1:5000
  内网访问: http://<本机IP>:5000
  调试模式: 开启
  端口配置: 命令行--port > 环境变量BUSINESS_PORT > config.py > 默认5000
============================================================
```

**端口号可配置**（管理员按需修改），三种方式：
1. **配置文件**（推荐）：编辑 `config.py`，修改 `PORT = 5000` 为目标端口
2. **命令行参数**：`python app.py --port 8080`
3. **环境变量**：`set BUSINESS_PORT=8080 && python app.py`

生产环境建议关闭调试模式：`python app.py --no-debug`

### 第六步：访问系统

- **本机访问**: 浏览器打开 http://127.0.0.1:5000
- **内网访问**: 浏览器打开 http://服务器IP:5000 （如 http://192.168.1.100:5000）

> 确保 Windows 防火墙放行 5000 端口：
> ```cmd
> netsh advfirewall firewall add rule name="BusinessSystem" dir=in action=allow protocol=TCP localport=5000
> ```

## 后台运行（生产环境推荐）

### 方式一：使用 nohup（Linux）

```bash
nohup python app.py > app.log 2>&1 &
```

### 方式二：Windows 后台运行

创建 `start.bat` 文件：
```bat
@echo off
cd /d C:\business-system
python app.py
pause
```

双击 `start.bat` 即可启动。如需后台运行（不显示窗口），可使用 `pythonw.exe`：
```bat
@echo off
cd /d C:\business-system
start pythonw app.py
```

### 方式三：注册为 Windows 服务（推荐）

使用 `nssm` 工具将应用注册为 Windows 服务，实现开机自启动：

1. 下载 nssm: https://nssm.cc/download
2. 解压后运行：
   ```cmd
   nssm install BusinessSystem "C:\Python39\python.exe" "C:\business-system\app.py"
   nssm set BusinessSystem AppDirectory "C:\business-system"
   nssm start BusinessSystem
   ```

## 数据备份

SQLite 数据库为单文件 `business.db`，备份只需复制该文件：

```cmd
copy business.db backup_%date:~0,4%%date:~5,2%%date:~8,2%.db
```

建议定期备份，可通过 Windows 任务计划程序设置每日自动备份。

## 常见问题

**Q: 应用启动成功但浏览器无法访问（显示ERR_UNSAFE_PORT或无法访问）？**
A: 这是浏览器的安全端口限制。Chrome/Edge 会拦截 6000、6665 等端口。请使用 5000、8000、8888、8899、9000 等端口：
```cmd
python app.py --port 5000
```
或编辑 `config.py` 修改 `PORT = 5000`。同时确保 Windows 防火墙已放行该端口。

**Q: 端口 5000 被占用怎么办？**
A: 端口号可配置，无需改源码。三种方式任选：
- 编辑 `config.py`，修改 `PORT = 8080`
- 启动时指定：`python app.py --port 8080`
- 设置环境变量：`set BUSINESS_PORT=8080 && python app.py`

**Q: 如何修改为生产模式（关闭debug）？**
A: 启动时加参数即可，无需改源码：
```cmd
python app.py --no-debug
```
或编辑 `config.py`，设置 `DEBUG = False`。

**Q: 数据库文件在哪里？**
A: 与 `app.py` 同目录下的 `business.db` 文件。

**Q: 支持多用户同时使用吗？**
A: SQLite 支持多用户读取，但写入时会锁库。如果并发写入较多（超过10人同时操作），建议迁移到 MySQL/PostgreSQL。

**Q: 如何重置管理员密码？**
A: 当前版本为内网轻量版，未内置用户认证。如需登录认证，可在 `app.py` 中添加 Flask-Login 扩展。

## 业务流程参考

```
需求数据库 → 产品规划 → 研发生产 → 质量检测 → 验证收益
    ↑                                            ↓
    └──────── 数据闭环（客户反馈/订单收益）────────┘
```

**备货策略**: 不囤货，依据用户需求再进行生产。关键物料由公司提供，其他通用物料由生产供应商自行采购备货。

---

*系统版本: v1.0 | 最后更新: 2026-09*
