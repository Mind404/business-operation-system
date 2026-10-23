# -*- coding: utf-8 -*-
"""
插件加载器：自动扫描 plugins/ 下的所有插件包，注册 blueprint + manifest。

使用方式（在 app.py 启动时）：
    from plugins import load_plugins
    load_plugins(app)

每个插件包需暴露：
  - manifest: dict（id/name/icon/group/core/description/endpoint/tables/menu）
  - blueprint: Flask Blueprint
"""
import importlib
import pkgutil


def discover_plugins():
    """扫描 plugins/ 目录，返回 [(plugin_id, module), ...]"""
    results = []
    for _, name, is_pkg in pkgutil.iter_modules(__path__):
        if not is_pkg:
            continue
        if name.startswith('_'):
            continue
        try:
            mod = importlib.import_module(f'plugins.{name}')
            if hasattr(mod, 'blueprint') and hasattr(mod, 'manifest'):
                results.append((name, mod))
        except Exception as e:
            print(f'[插件加载] 跳过 {name}: {e}')
    return results


def load_plugins(app):
    """加载所有插件：注册 blueprint + 返回 manifest 列表供 plugin_system 登记。"""
    loaded = []
    for name, mod in discover_plugins():
        app.register_blueprint(mod.blueprint)
        loaded.append(mod.manifest)
        print(f'[插件加载] 已注册: {mod.manifest.get("name", name)} ({name})')
    return loaded
