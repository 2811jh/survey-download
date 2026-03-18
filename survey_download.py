#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
网易问卷数据自动下载工具
从 survey-game.163.com 自动下载问卷原始数据（文本数据 + 量化数据）

使用方式:
  # 初始化 Cookie
  python survey_download.py init --survey_token "xxx" --jsessionid "xxx"

  # 检查认证状态
  python survey_download.py check

  # 按名称搜索问卷
  python survey_download.py search --name "山头服调研"

  # 按 ID 下载（文本+量化，全部时间范围）
  python survey_download.py download --id 90394

  # 按名称下载
  python survey_download.py download --name "山头服调研"

  # 指定导出类型和时间范围
  python survey_download.py download --id 90394 --type text --start 2026-01-01 --end 2026-03-17

  # 多个匹配时，指定选择序号
  python survey_download.py download --name "调研" --select 0
"""

import argparse
import json
import os
import re
import sys
import time
import requests
from datetime import datetime, timedelta
from urllib.parse import unquote


# ─── 常量配置 ────────────────────────────────────────────────────────────────

BASE_URL = "https://survey-game.163.com"
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# API 端点
API_SURVEY_LIST = "/view/survey/list"
API_QUESTION_LIST = "/view/survey_stat/get_question_list"
API_CREATE_TIME = "/view/survey_stat/create_time"
API_EXPORT_PAPERS = "/view/survey_stat/export_papers"
API_EXPORT_STATUS = "/view/survey_stat/export_status"
API_DOWNLOAD = "/view/survey_stat/download_papers"
API_QUESTION_DETAIL = "/view/question/list"          # 含选项详情
API_SET_DC_CONDITION = "/view/data_clean/set_dc_condition"
API_GET_DC_CONDITION = "/view/data_clean/get_dc_condition"

# 额外字段：全选
DEFAULT_DIMEN = (
    "country,province,city,url,refer_domain,refer,"
    "isp,browser_name,mobile,mobile_brand,device_name,"
    "full_paper,survey_user_id"
)

DEFAULT_HEADERS = {
    "accept": "application/json, text/javascript, */*; q=0.01",
    "accept-language": "zh-CN,zh;q=0.9,en;q=0.8",
    "content-type": "application/json",
    "origin": BASE_URL,
    "referer": f"{BASE_URL}/index.html",
    "x-requested-with": "XMLHttpRequest",
    "user-agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
    ),
}


# ─── 辅助函数 ────────────────────────────────────────────────────────────────

def _log(msg):
    """输出日志到 stderr（不影响 stdout 的 JSON 输出）"""
    print(f"[survey_download] {msg}", file=sys.stderr, flush=True)


def _json_output(data):
    """统一 JSON 输出到 stdout"""
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _strip_html(text):
    """去除 HTML 标签，返回纯文本"""
    return re.sub(r'<[^>]+>', '', text or '').strip()


def _detect_encoding(filepath, sample_size=8192):
    """检测 CSV 文件编码"""
    with open(filepath, 'rb') as f:
        raw = f.read(sample_size)
    # UTF-8 BOM
    if raw.startswith(b'\xef\xbb\xbf'):
        return 'utf-8-sig'
    # 尝试 UTF-8
    try:
        raw.decode('utf-8')
        return 'utf-8'
    except UnicodeDecodeError:
        return 'gbk'


def _merge_csv_files(file_list, output_path):
    """
    合并多个 CSV 分片文件为一个文件。
    第 2 个及以后的文件跳过表头行。
    """
    file_list.sort()
    encoding = _detect_encoding(file_list[0])
    _log(f"Merging {len(file_list)} CSV files (encoding: {encoding})...")
    total_lines = 0
    with open(output_path, 'w', encoding='utf-8-sig', newline='') as out:
        for i, fpath in enumerate(file_list):
            with open(fpath, 'r', encoding=encoding) as inp:
                for j, line in enumerate(inp):
                    if i > 0 and j == 0:
                        continue  # 后续文件跳过表头
                    out.write(line)
                    total_lines += 1
    # 删除原始分片
    for f in file_list:
        os.remove(f)
    merged_size = os.path.getsize(output_path)
    _log(f"Merged CSV: {output_path} ({total_lines:,} rows, {merged_size:,} bytes)")
    return output_path


def _merge_xlsx_files(file_list, output_path):
    """
    合并多个 XLSX 分片文件为一个 CSV 文件（大文件 XLSX 合并太慢，转为 CSV 更实用）。
    需要 pandas + openpyxl。如果不可用，回退为保留分片。
    output_path: 原始目标路径（.xlsx），会自动改为 .csv
    """
    try:
        import pandas as pd
    except ImportError:
        _log("WARNING: pandas not installed, cannot merge XLSX files. Keeping split files.")
        _log("  Install with: pip install pandas openpyxl")
        return None

    # 将输出路径改为 .csv
    csv_output = os.path.splitext(output_path)[0] + ".csv"
    file_list.sort()
    _log(f"Merging {len(file_list)} XLSX files → CSV...")
    try:
        dfs = []
        for fpath in file_list:
            _log(f"  Reading: {os.path.basename(fpath)}...")
            df = pd.read_excel(fpath, engine='openpyxl')
            dfs.append(df)
        merged = pd.concat(dfs, ignore_index=True)
        _log(f"  Writing merged CSV ({len(merged):,} rows)...")
        merged.to_csv(csv_output, index=False, encoding='utf-8-sig')
        # 删除原始分片
        for f in file_list:
            os.remove(f)
        merged_size = os.path.getsize(csv_output)
        _log(f"Merged: {csv_output} ({len(merged):,} rows, {merged_size:,} bytes)")
        return csv_output
    except Exception as e:
        _log(f"WARNING: XLSX merge failed: {e}. Keeping split files.")
        return None


# ─── 自动清洗规则引擎 ────────────────────────────────────────────────────────

# 年龄选项分类关键词
_AGE_YOUNG_KEYWORDS = ['14岁', '15', '16', '17', '18', '19', '岁以下']  # 20岁以下
_AGE_OLD_KEYWORDS = ['30', '35', '40', '45', '50', '岁以上']            # 30岁以上

# 职业选项分类关键词
_JOB_STUDENT_KEYWORDS = ['小学', '初中', '高中']
_JOB_WORKING_KEYWORDS = [
    '国企', '事业单位', '公务员', '民营', '私企', '外企',
    '专业技术', '医生', '教师', '律师', '商场', '餐饮', '运输', '服务业',
    '车间', '制造业', '生产', '个体户', '私营企业主', '农林牧渔',
    '自由职业', '自雇', '自媒体', '无固定工作', '兼职',
]

# 满意度/NPS 识别关键词
_SATISFACTION_KEYWORDS = ['满意', '满意度']
_NPS_KEYWORDS = ['推荐', 'NPS', 'nps', '净推荐']


def _classify_options(options, keywords):
    """从选项列表中筛选出包含任一关键词的选项 ID"""
    matched = []
    for opt in options:
        text = opt.get('text', '')
        if any(kw in text for kw in keywords):
            matched.append(opt['id'])
    return matched


def _find_question_by_keywords(questions, keywords):
    """从题目列表中找到标题包含任一关键词的题目"""
    for q in questions:
        title = _strip_html(q.get('title', ''))
        if any(kw in title for kw in keywords):
            return q
    return None


def _is_scale_question(question):
    """判断是否为量表题（选项为纯数字或 0-10 / 1-5 类型）"""
    options = question.get('options') or []
    if not options:
        return False
    texts = [o.get('text', '').strip() for o in options]
    try:
        nums = [int(t) for t in texts if t]
        return len(nums) >= 5  # 至少5个数字选项才算量表
    except ValueError:
        return False


def _get_scale_option_ids(options, value_range):
    """获取量表题中指定数值范围的选项 ID"""
    matched = []
    for opt in options:
        text = opt.get('text', '').strip()
        try:
            val = int(text)
            if val in value_range:
                matched.append(opt['id'])
        except ValueError:
            continue
    return matched


def build_clean_conditions(questions):
    """
    根据问卷题目结构，自动构建清洗条件。
    
    返回: {
        "conditions": [...],           # 可直接传给 set_dc_condition 的条件列表
        "rules_applied": [...],        # 已应用的规则描述
        "rules_skipped": [...],        # 跳过的规则及原因
    }
    """
    conditions = []
    rules_applied = []
    rules_skipped = []

    # ── 规则 ①：答题时间 < 30 秒 ─────────────────────────────────────────
    conditions.append({"and": [{"name": "TIME_SPAN", "op": "LT", "values": [30]}]})
    rules_applied.append("① 剔除答题时间 < 30秒")

    # ── 规则 ②：所有选择题选同一选项 ──────────────────────────────────────
    conditions.append({"and": [{"name": "ALL_ANSWER", "op": "EQ", "values": []}]})
    rules_applied.append("② 剔除所有选择题选同一选项")

    # ── 查找人口学题目 ───────────────────────────────────────────────────
    age_q = _find_question_by_keywords(questions, ['年龄'])
    job_q = _find_question_by_keywords(questions, ['职业'])
    sat_q = _find_question_by_keywords(questions, _SATISFACTION_KEYWORDS)
    nps_q = _find_question_by_keywords(questions, _NPS_KEYWORDS)

    # ── 规则 ③：年龄 < 20 且 职业为工作人群 ──────────────────────────────
    if age_q and job_q:
        age_opts = age_q.get('options') or []
        job_opts = job_q.get('options') or []
        young_ids = _classify_options(age_opts, _AGE_YOUNG_KEYWORDS)
        working_ids = _classify_options(job_opts, _JOB_WORKING_KEYWORDS)
        if young_ids and working_ids:
            conditions.append({
                "and": [
                    {"name": age_q['id'], "op": "EQ", "values": young_ids},
                    {"name": job_q['id'], "op": "EQ", "values": working_ids},
                ]
            })
            rules_applied.append(f"③ 剔除年龄<20岁 且 职业为工作人群（年龄题: {_strip_html(age_q['title'])[:20]}，职业题: {_strip_html(job_q['title'])[:20]}）")
        else:
            rules_skipped.append("③ 年龄-职业冲突：未能匹配到足够的年轻选项或工作选项")
    else:
        missing = []
        if not age_q:
            missing.append("年龄题")
        if not job_q:
            missing.append("职业题")
        rules_skipped.append(f"③ 年龄-职业冲突：未找到{'/'.join(missing)}（跳过）")

    # ── 规则 ④：职业为学生（小/初/高）且 年龄 ≥ 30 ─────────────────────
    if age_q and job_q:
        age_opts = age_q.get('options') or []
        job_opts = job_q.get('options') or []
        student_ids = _classify_options(job_opts, _JOB_STUDENT_KEYWORDS)
        old_ids = _classify_options(age_opts, _AGE_OLD_KEYWORDS)
        if student_ids and old_ids:
            conditions.append({
                "and": [
                    {"name": job_q['id'], "op": "EQ", "values": student_ids},
                    {"name": age_q['id'], "op": "EQ", "values": old_ids},
                ]
            })
            rules_applied.append(f"④ 剔除职业为学生(小/初/高) 且 年龄≥30岁")
        else:
            rules_skipped.append("④ 职业-年龄冲突：未能匹配到足够的学生选项或30+选项")
    else:
        missing = []
        if not age_q:
            missing.append("年龄题")
        if not job_q:
            missing.append("职业题")
        rules_skipped.append(f"④ 职业-年龄冲突：未找到{'/'.join(missing)}（跳过）")

    # ── 规则 ⑤：满意度与NPS冲突（仅当两题都存在时）────────────────────
    if sat_q and nps_q and _is_scale_question(sat_q) and _is_scale_question(nps_q):
        sat_opts = sat_q.get('options') or []
        nps_opts = nps_q.get('options') or []
        # 低满意(1分) + 高推荐(9-10分)
        low_sat_ids = _get_scale_option_ids(sat_opts, {1})
        high_nps_ids = _get_scale_option_ids(nps_opts, {9, 10})
        if low_sat_ids and high_nps_ids:
            conditions.append({
                "and": [
                    {"name": sat_q['id'], "op": "EQ", "values": low_sat_ids},
                    {"name": nps_q['id'], "op": "EQ", "values": high_nps_ids},
                ]
            })
        # 高满意(5分) + 低推荐(0-1分)
        high_sat_ids = _get_scale_option_ids(sat_opts, {5})
        low_nps_ids = _get_scale_option_ids(nps_opts, {0, 1})
        if high_sat_ids and low_nps_ids:
            conditions.append({
                "and": [
                    {"name": sat_q['id'], "op": "EQ", "values": high_sat_ids},
                    {"name": nps_q['id'], "op": "EQ", "values": low_nps_ids},
                ]
            })
        if (low_sat_ids and high_nps_ids) or (high_sat_ids and low_nps_ids):
            rules_applied.append(f"⑤ 剔除满意度-NPS冲突（低满意+高推荐 / 高满意+低推荐）")
        else:
            rules_skipped.append("⑤ 满意度-NPS冲突：未能匹配到量表选项值")
    else:
        if not sat_q and not nps_q:
            rules_skipped.append("⑤ 满意度-NPS冲突：未找到满意度题和NPS题（跳过）")
        elif not sat_q:
            rules_skipped.append("⑤ 满意度-NPS冲突：未找到满意度题（跳过）")
        elif not nps_q:
            rules_skipped.append("⑤ 满意度-NPS冲突：未找到NPS题（跳过）")
        else:
            rules_skipped.append("⑤ 满意度-NPS冲突：题目不是量表类型（跳过）")

    return {
        "conditions": conditions,
        "rules_applied": rules_applied,
        "rules_skipped": rules_skipped,
    }


# ─── 核心类 ──────────────────────────────────────────────────────────────────

class SurveyDownloader:
    """网易问卷下载器"""

    def __init__(self, config_path=None):
        self.config_path = config_path or CONFIG_FILE
        self.session = requests.Session()
        self.session.headers.update(DEFAULT_HEADERS)
        self._load_config()

    # ── Cookie 管理 ──────────────────────────────────────────────────────

    def _load_config(self):
        """从 config.json 加载 Cookie"""
        if not os.path.exists(self.config_path):
            return False
        with open(self.config_path, "r", encoding="utf-8") as f:
            config = json.load(f)
        for name, value in config.get("cookies", {}).items():
            self.session.cookies.set(name, value, domain="survey-game.163.com")
        return True

    def save_config(self, cookies_dict):
        """保存 Cookie 到 config.json"""
        config = {
            "cookies": cookies_dict,
            "updated_at": datetime.now().isoformat(),
        }
        with open(self.config_path, "w", encoding="utf-8") as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        _log(f"Config saved to {self.config_path}")

    # ── 自动刷新 Cookie ──────────────────────────────────────────────────

    def _auto_refresh_cookie(self):
        """调用 refresh_cookie.py 自动刷新 Cookie，刷新后重新加载"""
        refresh_script = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "refresh_cookie.py"
        )
        if not os.path.exists(refresh_script):
            _log("refresh_cookie.py not found, cannot auto-refresh.")
            return False

        import subprocess
        python_exe = sys.executable
        _log(f"Running refresh_cookie.py...")
        try:
            result = subprocess.run(
                [python_exe, refresh_script, "--timeout", "300"],
                capture_output=False,
                timeout=310,
            )
            if result.returncode == 0:
                # 重新加载 config
                self._load_config()
                return True
            else:
                _log("refresh_cookie.py exited with error.")
                return False
        except subprocess.TimeoutExpired:
            _log("refresh_cookie.py timed out.")
            return False
        except Exception as e:
            _log(f"Failed to run refresh_cookie.py: {e}")
            return False

    # ── 认证检查 ─────────────────────────────────────────────────────────

    def check_auth(self):
        """检查 Cookie 是否有效（尝试拉取问卷列表）"""
        try:
            resp = self.session.post(
                f"{BASE_URL}{API_SURVEY_LIST}",
                json={
                    "pageNo": 1, "surveyName": "", "status": "-1",
                    "deliveryRange": -1, "type": -1, "groupId": -1,
                    "groupUser": -1, "gameName": "",
                },
            )
            data = resp.json()
            return data.get("resultCode") == 100
        except Exception as e:
            _log(f"Auth check failed: {e}")
            return False

    # ── 问卷搜索 ─────────────────────────────────────────────────────────

    def search_surveys(self, name="", page=1):
        """按名称搜索问卷列表"""
        resp = self.session.post(
            f"{BASE_URL}{API_SURVEY_LIST}",
            json={
                "pageNo": page,
                "surveyName": name,
                "status": "-1",
                "deliveryRange": -1,
                "type": -1,
                "groupId": -1,
                "groupUser": -1,
                "gameName": "",
            },
        )
        data = resp.json()
        if data.get("resultCode") != 100:
            return {"status": "error", "message": data.get("resultDesc", "Unknown error")}

        surveys = data.get("dataList", [])
        results = []
        for s in surveys:
            results.append({
                "id": s.get("id"),
                "name": s.get("surveyName", ""),
                "status": s.get("statusName", ""),
                "responses": s.get("paperCount", 0),
                "createTime": s.get("createTime", ""),
            })

        page_info = data.get("page") or {}
        total = page_info.get("totalCount", len(results))

        return {"status": "success", "surveys": results, "total": total}

    # ── 获取题目列表 ─────────────────────────────────────────────────────

    def get_question_list(self, survey_id):
        """获取问卷的题目列表（用于构建导出请求）"""
        resp = self.session.post(
            f"{BASE_URL}{API_QUESTION_LIST}",
            json={"surveyId": survey_id, "type": "", "keyWord": "", "questionExportList": []},
        )
        data = resp.json()
        if data.get("resultCode") != 100:
            _log(f"get_question_list failed: {data.get('resultDesc')}")
            return None
        # 题目列表在 data.questionExportList 中
        inner = data.get("data")
        if isinstance(inner, dict):
            return inner.get("questionExportList") or []
        return inner or data.get("dataList") or []

    # ── 获取问卷详情（含选项）────────────────────────────────────────────

    def get_question_detail(self, survey_id):
        """获取问卷完整题目结构（含选项 ID 和文本），用于清洗条件构建"""
        resp = self.session.get(
            f"{BASE_URL}{API_QUESTION_DETAIL}",
            params={"surveyId": survey_id, "from": "dataclean"},
        )
        data = resp.json()
        if data.get("resultCode") != 100:
            _log(f"get_question_detail failed: {data.get('resultDesc')}")
            return None
        return data.get("dataList") or data.get("data") or []

    # ── 数据清洗 ─────────────────────────────────────────────────────────

    def set_clean_conditions(self, survey_id, conditions, enabled=1):
        """设置问卷的数据清洗条件"""
        body = {
            "surveyId": survey_id,
            "enabled": enabled,
            "conditions": conditions,
        }
        resp = self.session.post(f"{BASE_URL}{API_SET_DC_CONDITION}", json=body)
        return resp.json()

    def get_clean_conditions(self, survey_id):
        """获取问卷当前的数据清洗条件"""
        resp = self.session.get(
            f"{BASE_URL}{API_GET_DC_CONDITION}",
            params={"surveyId": survey_id},
        )
        return resp.json()

    def auto_clean(self, survey_id, dry_run=False):
        """
        自动清洗：识别问卷结构 → 构建清洗规则 → (可选)提交到服务端
        dry_run: 仅预览规则，不实际提交
        返回: {"status": "success/preview/error", "rules_applied": [...], ...}
        """
        # 1. 获取问卷完整题目结构
        questions = self.get_question_detail(survey_id)
        if questions is None:
            return {"status": "error", "message": "Failed to get question detail"}

        _log(f"Loaded {len(questions)} questions for cleaning analysis")

        # 2. 自动构建清洗条件
        result = build_clean_conditions(questions)
        conditions = result["conditions"]

        _log(f"Built {len(conditions)} cleaning conditions")
        for r in result["rules_applied"]:
            _log(f"  ✓ {r}")
        for r in result["rules_skipped"]:
            _log(f"  ⊘ {r}")

        # 3. 如果是预览模式，返回规则不提交
        if dry_run:
            return {
                "status": "preview",
                "message": f"预览：将应用 {len(conditions)} 条清洗规则（未提交）",
                "survey_id": survey_id,
                "total_conditions": len(conditions),
                "rules_applied": result["rules_applied"],
                "rules_skipped": result["rules_skipped"],
            }

        # 4. 提交清洗条件
        resp = self.set_clean_conditions(survey_id, conditions, enabled=1)
        if resp.get("resultCode") != 100:
            return {
                "status": "error",
                "message": f"Failed to set conditions: {resp.get('resultDesc', 'Unknown')}",
                "rules_applied": result["rules_applied"],
                "rules_skipped": result["rules_skipped"],
            }

        return {
            "status": "success",
            "message": f"已成功配置 {len(conditions)} 条清洗规则",
            "survey_id": survey_id,
            "total_conditions": len(conditions),
            "rules_applied": result["rules_applied"],
            "rules_skipped": result["rules_skipped"],
        }

    # ── 获取问卷创建时间 ─────────────────────────────────────────────────

    def get_create_time(self, survey_id):
        """获取问卷创建时间（用于默认时间范围的起点）"""
        try:
            resp = self.session.get(
                f"{BASE_URL}{API_CREATE_TIME}",
                params={"surveyId": survey_id},
            )
            data = resp.json()
            if data.get("resultCode") == 100 and data.get("data"):
                return data["data"]  # 可能是时间戳或字符串
        except Exception as e:
            _log(f"get_create_time failed: {e}")
        return None

    # ── 触发导出 ─────────────────────────────────────────────────────────

    def trigger_export(self, survey_id, data_type, begin, end, questions):
        """
        触发数据导出
        data_type: 1=文本数据, 0=量化数据
        begin/end: 毫秒时间戳
        questions: 题目列表（带 selected=1）
        """
        body = {
            "surveyId": survey_id,
            "begin": begin,
            "end": end,
            "dimen": DEFAULT_DIMEN,
            "dataType": data_type,
            "questionExportList": questions,
        }
        resp = self.session.post(f"{BASE_URL}{API_EXPORT_PAPERS}", json=body)
        data = resp.json()
        if data.get("resultCode") != 100:
            _log(f"trigger_export(type={data_type}) failed: {data.get('resultDesc')}")
        return data

    # ── 查询导出状态 ─────────────────────────────────────────────────────

    def check_export_status(self, survey_id):
        """查询导出进度"""
        resp = self.session.get(
            f"{BASE_URL}{API_EXPORT_STATUS}",
            params={"surveyId": survey_id},
        )
        return resp.json()

    def wait_for_export(self, survey_id, target_types, timeout=300, poll_interval=3):
        """
        轮询等待导出完成
        target_types: 需要等待的 type 集合，如 {0, 1}
        返回: {"status": "success", "exports": [...]} 或 {"status": "error", ...}
        """
        start_time = time.time()
        while time.time() - start_time < timeout:
            status_data = self.check_export_status(survey_id)
            if status_data.get("resultCode") != 100:
                return {"status": "error", "message": "Failed to check export status"}

            data_list = status_data.get("dataList", [])
            if data_list:
                # 只检查我们需要的 type
                relevant = [item for item in data_list if item.get("type") in target_types]
                if len(relevant) >= len(target_types):
                    all_done = all(item.get("status") == 1 for item in relevant)
                    if all_done:
                        return {"status": "success", "exports": relevant}

            elapsed = int(time.time() - start_time)
            _log(f"Waiting for export... ({elapsed}s)")
            time.sleep(poll_interval)

        return {"status": "error", "message": f"Export timeout after {timeout}s"}

    # ── 下载文件 ─────────────────────────────────────────────────────────

    def download_file(self, survey_id, data_type, output_dir, begin_ts=None, end_ts=None):
        """
        下载已导出的文件
        data_type: 1=文本数据, 0=量化数据
        begin_ts/end_ts: 毫秒时间戳，用于文件名中的数据周期
        返回: 下载后的文件绝对路径，失败返回 None
        """
        url = f"{BASE_URL}{API_DOWNLOAD}"
        resp = self.session.get(
            url,
            params={"surveyId": survey_id, "type": data_type},
            stream=True,
        )

        if resp.status_code != 200:
            _log(f"Download failed: HTTP {resp.status_code}")
            return None

        # 从 Content-Disposition 解析原始文件扩展名
        content_disp = resp.headers.get("content-disposition", "")
        orig_filename = None
        if "filename" in content_disp:
            match = re.search(r"filename\*=UTF-8''(.+?)(?:;|$)", content_disp)
            if match:
                orig_filename = unquote(match.group(1).strip('"'))
            else:
                match = re.search(r'filename="?([^";]+)"?', content_disp)
                if match:
                    orig_filename = unquote(match.group(1).strip())

        # 确定扩展名
        if orig_filename:
            ext = os.path.splitext(orig_filename)[1]  # 如 .csv 或 .xlsx
        else:
            ext = ".csv" if data_type == 1 else ".xlsx"

        # 构建文件名：survey_{id}【文本数据/量化数据】{起始日期}-{结束日期}_{时间戳}{ext}
        type_label = "文本数据" if data_type == 1 else "量化数据"
        now_ts = int(time.time())
        if begin_ts and end_ts:
            begin_str = datetime.fromtimestamp(begin_ts / 1000).strftime("%Y%m%d")
            end_str = datetime.fromtimestamp(end_ts / 1000).strftime("%Y%m%d")
            date_range = f"{begin_str}-{end_str}"
        else:
            date_range = "all"
        filename = f"survey_{survey_id}【{type_label}】{date_range}_{now_ts}{ext}"

        filepath = os.path.join(output_dir, filename)
        with open(filepath, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)

        file_size = os.path.getsize(filepath)
        _log(f"Downloaded: {filepath} ({file_size:,} bytes)")

        # 检测是否为 ZIP 压缩包（服务端大文件会自动压缩）
        import zipfile
        if zipfile.is_zipfile(filepath):
            _log(f"Detected ZIP archive, extracting...")
            extract_dir = output_dir
            extracted_files = []
            with zipfile.ZipFile(filepath, 'r') as zf:
                for member in zf.namelist():
                    # 解压并重命名为统一格式
                    zf.extract(member, extract_dir)
                    src = os.path.join(extract_dir, member)
                    member_ext = os.path.splitext(member)[1]
                    dest_name = f"survey_{survey_id}【{type_label}】{date_range}_{now_ts}{member_ext}"
                    dest = os.path.join(extract_dir, dest_name)
                    # 如果目标文件已存在，添加序号避免冲突
                    counter = 1
                    while os.path.exists(dest) and dest != src:
                        dest_name = f"survey_{survey_id}【{type_label}】{date_range}_{now_ts}_{counter}{member_ext}"
                        dest = os.path.join(extract_dir, dest_name)
                        counter += 1
                    if src != dest:
                        os.rename(src, dest)
                    extracted_files.append(dest)
                    _log(f"Extracted: {dest} ({os.path.getsize(dest):,} bytes)")
            # 删除原始 ZIP 文件
            os.remove(filepath)
            # 清理 ZIP 解压可能产生的空目录
            for member in zf.namelist():
                member_dir = os.path.join(output_dir, os.path.dirname(member))
                if os.path.isdir(member_dir) and not os.listdir(member_dir):
                    try:
                        os.rmdir(member_dir)
                    except OSError:
                        pass

            # 如果有多个分片文件，自动合并
            if len(extracted_files) > 1:
                merged_name = f"survey_{survey_id}【{type_label}】{date_range}_{now_ts}"
                # 按扩展名分组
                csv_files = sorted([f for f in extracted_files if f.endswith('.csv')])
                xlsx_files = sorted([f for f in extracted_files if f.endswith('.xlsx')])

                if csv_files:
                    merged_path = os.path.join(output_dir, merged_name + ".csv")
                    result = _merge_csv_files(csv_files, merged_path)
                    if result:
                        filepath = result
                    else:
                        filepath = csv_files[0]
                elif xlsx_files:
                    merged_path = os.path.join(output_dir, merged_name + ".xlsx")
                    result = _merge_xlsx_files(xlsx_files, merged_path)
                    if result:
                        filepath = result
                    else:
                        filepath = xlsx_files[0]
                else:
                    filepath = extracted_files[0]
            else:
                filepath = extracted_files[0] if extracted_files else filepath

        return filepath

    # ── 主流程 ───────────────────────────────────────────────────────────

    def run(self, survey_id=None, survey_name=None, export_type="both",
            start_date=None, end_date=None, output_dir=None, select_index=None,
            clean=False):
        """
        主入口：搜索问卷 → (可选)自动清洗 → 触发导出 → 等待完成 → 下载文件
        export_type: "both" | "text" | "quantified"
        clean: 是否在导出前自动配置清洗条件
        """
        # 1. 检查认证（失败时自动刷新 Cookie）
        if not self.check_auth():
            _log("Auth failed, attempting auto-refresh...")
            if self._auto_refresh_cookie():
                _log("Cookie refreshed, retrying auth...")
                if not self.check_auth():
                    return {
                        "status": "error",
                        "message": "Authentication failed even after cookie refresh.",
                    }
            else:
                return {
                    "status": "error",
                    "message": "Authentication failed and auto-refresh unavailable.",
                }

        # 2. 定位问卷
        target_id = None
        target_name = None

        if survey_id:
            target_id = int(survey_id)
            # 尝试获取问卷名称（非必须）
            search_result = self.search_surveys()
            for s in search_result.get("surveys", []):
                if s["id"] == target_id:
                    target_name = s["name"]
                    break
            if not target_name:
                target_name = f"Survey {target_id}"

        elif survey_name:
            search_result = self.search_surveys(survey_name)
            if search_result["status"] != "success":
                return search_result

            surveys = search_result["surveys"]
            if len(surveys) == 0:
                return {
                    "status": "no_match",
                    "message": f"No survey found matching '{survey_name}'",
                }
            elif len(surveys) == 1:
                target_id = surveys[0]["id"]
                target_name = surveys[0]["name"]
            else:
                # 多个匹配
                if select_index is not None:
                    idx = int(select_index)
                    if 0 <= idx < len(surveys):
                        target_id = surveys[idx]["id"]
                        target_name = surveys[idx]["name"]
                    else:
                        return {
                            "status": "error",
                            "message": f"Invalid selection: {idx}. Valid range: 0-{len(surveys)-1}",
                        }
                else:
                    return {
                        "status": "multiple_matches",
                        "message": f"Found {len(surveys)} surveys matching '{survey_name}':",
                        "matches": surveys,
                    }
        else:
            return {"status": "error", "message": "Please provide --id or --name"}

        _log(f"Target survey: [{target_id}] {target_name}")

        # 2.5 自动清洗（如果启用）
        clean_result = None
        if clean:
            _log("Running auto-clean...")
            clean_result = self.auto_clean(target_id)
            if clean_result["status"] == "success":
                _log(f"Auto-clean done: {clean_result['message']}")
            else:
                _log(f"Auto-clean failed: {clean_result.get('message')}")

        # 3. 获取题目列表
        questions = self.get_question_list(target_id)
        if questions is None:
            return {"status": "error", "message": "Failed to get question list"}

        # 标记全部题目为选中
        for q in questions:
            q["selected"] = 1
            if q.get("subQuestions"):
                for sq in q["subQuestions"]:
                    sq["selected"] = 1

        _log(f"Question count: {len(questions)}")

        # 4. 确定时间范围
        if start_date:
            begin_ts = int(datetime.strptime(start_date, "%Y-%m-%d").timestamp() * 1000)
        else:
            # 默认：获取问卷创建时间，或回退到 2 年前
            create_time = self.get_create_time(target_id)
            if create_time and isinstance(create_time, (int, float)):
                begin_ts = int(create_time)
            elif create_time and isinstance(create_time, str):
                try:
                    begin_ts = int(datetime.strptime(create_time, "%Y-%m-%d %H:%M:%S").timestamp() * 1000)
                except ValueError:
                    begin_ts = int((datetime.now() - timedelta(days=730)).timestamp() * 1000)
            else:
                begin_ts = int((datetime.now() - timedelta(days=730)).timestamp() * 1000)

        if end_date:
            end_ts = int(datetime.strptime(end_date, "%Y-%m-%d").timestamp() * 1000)
        else:
            end_ts = int(datetime.now().timestamp() * 1000)

        _log(f"Time range: {datetime.fromtimestamp(begin_ts/1000)} ~ {datetime.fromtimestamp(end_ts/1000)}")

        # 5. 确定导出类型
        types_to_export = []
        if export_type in ("both", "text"):
            types_to_export.append((1, "text"))
        if export_type in ("both", "quantified"):
            types_to_export.append((0, "quantified"))

        # 6. 触发导出
        for dt, dt_name in types_to_export:
            _log(f"Triggering {dt_name} data export (dataType={dt})...")
            result = self.trigger_export(target_id, dt, begin_ts, end_ts, questions)
            if result.get("resultCode") != 100:
                return {
                    "status": "error",
                    "message": f"Failed to trigger {dt_name} export: {result.get('resultDesc', 'Unknown')}",
                }
            _log(f"{dt_name} export triggered successfully")

        # 7. 等待导出完成
        target_type_set = {dt for dt, _ in types_to_export}
        _log("Waiting for export to complete...")
        wait_result = self.wait_for_export(target_id, target_type_set)
        if wait_result["status"] != "success":
            return wait_result

        _log("Export completed!")

        # 8. 下载文件
        if not output_dir:
            output_dir = os.getcwd()
        os.makedirs(output_dir, exist_ok=True)

        files = {}
        for dt, dt_name in types_to_export:
            _log(f"Downloading {dt_name} data...")
            filepath = self.download_file(target_id, dt, output_dir, begin_ts, end_ts)
            if filepath:
                files[f"{dt_name}_data"] = os.path.abspath(filepath)
            else:
                files[f"{dt_name}_data"] = None
                _log(f"WARNING: Failed to download {dt_name} data")

        result = {
            "status": "success",
            "survey_name": target_name,
            "survey_id": target_id,
            "files": files,
        }
        if clean_result:
            result["clean"] = clean_result
        return result


# ─── CLI ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="网易问卷数据自动下载工具",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", help="可用命令")

    # ── init: 初始化 Cookie ──────────────────────────────────────────────
    init_p = subparsers.add_parser("init", help="初始化 Cookie 配置")
    init_p.add_argument("--survey_token", required=True, help="SURVEY_TOKEN cookie")
    init_p.add_argument("--jsessionid", required=True, help="JSESSIONID cookie")
    init_p.add_argument("--p_info", default="", help="P_INFO cookie (optional)")

    # ── check: 检查认证 ─────────────────────────────────────────────────
    subparsers.add_parser("check", help="检查认证是否有效")

    # ── search: 搜索问卷 ────────────────────────────────────────────────
    search_p = subparsers.add_parser("search", help="按名称搜索问卷")
    search_p.add_argument("--name", required=True, help="问卷名称（支持模糊搜索）")
    search_p.add_argument("--page", type=int, default=1, help="页码（默认 1）")

    # ── clean: 自动清洗 ─────────────────────────────────────────────────
    clean_p = subparsers.add_parser("clean", help="自动配置问卷数据清洗条件")
    clean_p.add_argument("--id", type=int, required=True, help="问卷 ID")
    clean_p.add_argument("--dry-run", action="store_true", help="仅预览规则，不实际提交")

    # ── download: 下载数据 ──────────────────────────────────────────────
    dl_p = subparsers.add_parser("download", help="下载问卷数据")
    dl_p.add_argument("--id", type=int, help="问卷 ID")
    dl_p.add_argument("--name", help="问卷名称（模糊匹配）")
    dl_p.add_argument(
        "--type", choices=["both", "text", "quantified"], default="both",
        help="导出类型: both=两者, text=文本数据, quantified=量化数据（默认 both）",
    )
    dl_p.add_argument("--start", help="起始日期 (YYYY-MM-DD)")
    dl_p.add_argument("--end", help="结束日期 (YYYY-MM-DD)")
    dl_p.add_argument("--output_dir", help="输出目录（默认当前目录）")
    dl_p.add_argument("--select", type=int, help="多个匹配时的选择序号（从 0 开始）")
    dl_p.add_argument("--clean", action="store_true", help="下载前自动配置清洗条件")

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    downloader = SurveyDownloader()

    # ── 执行命令 ─────────────────────────────────────────────────────────
    if args.command == "init":
        cookies = {"SURVEY_TOKEN": args.survey_token, "JSESSIONID": args.jsessionid}
        if args.p_info:
            cookies["P_INFO"] = args.p_info
        downloader.save_config(cookies)

        # 重新加载并验证
        downloader = SurveyDownloader()
        if downloader.check_auth():
            _json_output({"status": "success", "message": "Cookie 配置成功，认证验证通过 ✓"})
        else:
            _json_output({"status": "warning", "message": "Cookie 已保存，但认证验证失败。请检查 Cookie 是否正确。"})

    elif args.command == "check":
        if downloader.check_auth():
            _json_output({"status": "success", "message": "认证有效 ✓"})
        else:
            _log("Auth invalid, attempting auto-refresh...")
            if downloader._auto_refresh_cookie() and downloader.check_auth():
                _json_output({"status": "success", "message": "认证已自动刷新 ✓"})
            else:
                _json_output({"status": "error", "message": "认证无效，自动刷新失败。请手动运行 init 命令。"})

    elif args.command == "search":
        # 搜索前先检查认证，失败时自动刷新
        if not downloader.check_auth():
            _log("Auth invalid for search, attempting auto-refresh...")
            if downloader._auto_refresh_cookie():
                downloader = SurveyDownloader()  # 重新加载
            else:
                _json_output({"status": "error", "message": "认证无效，自动刷新失败。请手动运行 init 命令。"})
                return
        result = downloader.search_surveys(args.name, args.page)
        _json_output(result)

    elif args.command == "clean":
        dry_run = getattr(args, 'dry_run', False)
        result = downloader.auto_clean(args.id, dry_run=dry_run)
        _json_output(result)

    elif args.command == "download":
        result = downloader.run(
            survey_id=args.id,
            survey_name=args.name,
            export_type=args.type,
            start_date=args.start,
            end_date=args.end,
            output_dir=args.output_dir,
            select_index=args.select,
            clean=args.clean,
        )
        _json_output(result)


if __name__ == "__main__":
    main()
