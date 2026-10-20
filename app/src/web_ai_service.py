"""本机网页两阶段复核。服务端绑定输入、单次票据与跨重启请求额度。

每批显式配置请求日期和次数，旧默认仍为10月18日10次，不自动续期。
次数是本项目网页请求的保守预留数，不是人民币封顶或账户级限额。
没有Key时不读取环境或其他项目配置；不对外提供凭据设置接口。
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import fcntl
import json
import os
import secrets
import threading
import time
from uuid import uuid4

from .deepseek_client import call_deepseek_once
from .report_checker import check_report
from .review_fusion import fuse_review_result
from .semantic_review import PROMPT_VERSION

BATCH_DATE = "2026-10-18"
MAX_REQUESTS = 10


def current_batch_date():
    return datetime.now(ZoneInfo("Asia/Shanghai")).date().isoformat()


class RequestBudget:
    """先落盘预留，再尝试请求。中断也不返还次数，重启不清零。"""
    def __init__(self, path: Path, *, today=None, batch_date=BATCH_DATE, max_requests=MAX_REQUESTS):
        try:
            valid_date = isinstance(batch_date, str) and date.fromisoformat(batch_date).isoformat() == batch_date
        except ValueError:
            valid_date = False
        if not valid_date:
            raise ValueError("批次日期必须是有效的YYYY-MM-DD日期。")
        if type(max_requests) is not int or not 1 <= max_requests <= MAX_REQUESTS:
            raise ValueError("本批请求上限必须是1至10的整数。")
        self.path = path
        self.batch_date, self.max_requests = batch_date, max_requests
        self.today = today or current_batch_date

    def _read(self, stream):
        stream.seek(0)
        rows = [json.loads(line) for line in stream if line.strip()]
        if any(not isinstance(r, dict) or r.get("batch_date") != self.batch_date or
               type(r.get("max_requests", MAX_REQUESTS)) is not int or
               r.get("max_requests", MAX_REQUESTS) != self.max_requests or
               not isinstance(r.get("request_id"), str) or not r["request_id"] for r in rows):
            raise ValueError("额度记录异常，停止调用，请先复核。")
        ids = [r["request_id"] for r in rows]
        if len(ids) != len(set(ids)):
            raise ValueError("额度记录重复，停止调用，请先复核。")
        return rows

    def count(self):
        if not self.path.exists():
            return 0
        with self.path.open(encoding="utf-8") as stream:
            fcntl.flock(stream, fcntl.LOCK_SH)
            return len(self._read(stream))

    def reserve(self, run_id):
        if self.today() != self.batch_date:
            raise ValueError("本批授权已到期；请重新确认日期和额度，不发送请求。")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a+", encoding="utf-8") as stream:
            os.chmod(self.path, 0o600)
            fcntl.flock(stream, fcntl.LOCK_EX)
            rows = self._read(stream)
            if len(rows) >= self.max_requests:
                raise ValueError(f"本批{self.max_requests}次请求额度已用完，不发送请求。")
            rid = str(uuid4())
            stream.write(json.dumps({"batch_date": self.batch_date, "max_requests": self.max_requests, "request_id": rid,
                                     "run_id": run_id, "reserved_at": datetime.now().astimezone().isoformat()},
                                    ensure_ascii=False) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
            return rid


class WebAIService:
    def __init__(self, *, api_key="", budget=None, record_dir=None, caller=None,
                 execution_source="live_api", clock=time.monotonic):
        self._key = api_key
        self._caller = caller or call_deepseek_once
        self._source = execution_source
        if execution_source not in ("live_api", "mock"):
            raise ValueError("网页仅支持本次实时调用或明确模拟联调。")
        self._budget, self._record_dir = budget, record_dir
        self._clock, self._runs, self._lock = clock, {}, threading.Lock()
        self._busy, self._halted = False, False
        self.csrf = secrets.token_urlsafe(32)

    def capabilities(self):
        batch_date = self._budget.batch_date if self._budget else BATCH_DATE
        maximum = self._budget.max_requests if self._budget else MAX_REQUESTS
        try:
            count = self._budget.count() if self._budget else 0
            expired = bool(self._budget and self._budget.today() != batch_date)
            ready = bool(self._key and self._budget and self._record_dir and not self._halted and not expired and count < maximum)
            reason = "" if ready else "AI未配置、额度已用完或本批已停止；规则仍可使用。"
        except (OSError, ValueError):
            count, ready, reason = None, False, "额度记录不可用，禁止调用；请先复核。"
        return {"ready": ready, "csrf_token": self.csrf, "execution_source": self._source,
                "reserved_requests": count, "max_requests": maximum,
                "batch_date": batch_date, "message": reason,
                "cost_caveat": "10元是费用目标而非精确封顶；每次最多1200输出tokens，无自动重试。"}

    def check(self, payload):
        revision = payload.get("input_revision")
        if type(revision) is not int or revision < 0:
            raise ValueError("input_revision必须是非负整数。")
        rule = check_report(payload.get("findings_text"), payload.get("impression_text"), payload.get("modules"))
        result = fuse_review_result(rule, input_revision=revision)
        if not rule.get("error_message"):
            with self._lock:
                self._runs = {k: v for k, v in self._runs.items() if self._clock() - v["created"] < 1200}
                while len(self._runs) >= 100:
                    del self._runs[next(iter(self._runs))]
                ticket = secrets.token_urlsafe(24)
                self._runs[ticket] = {"rule": rule, "revision": revision, "created": self._clock(), "state": "new"}
            result["ai_ticket"] = ticket
        return result

    def review(self, payload):
        if payload.get("confirm_synthetic") is not True or payload.get("confirm_paid") is not True:
            return 400, {"error": "需确认仅含合成文本且允许本次请求。"}
        with self._lock:
            ticket = payload.get("ai_ticket")
            item = self._runs.get(ticket) if isinstance(ticket, str) else None
            if not item or self._clock() - item["created"] >= 1200:
                return 409, {"error": "检查票据已过期或服务已重启，请先重新运行规则。"}
            if type(payload.get("input_revision")) is not int or payload["input_revision"] != item["revision"]:
                return 409, {"error": "输入版本已变更，请重新运行规则。"}
            if item["state"] == "done":
                return 200, deepcopy(item["result"])
            if item["state"] != "new" or self._busy:
                return 409, {"error": "已有AI请求未完成；不重复发送，请等待。"}
            if not self.capabilities()["ready"]:
                return 503, {"error": self.capabilities()["message"]}
            snapshot = item["rule"]["input_snapshot"]
            if any(len(snapshot[k]) > 2000 for k in ("findings_text", "impression_text")):
                return 400, {"error": "AI演示每段最多2000字符；未请求模型，规则结果保留。"}
            try:
                request_id = self._budget.reserve(item["rule"]["run_id"])
            except (OSError, ValueError) as exc:
                return 503, {"error": str(exc) if isinstance(exc, ValueError) else "无法写入额度记录，未发送请求。"}
            item["state"], self._busy = "pending", True
        try:
            try:
                provider = self._caller(snapshot["findings_text"], snapshot["impression_text"],
                                        allow_paid=True, api_key=self._key)
            except Exception:
                # 不回传任意异常文字；一次未知失败仍消耗保守预留额度。
                provider = {"llm_status": "failed", "error_code": "unknown_failure"}
            validation = provider.get("semantic_validation") or {}
            raw = json.dumps({"schema_version": validation.get("schema_version"),
                              "assessment": validation.get("assessment"),
                              "candidates": [{k: c.get(k) for k in ("kind", "findings_evidence", "impression_evidence")}
                                             for c in validation.get("candidates", [])]}, ensure_ascii=False)
            packet = {"input_snapshot": snapshot, "input_revision": item["revision"],
                      "execution_source": self._source, "prompt_version": PROMPT_VERSION,
                      "llm_status": provider.get("llm_status"), "error_code": provider.get("error_code"),
                      "response_json": raw}
            result = fuse_review_result(item["rule"], input_revision=item["revision"], semantic_packet=packet)
            result["ai_ticket"] = ticket
            # 仅复制适配层白名单元数据；不得复制Key、头部、任意原始异常。
            meta = {k: provider.get(k) for k in ("requested_model", "returned_model", "elapsed_ms", "usage",
                                                "http_status", "attempted_requests", "error_code", "client_version")}
            result["ai_call"] = {"request_id": request_id, "execution_source": self._source, **meta}
            try:
                self._record_dir.mkdir(parents=True, exist_ok=True)
                path = self._record_dir / f"网页AI-{self._budget.batch_date}-{request_id}.json"
                with path.open("x", encoding="utf-8") as stream:
                    os.chmod(path, 0o600)
                    json.dump({"data_kind": "user_confirmed_synthetic", "result": result}, stream, ensure_ascii=False, indent=2)
                    stream.flush()
                    os.fsync(stream.fileno())
            except OSError:
                result["persistence_warning"] = "结果未能留档，本批后续AI调用已停止；请保留页面导出并复核。"
                self._halted = True
            with self._lock:
                item.update(state="done", result=deepcopy(result))
            return 200, result
        finally:
            with self._lock:
                self._busy = False
