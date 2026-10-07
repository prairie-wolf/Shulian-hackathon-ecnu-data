"""Project regressions only; excludes personal hooks and real user data."""
from datetime import datetime, timedelta, timezone
import sys
import time
import unittest

MODULES = (
    "tests.test_upload_security", "tests.test_upload_filtering", "tests.test_upload_lifecycle",
    "tests.test_semantic", "tests.test_entity_equivalence", "tests.test_semantic_baseline",
    "tests.test_remaining_regressions", "tests.test_public_persistence", "tests.test_private_storage",
    "tests.test_answers", "tests.test_console_ui", "tests.test_machine_interfaces", "tests.test_check_gate",
)


def main():
    zone = timezone(timedelta(hours=8), "Asia/Taipei")
    started = datetime.now(zone)
    clock = time.monotonic()
    print("测试对象：面向 AI 的大数据平台；上传、语义、存储、问答、REST/MCP 与接口门禁", flush=True)
    print("开始时间：", started.isoformat(), "Asia/Taipei", flush=True)
    suite = unittest.defaultTestLoader.loadTestsFromNames(MODULES)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    print("结束时间：", datetime.now(zone).isoformat(), "Asia/Taipei", flush=True)
    print(f"耗时：{time.monotonic() - clock:.3f}s；执行 {result.testsRun} 项；"
          f"失败 {len(result.failures)}；错误 {len(result.errors)}；跳过 {len(result.skipped)}", flush=True)
    print("未测试：真实用户数据、外部 AI 服务、浏览器像素布局及生产部署。", flush=True)
    return 1 if not result.wasSuccessful() else (2 if result.skipped else 0)


if __name__ == "__main__":
    sys.exit(main())
