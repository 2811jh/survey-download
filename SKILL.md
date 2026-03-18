---
name: survey_download
description: |
  从网易问卷系统（survey-game.163.com）下载问卷原始数据和自动数据清洗。
  通过问卷 ID 或名称定位问卷，导出文本数据和量化数据。
  支持自动配置清洗条件（剔除无效问卷：答题过快、选项雷同、人口学冲突、满意度-NPS矛盾）。
  当用户提到"下载问卷"、"导出问卷数据"、"帮我下数据"、"把问卷数据下下来"、
  "清洗问卷"、"筛选问卷数据"、"帮我筛一下"等场景时使用。
  即使用户没有明确说"网易问卷"，只要涉及问卷数据的下载、导出、清洗、筛选就应触发。
---

# Survey Download

从网易问卷系统下载原始数据，支持自动清洗。

## 环境

- **Python**: 系统默认 `python`（如不可用，尝试 `python3`）
- **脚本目录**: `{SKILL_DIR}/`
- **核心依赖**: `requests`（必须）；`pandas`+`openpyxl`（大文件合并）；`playwright`（Cookie 自动刷新）
- **依赖安装**: `pip install -r {SKILL_DIR}/requirements.txt`

## 任务路由

根据用户意图，**只读取当前任务需要的 reference 文档**：

| 用户意图 | 读取文档 | 示例表达 |
|----------|----------|----------|
| 下载问卷数据 | `{SKILL_DIR}/references/download.md` | "下载问卷90450"、"帮我下2月满意度的数据" |
| 清洗/筛选数据 | `{SKILL_DIR}/references/clean.md` | "清洗一下这份问卷"、"帮我筛选数据" |
| 清洗并下载 | 先读 `clean.md` 完成确认，再读 `download.md` 执行下载 | "清洗并下载问卷xxx" |
| Cookie 问题 | `{SKILL_DIR}/references/cookie.md` | "登录过期了"、"Cookie 怎么更新" |

## 快速开始

大多数场景只需两步：

```bash
# 1. 检查认证（失败时脚本自动刷新 Cookie）
python {SKILL_DIR}/survey_download.py check

# 2. 下载数据
python {SKILL_DIR}/survey_download.py download --id 问卷ID --output_dir "目录"
```

搜索问卷（按名称定位时）：
```bash
python {SKILL_DIR}/survey_download.py search --name "关键词"
```

多个匹配时用 `ask_user_question` 让用户选择，然后用选定的 `--id` 下载。

## 通用注意事项

1. stdout 输出 JSON 结果，stderr 输出日志。解析 stdout 获取结果。
2. Windows 路径含 `&` 等特殊字符时用双引号包裹。
3. Cookie 失效时脚本自动刷新，通常无需额外处理。仅自动刷新失败时才读 `cookie.md`。
4. 大数据量导出可能需要 1-2 分钟，脚本自动轮询等待。
