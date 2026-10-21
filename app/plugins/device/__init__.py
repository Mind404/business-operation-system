# -*- coding: utf-8 -*-
"""版本管理插件：设备运维、OTA版本发布、软硬件版本管理"""
from .routes import blueprint

manifest = {
    'id': 'device',
    'name': '版本管理',
    'icon': '📡',
    'group': '业务插件',
    'core': False,
    'description': '设备状态监控、版本变更记录、OTA版本发布、软硬件版本管理',
    'endpoint': 'device.device_center',
    'tables': ['devices', 'device_alerts', 'device_version_changes', 'ota_versions', 'ota_upgrades'],
    'menu': [
        {'name': '设备运维', 'endpoint': 'device.device_center', 'icon': '📡'},
    ],
}
