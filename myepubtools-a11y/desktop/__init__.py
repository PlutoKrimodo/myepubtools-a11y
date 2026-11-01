# -*- coding: utf-8 -*-
"""MyEpubTools 的 Windows 桌面层（PyQt6）。

本包只负责「交互外壳」：把原有 ``modules/`` 里的声明式页面配置渲染成
原生 Qt 控件，并在按钮事件里调用各业务模块的纯函数。

业务模块（``modules/`` 与 ``build_epub.py``）保持逐字节不变，
因此无障碍适配没有新增任何业务功能。
"""
