"""DeepSeek单次接入适配层。默认禁止调用，网页由本机服务显式开启。

仅从当前进程环境读取密钥，不读其他项目配置；不重试、不打印提供商原始错误。
已完成单例真实连通；按阶段记录失败，不把本地凭据异常称为模型响应错误。
"""
from __future__ import annotations

import json
from http.client import HTTPException
import os
import socket
import ssl
import time
from urllib import error, request

from .semantic_review import build_semantic_messages, validate_semantic_response, PROMPT_VERSION

ENDPOINT = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-flash"
MAX_BODY_BYTES = 1048576
CLIENT_VERSION = "m4-client-v0.3"
ERROR_MESSAGES = {
    "paid_call_not_enabled": "未启用真实调用，没有发送请求。",
    "invalid_input": "报告输入不符合要求，没有发送请求。",
    "preflight_input_limit": "输入超过单例限制，没有发送请求。",
    "missing_api_key": "本进程没有Key，没有发送请求。",
    "invalid_api_key_format": "Key中有非英文半角字符、空白或控制字符，已在本机拦截；请只复制密钥本身，不含说明、引号或Bearer前缀。",
    "request_encoding_error": "请求构造或编码失败，尚未收到服务响应；不代表模型返回了错误答案。",
    "request_setup_failed": "请求设置失败，尚未收到服务响应；请检查本机请求配置。",
    "authentication_failed": "服务返回鉴权失败，请核对Key及有效状态。",
    "insufficient_balance": "服务返回余额不足。",
    "rate_limited": "服务返回限流；本程序不自动重试。",
    "provider_http_error": "服务返回HTTP错误，请查看记录中的状态码。",
    "timeout": "连接或读取超时，是否计费未知，不自动重试。",
    "network_error": "网络连接失败，未取得可处理结果。",
    "tls_error": "安全连接验证或握手失败，不会关闭证书校验重试。",
    "http_protocol_error": "HTTP通信中断或协议异常，未取得完整结果。",
    "network_io_error": "网络读写异常，未取得完整结果。",
    "provider_response_too_large": "收到响应，但体积超过本机限制。",
    "provider_response_not_utf8": "收到响应，但不能按UTF-8解码。",
    "provider_response_not_json": "收到响应，但不是可解析的JSON；不能据此判断是Key还是服务中间环节问题。",
    "invalid_provider_response": "响应JSON的外层字段结构不符合预期。",
    "response_read_error": "已进入响应读取阶段，但读取结果不符合预期。",
    "incomplete_provider_output": "服务返回输出未完整结束，不将截断内容当作成功。",
    "invalid_model_output": "收到模型内容，但格式或原文证据校验未通过。",
}


def valid_key_transport_format(key: str) -> bool:
    """只检查本机HTTP兼容性，不推断Key真伪，不保存值或字符片段。"""
    return bool(key) and all(33 <= ord(char) <= 126 for char in key)


class NoRedirect(request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def call_deepseek_once(findings: str, impression: str, *, allow_paid: bool = False, api_key: str | None = None) -> dict:
    """至多一次外部请求。allow_paid需由调用者明确开启，不是预算管理器。"""
    result = {"provider": "DeepSeek", "requested_model": MODEL, "returned_model": None,
              "client_version": CLIENT_VERSION, "failure_stage": None, "response_received": False,
              "http_status": None, "exception_type": None,
              "prompt_version": PROMPT_VERSION, "llm_status": "not_called",
              "execution_source": "live_api", "attempted_requests": 0, "usage": None,
              "elapsed_ms": None, "semantic_validation": None, "error_code": None, "error_message": None}

    def fail(code, stage):
        result.update(error_code=code, error_message=ERROR_MESSAGES[code], failure_stage=stage)
        return result

    if not allow_paid:
        return fail("paid_call_not_enabled", "precheck")
    try:
        messages = build_semantic_messages(findings, impression)
    except ValueError:
        return fail("invalid_input", "precheck")
    if len(findings) > 2000 or len(impression) > 2000:
        return fail("preflight_input_limit", "precheck")
    # 网页服务使用进程内显式凭据；历史命令行仍兼容环境变量。不向浏览器传Key。
    key = (os.environ.get("DEEPSEEK_API_KEY", "") if api_key is None else api_key).strip()
    if not key:
        return fail("missing_api_key", "credential_precheck")
    if not valid_key_transport_format(key):
        return fail("invalid_api_key_format", "credential_precheck")
    payload = {"model": MODEL, "messages": messages, "stream": False,
               "response_format": {"type": "json_object"}, "thinking": {"type": "disabled"},
               "max_tokens": 1200, "temperature": 0}
    started = time.monotonic()
    result["llm_status"] = "failed"
    stage = "request_setup"
    try:
        req = request.Request(ENDPOINT, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                              headers={"Authorization": "Bearer " + key, "Content-Type": "application/json"},
                              method="POST")
        stage = "transport"
        # 这里只统计进入open的尝试；不证明服务已收到或已扣费。
        result["attempted_requests"] = 1
        with request.build_opener(NoRedirect()).open(req, timeout=30) as response:
            result["response_received"] = True
            status = response.getcode()
            result["http_status"] = status if isinstance(status, int) and not isinstance(status, bool) else None
            stage = "response_read"
            raw = response.read(MAX_BODY_BYTES + 1)
        if len(raw) > MAX_BODY_BYTES:
            return fail("provider_response_too_large", stage)
        stage = "response_decode"
        data = json.loads(raw.decode("utf-8"))
        stage = "provider_envelope"
        if not isinstance(data, dict):
            raise ValueError("invalid provider envelope")
        choice = data["choices"][0]
        model = data.get("model")
        result["returned_model"] = model if isinstance(model, str) and len(model) <= 200 else None
        usage = data.get("usage")
        if isinstance(usage, dict):
            result["usage"] = {k: v for k, v in usage.items() if k in (
                "prompt_tokens", "completion_tokens", "total_tokens", "prompt_cache_hit_tokens", "prompt_cache_miss_tokens"
            ) and isinstance(v, int) and not isinstance(v, bool) and v >= 0}
        if choice.get("finish_reason") != "stop":
            return fail("incomplete_provider_output", stage)
        content = choice["message"]["content"]
        stage = "model_output_validation"
        validated = validate_semantic_response(content, findings, impression)
        result["semantic_validation"] = validated
        result["llm_status"] = "completed" if validated["validation_status"] == "accepted" else "invalid_result"
        if result["llm_status"] == "invalid_result":
            fail("invalid_model_output", stage)
    except error.HTTPError as exc:
        result.update(response_received=True, http_status=exc.code, exception_type=type(exc).__name__)
        fail({401: "authentication_failed", 402: "insufficient_balance",
              429: "rate_limited"}.get(exc.code, "provider_http_error"), "http_status")
        exc.close()
    except (TimeoutError, socket.timeout) as exc:
        result["exception_type"] = type(exc).__name__
        fail("timeout", stage)
    except error.URLError as exc:
        result["exception_type"] = type(exc).__name__
        fail("tls_error" if isinstance(exc.reason, ssl.SSLError) else "network_error", stage)
    except UnicodeEncodeError as exc:
        result["exception_type"] = type(exc).__name__
        fail("request_encoding_error", stage)
    except UnicodeDecodeError as exc:
        result["exception_type"] = type(exc).__name__
        fail("provider_response_not_utf8", stage)
    except json.JSONDecodeError as exc:
        result["exception_type"] = type(exc).__name__
        fail("provider_response_not_json", stage)
    except ssl.SSLError as exc:
        result["exception_type"] = type(exc).__name__
        fail("tls_error", stage)
    except HTTPException as exc:
        result["exception_type"] = type(exc).__name__
        fail("http_protocol_error", stage)
    except OSError as exc:
        result["exception_type"] = type(exc).__name__
        fail("network_io_error", stage)
    except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
        result["exception_type"] = type(exc).__name__
        code = ("request_setup_failed" if stage in ("request_setup", "transport") else
                "response_read_error" if stage == "response_read" else "invalid_provider_response")
        fail(code, stage)
    finally:
        result["elapsed_ms"] = round((time.monotonic() - started) * 1000)
    return result
