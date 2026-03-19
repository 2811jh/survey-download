---
name: survey_download
description: |
  从网易问卷系统下载问卷原始数据并自动数据清洗。
  支持国内平台（survey-game.163.com）和国外平台（survey-game.easebar.com）。
  通过问卷 ID 或名称定位问卷，导出文本数据和量化数据。
  支持自动配置服务端清洗条件（剔除无效问卷：答题过快、选项雷同、人口学冲突、满意度-NPS矛盾）。
  当用户提到"下载问卷"、"导出问卷数据"、"帮我下数据"、"download survey"、
  "export 问卷"、"清洗问卷"、"筛选无效数据"、"帮我筛一下再导出"、
  "下载国外问卷"、"从 easebar 导出"等场景时使用。
  关键区分：本 skill 负责从问卷平台"下载/导出/清洗原始数据"；
  如果用户已有数据文件、需要"分析/统计/出报告"，那属于 survey-research 的职责。
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

## 平台切换

支持双平台，通过 `--platform` 参数切换（首次指定后会记住，后续无需重复指定）：

| 参数值 | 平台 | 域名 |
|--------|------|------|
| `cn`（默认） | 国内 | survey-game.163.com |
| `intl` | 国外 | survey-game.easebar.com |

```bash
# 国内平台（默认，可省略）
python {SKILL_DIR}/survey_download.py search --name "关键词"

# 国外平台（首次需指定，之后自动记住）
python {SKILL_DIR}/survey_download.py --platform intl search --name "关键词"
```

当用户提到"国外问卷"、"海外问卷"、"easebar"时，使用 `--platform intl`。

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
5. 错误处理——根据 JSON 中的 `status` 字段决定下一步：
   - `"error"` → 将 `message` 翻译为用户友好语言告知原因，常见：认证失败、网络超时
   - `"no_match"` → 告知用户未找到匹配问卷，建议换关键词或直接提供问卷 ID
   - `"multiple_matches"` → 用 `ask_user_question` 展示匹配列表让用户选择
   - `"not_collecting"` → 问卷尚未发布/未回收，没有统计分析功能，告知用户需先发布回收
   - `"warning"` → 操作部分成功，将警告内容告知用户
