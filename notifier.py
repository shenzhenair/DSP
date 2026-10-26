# -*- coding: utf-8 -*-
"""飞书群机器人通知（移植自旧版日常监控脚本）"""
import warnings

from logutil import get_logger

_log = get_logger("notify")


def send_feishu(webhook, content, title="数据调度平台通知", timeout=15):
    """
    发送飞书 post 富文本消息。
    :param webhook: 群机器人 webhook 地址；为空则跳过
    :return: (ok: bool, msg: str)
    """
    if not webhook:
        _log.info(f"通知「{title}」未配置 Webhook，跳过发送")
        return False, "未配置飞书 Webhook，跳过发送"
    try:
        import requests
    except ImportError:
        warnings.warn("未安装 requests 库，无法发送飞书通知")
        return False, "服务器未安装 requests"

    payload = {
        "msg_type": "post",
        "content": {
            "post": {
                "zh_cn": {
                    "title": title,
                    "content": [[{"tag": "text", "text": content}]],
                }
            }
        },
    }
    try:
        resp = requests.post(url=webhook, json=payload, timeout=timeout)
        data = resp.json()
        if data.get("code") == 0:
            _log.info(f"通知「{title}」发送成功")
            return True, "发送成功"
        _log.warning(f"通知「{title}」发送失败: {data.get('msg', '未知错误')}")
        return False, f"飞书返回错误: {data.get('msg', '未知错误')}"
    except Exception as e:
        _log.error(f"通知「{title}」发送异常: {e}")
        return False, f"发送异常: {e}"


def render_template(tmpl, mapping):
    """把消息模板里的 {{key}} 占位符替换为实际值"""
    if not tmpl:
        return ""
    out = tmpl
    for k, v in mapping.items():
        out = out.replace("{{" + k + "}}", str(v))
    return out
