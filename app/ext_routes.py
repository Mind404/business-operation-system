# -*- coding: utf-8 -*-
"""扩展模块路由（v10.4.0 已全部迁移到 plugins/ 独立插件包，此文件保留空 blueprint 兼容）"""
from flask import Blueprint
ext_bp = Blueprint('ext', __name__)
