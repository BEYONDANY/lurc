# -*- coding: utf-8 -*-
# AI-GEN-BEGIN
"""姓名拼音：按通讯录常见姓氏读音（含多音姓/复姓）转全拼。"""
from __future__ import annotations

import re

# 复姓优先（长匹配）
COMPOUND_SURNAMES = {
    "万俟": "moqi",
    "尉迟": "yuchi",
    "长孙": "zhangsun",
    "欧阳": "ouyang",
    "司马": "sima",
    "上官": "shangguan",
    "诸葛": "zhuge",
    "司徒": "situ",
    "司空": "sikong",
    "端木": "duanmu",
    "东方": "dongfang",
    "独孤": "dugu",
    "南宫": "nangong",
    "夏侯": "xiahou",
    "呼延": "huyan",
    "慕容": "murong",
    "皇甫": "huangfu",
    "宇文": "yuwen",
    "司寇": "sikou",
}

# 单字多音姓：按姓氏常用读音（对照通讯录姓）
SURNAME_PINYIN = {
    "曾": "zeng",
    "单": "shan",
    "解": "xie",
    "区": "ou",
    "仇": "qiu",
    "朴": "piao",
    "查": "zha",
    "乐": "yue",
    "翟": "zhai",
    "盖": "ge",
    "缪": "miao",
    "燕": "yan",
    "覃": "qin",
    "隗": "kui",
    "郗": "xi",
    "宓": "fu",
    "能": "nai",
    "阚": "kan",
    "乜": "nie",
    "冼": "xian",
    "折": "she",
    "繁": "po",
    "员": "yun",
    "谌": "chen",
    "召": "shao",
    "种": "chong",
    "过": "guo",
    "柏": "bai",
    "瞿": "qu",
    "纪": "ji",
    "华": "hua",
    "任": "ren",
    "沈": "shen",
    "冯": "feng",
    "汤": "tang",
    "蓝": "lan",
    "於": "yu",
}


def split_surname(name: str) -> tuple[str, str]:
    """返回 (姓拼音, 名剩余汉字)。无特殊姓则姓拼音为空。"""
    name = (name or "").strip()
    if not name:
        return "", ""
    for n in sorted((2, 1), reverse=True):
        if len(name) >= n:
            head = name[:n]
            if n == 2 and head in COMPOUND_SURNAMES:
                return COMPOUND_SURNAMES[head], name[n:]
            if n == 1 and head in SURNAME_PINYIN:
                return SURNAME_PINYIN[head], name[n:]
    return "", name


def name_to_pinyin(display_name: str, lazy_pinyin=None, fallback: dict | None = None) -> str:
    """姓名 → 登录用户名全拼（小写字母数字）。"""
    name = (display_name or "").strip()
    if not name:
        return "user"
    sur_py, given = split_surname(name)
    if sur_py:
        rest_src = given
        prefix = sur_py
    else:
        rest_src = name
        prefix = ""

    if rest_src:
        if lazy_pinyin:
            rest = "".join(lazy_pinyin(rest_src))
        else:
            fb = fallback or {}
            rest = "".join(
                fb.get(ch, ch if re.match(r"[A-Za-z0-9]", ch) else "") for ch in rest_src
            )
    else:
        rest = ""

    raw = re.sub(r"[^a-zA-Z0-9]", "", (prefix + rest)).lower()
    return raw or "user"


def expected_username_base(display_name: str, lazy_pinyin=None, fallback: dict | None = None) -> str:
    return name_to_pinyin(display_name, lazy_pinyin=lazy_pinyin, fallback=fallback)
# AI-GEN-END
