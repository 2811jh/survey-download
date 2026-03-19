# survey-download

从网易问卷系统下载问卷原始数据，支持自动数据清洗。支持国内平台（survey-game.163.com）和国外平台（survey-game.easebar.com）。

## ✨ 功能

- 🔍 **按 ID 或名称搜索问卷**，快速定位目标问卷
- 📥 **一键下载**文本数据和量化数据
- 🧹 **自动清洗**：智能识别题目结构，配置服务端清洗条件
  - 答题时间过短（< 30秒）
  - 选项雷同（所有选择题选同一选项）
  - 人口学冲突（年龄-职业矛盾）
  - 满意度-NPS 逻辑矛盾
- 📦 **大文件自动处理**：自动解压分片 ZIP，合并为单个文件
- 🔐 **Cookie 自动刷新**：通过 Playwright 自动维护登录态

## 🚀 安装

### 方式一：通过 Skills CLI 安装（推荐）

```bash
npx skills add https://github.com/2811jh/survey-download
```

### 方式二：手动安装

```bash
# 克隆到你的 skills 目录
git clone https://github.com/2811jh/survey-download.git ~/.claude/skills/survey-download

# 安装 Python 依赖
pip install -r ~/.claude/skills/survey-download/requirements.txt

# （可选）安装 Playwright 以支持 Cookie 自动刷新
pip install playwright
playwright install chromium
```

## 📋 前置条件

- Python 3.8+
- 网易问卷系统账号

## 🎯 使用方式

安装后，在 Claude Code / Cursor 等 AI 编辑器中直接对话即可：

```
> 帮我下载问卷 90450 的数据
> 搜索"满意度"相关的问卷
> 清洗并下载问卷 12345
```

Skill 会自动触发并执行操作。

## 📁 文件结构

```
survey-download/
├── SKILL.md                  # Skill 主文件（元数据 + 路由）
├── references/
│   ├── download.md           # 下载详细指引
│   ├── clean.md              # 清洗详细指引
│   └── cookie.md             # Cookie 处理指引
├── survey_download.py        # 核心下载/清洗脚本
├── refresh_cookie.py         # Cookie 自动刷新脚本
└── requirements.txt          # Python 依赖
```

## 🔧 手动使用

如果不通过 AI 编辑器，也可以直接使用命令行：

```bash
# 搜索问卷（默认国内平台）
python survey_download.py search --name "满意度"

# 切换到国外平台（首次指定后自动记住）
python survey_download.py --platform intl search --name "满意度"

# 下载问卷数据
python survey_download.py download --id 90450 --output_dir "./data"

# 清洗 + 下载
python survey_download.py download --id 90450 --clean --output_dir "./data"

# 预览清洗规则
python survey_download.py clean --id 90450 --dry-run
```

## ⚠️ 首次使用

首次运行时需要登录网易问卷系统：
1. 脚本会自动打开浏览器
2. 在浏览器中完成登录
3. 登录后 session 会被缓存，后续运行自动复用

## 📄 License

MIT
