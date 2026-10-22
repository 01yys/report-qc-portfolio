"""报告质控三规则本机工作台与产品材料入口。

只监听本机；新版网页复用src.report_checker，旧P0接口继续兼容。
"""

from __future__ import annotations

import json
import html
import argparse
import getpass
import sys
import warnings
import hashlib
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from src.laterality_checker import check_laterality
from src.report_checker import check_report
from src.web_ai_service import WebAIService, RequestBudget, current_batch_date
from src.deepseek_client import valid_key_transport_format
from src.knowledge_assistant import KnowledgeService


HOST = "127.0.0.1"
PORT = 8765
WEB_DIRECTORY = Path(__file__).parent / "web"
EVALUATION_DIRECTORY = Path(__file__).parent.parent / "05_测试与评测"
PROJECT_DIRECTORY = Path(__file__).parent.parent
DOCUMENTS = {
    "/docs/prd": "docs/PRODUCT_SPEC.md",
    "/docs/rag": "docs/AI_DESIGN.md",
    "/docs/review": "docs/ACCEPTANCE.md",
    "/docs/case": "docs/PROJECT_OVERVIEW.md",
}
ASSETS = {
    "/assets/knowledge-workflow.svg": "03_AI方案与工作流/04_流程图_知识助手RAG与Agent-v0.1.svg",
    "/assets/product-process.svg": "00_项目治理/07_流程图_报告质控项目构建流程-v1.1.svg",
    "/assets/check-workflow.svg": "03_AI方案与工作流/02_流程图_三模块报告人工复核-v1.2.svg",
}
PUBLIC_DOCUMENTS = {"/docs/prd": "docs/PRODUCT_SPEC.md", "/docs/rag": "docs/AI_DESIGN.md",
                    "/docs/review": "docs/ACCEPTANCE.md", "/docs/case": "docs/PROJECT_OVERVIEW.md"}


def material_path(route, internal):
    """独立交付包只用包内公开稿；本工作台仍使用已有内部材料。"""
    public = PROJECT_DIRECTORY / PUBLIC_DOCUMENTS.get(route, "__not_public__")
    return public if public.is_file() else PROJECT_DIRECTORY / internal


def acceptance_summary():
    path = EVALUATION_DIRECTORY / "21_三规则团队复查后冻结验收结果-2026-10-18.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    provenance = data["provenance"]
    implementation_matches = all(
        (Path(__file__).parent / name).is_file() and
        hashlib.sha256((Path(__file__).parent / name).read_bytes()).hexdigest() == digest
        for name, digest in provenance["source_sha256"].items())
    # 只返回合成案例与安全字段，不返回个人路径或其他调用记录。
    records = [{**{k: r[k] for k in ("id", "module", "group", "findings_text", "impression_text", "expected_statuses",
                                  "expected_issue_count", "label_reason", "verdict", "mismatch_reasons", "evidence_valid", "comparable_pairs")},
                "actual_status": r["actual"]["status"], "actual_issue_count": len(r["actual"]["issues"])} for r in data["records"]]
    return {**{k: data[k] for k in ("total", "scored", "matched", "mismatched", "unscored", "groups", "caveat")},
            "dataset_file": provenance["dataset_file"], "dataset_sha256": provenance["dataset_sha256"],
            "checker_version": provenance["checker_version"], "created_on": provenance["created_on"],
            "independence_caveat": provenance["independence_caveat"],
            "snapshot_implementation_matches": implementation_matches, "cases": records}


def evaluation_summary() -> dict[str, Any]:
    """仅读取允许清单里的合成评测快照，不开放文件浏览或个人路径。"""
    # 公开候选包提供经过字段白名单整理的快照，不带内部完整目录。
    public_snapshot = PROJECT_DIRECTORY / "public_data/evaluation.json"
    if public_snapshot.is_file():
        result = json.loads(public_snapshot.read_text(encoding="utf-8"))
        result["acceptance"] = acceptance_summary()
        return result
    reports = []
    for name, filename in (
        ("左右旧基线（10-13）", "04_扩展模拟评测基线结果-2026-10-13.json"),
        ("左右否定隔离后（10-13）", "06_左右规则否定隔离后评测结果-2026-10-13.json"),
    ):
        data = json.loads((EVALUATION_DIRECTORY / filename).read_text(encoding="utf-8"))
        reports.append({"name": name, "source": filename, **{key: data[key] for key in (
            "sample_count", "labeled_count", "matched_count", "unscored_count"
        )}})
    return {"reports": reports, "caveat": "定向构造的合成开发集，标签仍待本人复核。以上是历史左右入口快照，不是临床准确率，也不代表当前三模块整体效果。",
            "acceptance": acceptance_summary()}


class ReportQcHandler(BaseHTTPRequestHandler):
    """提供页面、检查接口及允许清单中的产品材料。"""

    def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的固定方法名
        if self.path == "/api/knowledge":
            if self.headers.get("Host") != f"{HOST}:{self.server.server_port}":
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "仅允许本机固定地址访问。"})
                return
            service = getattr(self.server, "knowledge_service", None) or KnowledgeService()
            self._send_json(HTTPStatus.OK, {**service.library.overview(), "ai":self.server.ai_service.capabilities()})
            return
        if self.path == "/api/ai-capabilities":
            if self.headers.get("Host") != f"{HOST}:{self.server.server_port}":
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "仅允许本机固定地址访问。"})
                return
            self._send_json(HTTPStatus.OK, self.server.ai_service.capabilities())
            return
        if self.path in DOCUMENTS:
            try:
                source = material_path(self.path, DOCUMENTS.get(self.path, "__not_public__")).read_text(encoding="utf-8")
            except OSError:
                self.send_error(HTTPStatus.NOT_FOUND, "Document unavailable")
                return
            # 可读原文视图；不执行 Markdown HTML，避免资料内容成为脚本。
            content = ('<!doctype html><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">'
                       '<title>报告质控｜产品材料</title><style>body{margin:32px auto;padding:0 24px;max-width:1100px;color:#18345b;font-family:system-ui;line-height:1.8}pre{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}a{color:#0864ff}</style>'
                       '<a href="/">返回工作台</a><pre>' + html.escape(source) + '</pre>').encode("utf-8")
            self._send_content(content, "text/html; charset=utf-8")
            return
        if self.path in ASSETS:
            try:
                public = PROJECT_DIRECTORY / "assets" / self.path.rsplit("/", 1)[1]
                self._send_content((public if public.is_file() else PROJECT_DIRECTORY / ASSETS[self.path]).read_bytes(), "image/svg+xml")
            except OSError:
                self.send_error(HTTPStatus.NOT_FOUND, "Asset unavailable")
            return
        if self.path == "/api/evaluation":
            try:
                self._send_json(HTTPStatus.OK, evaluation_summary())
            except (OSError, ValueError, KeyError):
                self._send_json(HTTPStatus.SERVICE_UNAVAILABLE, {"error": "合成评测快照暂不可用，请重试。"})
            return
        if self.path in ("/", "/index.html"):
            self._send_file("index.html", "text/html; charset=utf-8")
            return
        if self.path == "/style.css":
            self._send_file("style.css", "text/css; charset=utf-8")
            return
        if self.path == "/app.js":
            self._send_file("app.js", "application/javascript; charset=utf-8")
            return
        if self.path in ("/samples.js", "/ui.js", "/review.js", "/knowledge.js"):
            self._send_file(self.path[1:], "application/javascript; charset=utf-8")
            return
        if self.path == "/favicon.ico":
            self.send_response(HTTPStatus.NO_CONTENT)
            self.end_headers()
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 的固定方法名
        if self.path not in ("/api/check", "/api/check-v1", "/api/check-v2", "/api/ai-review", "/api/knowledge/query"):
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        if self.path in ("/api/check-v2", "/api/ai-review", "/api/knowledge/query"):
            origin = f"http://{HOST}:{self.server.server_port}"
            if (self.headers.get("Host") != f"{HOST}:{self.server.server_port}" or
                    self.headers.get("Origin") != origin or
                    self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json"):
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "仅接受本机同源JSON请求。"})
                return
            if self.path in ("/api/ai-review", "/api/knowledge/query") and self.headers.get("X-QC-Token") != self.server.ai_service.csrf:
                self._send_json(HTTPStatus.FORBIDDEN, {"error": "本机会话验证失败；未请求模型，请刷新页面。"})
                return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0:
                raise ValueError("Empty request")
            if content_length > 200000:
                self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "请求内容过长。"})
                return
            body = self.rfile.read(content_length)
            payload = json.loads(body.decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("Expected a JSON object")
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError):
            self._send_json(
                HTTPStatus.BAD_REQUEST,
                {"error": "请求格式错误，请重新填写两段模拟文本后重试。"},
            )
            return

        if self.path == "/api/knowledge/query":
            try:
                service = getattr(self.server, "knowledge_service", None) or KnowledgeService()
                self._send_json(HTTPStatus.OK, service.query(payload))
            except ValueError as exc:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error":str(exc)})
            return
        if self.path == "/api/check-v2":
            try:
                result = self.server.ai_service.check(payload)
                self._send_json(HTTPStatus.OK, result)
            except ValueError:
                self._send_json(HTTPStatus.BAD_REQUEST, {"error": "输入版本无效，请重新检查。"})
            return
        if self.path == "/api/ai-review":
            status, result = self.server.ai_service.review(payload)
            self._send_json(status, result)
            return
        if self.path == "/api/check-v1":
            result = check_report(payload.get("findings_text"), payload.get("impression_text"), payload.get("modules"))
        else:
            result = check_laterality(payload.get("findings_text"), payload.get("impression_text"))
        self._send_json(HTTPStatus.OK, result)

    def _send_file(self, filename: str, content_type: str) -> None:
        try:
            content = (WEB_DIRECTORY / filename).read_bytes()
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        self._send_content(content, content_type)

    def _send_content(self, content: bytes, content_type: str) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def _send_json(self, status: HTTPStatus, payload: dict[str, Any]) -> None:
        content = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        """保留简洁的本机访问日志，便于演示时定位问题。"""
        print(f"[本机页面] {self.address_string()} - {format % args}")


def main() -> None:
    """启动仅本机可访问的演示服务。"""
    parser = argparse.ArgumentParser(description="报告质控本机网页；默认仅运行免费规则。")
    parser.add_argument("--ai", action="store_true", help="配合显式日期与次数，隐藏输入Key启用网页AI")
    parser.add_argument("--ai-date", help="已获授权的批次日期，格式YYYY-MM-DD；不会自动续期")
    parser.add_argument("--ai-max-requests", type=int, help="已获授权的请求次数，1至10")
    args = parser.parse_args()
    records = EVALUATION_DIRECTORY / "网页AI调用记录"
    budget = None
    if args.ai:
        if args.ai_date is None or args.ai_max_requests is None:
            parser.error("--ai必须同时提供--ai-date和--ai-max-requests；旧授权不会自动续期。")
        try:
            budget = RequestBudget(records / f"网页AI请求额度-{args.ai_date}.jsonl",
                                   batch_date=args.ai_date, max_requests=args.ai_max_requests)
            if current_batch_date() != budget.batch_date:
                raise ValueError("本批AI授权日期与今天不符；不会索取Key。")
            if budget.count() >= budget.max_requests:
                raise ValueError("本批请求额度已用完；不会索取Key。")
        except (ValueError, OSError) as exc:
            parser.error(str(exc) if isinstance(exc, ValueError) else "额度记录不可用；不会索取Key。")
    elif args.ai_date is not None or args.ai_max_requests is not None:
        parser.error("--ai-date和--ai-max-requests只能与--ai一起使用。")
    # 先占用端口，避免用户输入完Key后才发现旧服务未关。
    server = ThreadingHTTPServer((HOST, PORT), ReportQcHandler)
    key = ""
    if args.ai:
        if not sys.stdin.isatty():
            server.server_close()
            raise SystemExit("必须在可交互终端隐藏输入Key；不接受命令参数或管道。")
        print(f"本批日期{budget.batch_date}：网页至多{budget.max_requests}次新请求，无自动重试；每次最多1200输出tokens。")
        print("费用目标10元不是精确封顶；失败/中断也保守占一次，重启不清零。Key仅进程内使用。")
        if input(f"确认启用该限制请输入{budget.max_requests}，其他取消：").strip() != str(budget.max_requests):
            server.server_close()
            return
        with warnings.catch_warnings():
            warnings.simplefilter("error", getpass.GetPassWarning)
            try:
                key = getpass.getpass("请粘贴DeepSeek API Key（不显示字符），然后回车：").strip()
            except (getpass.GetPassWarning, EOFError, KeyboardInterrupt):
                server.server_close()
                raise SystemExit("输入已取消或无法隐藏，未启动AI服务。")
        if not valid_key_transport_format(key):
            server.server_close()
            raise SystemExit("Key为空或格式不适合HTTP传输，未启动服务；不要发送Key到聊天。")
    server.ai_service = WebAIService(api_key=key,
        budget=budget, record_dir=records)
    server.knowledge_service = KnowledgeService(api_key=key, budget=budget, record_dir=records)
    print(f"报告质控本机工作台已启动： http://{HOST}:{PORT}")
    print("按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
