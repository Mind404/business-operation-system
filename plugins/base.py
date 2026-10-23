# -*- coding: utf-8 -*-
"""
插件基类与 manifest 规范。

每个业务插件是一个独立 Python 包，包内必须包含：
  - manifest: dict，声明插件元数据（id/name/icon/description/tables/menu 等）
  - blueprint: Flask Blueprint，承载该插件的全部路由

插件包通过 plugins/__init__.py 的 load_plugins() 自动扫描注册，
无需在 app.py 或 ext_routes.py 里硬编码。
"""
from flask import Blueprint


def make_blueprint(plugin_id, import_name, template_folder='templates',
                   static_folder='static', url_prefix=''):
    """创建插件 blueprint（统一工厂，保证命名规范一致）。

    Args:
        plugin_id: 插件唯一标识，同时作为 blueprint name（endpoint 前缀）
        import_name: 通常传 __name__
        template_folder: 插件自带模板目录（相对插件包）
        static_folder: 插件自带静态资源目录
        url_prefix: 路由前缀，默认空（保持现有 URL 不变）
    """
    return Blueprint(
        plugin_id,
        import_name,
        template_folder=template_folder,
        static_folder=static_folder,
        url_prefix=url_prefix,
    )
