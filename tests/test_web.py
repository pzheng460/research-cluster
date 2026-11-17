"""Opt-in browser QA; runs against a real API and CPU worker."""
import os
import threading

import pytest

from test_execution import runtime
from test_verification import lean_home, lean_runtime
from conftest import hardware_report

pytestmark = pytest.mark.skipif(os.getenv("CLUSTER_TEST_WEB") != "1", reason="Set CLUSTER_TEST_WEB=1 for browser tests")


@pytest.fixture
def web_runtime(runtime):
    from research_cluster.research import run_local_orchestrator
    stop = threading.Event()
    thread = threading.Thread(target=run_local_orchestrator, args=(runtime[2], stop), daemon=True)
    thread.start()
    try:
        yield runtime
    finally:
        stop.set()
        thread.join(10)


def test_management_ui_runs_real_work_and_handles_mobile(web_runtime, monkeypatch):
    from playwright.sync_api import expect, sync_playwright
    client, _, _ = web_runtime
    response = client.post("/api/inventory", json=hardware_report())
    assert response.status_code == 200
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.goto(str(client.base_url))
        expect(page.get_by_role("heading", name="研究工作台")).to_be_visible()
        expect(page.get_by_test_id("overview-cpu-count")).to_contain_text("192")
        expect(page.get_by_test_id("overview-gpu-count")).to_contain_text("8")
        page.get_by_role("button", name="运行 CPU 基准研究").click()
        expect(page.locator("[data-study]")).to_have_count(1)
        expect(page.locator("[data-study] .badge")).to_have_text("已完成", timeout=30000)
        page.get_by_role("link", name="计算资源", exact=True).click()
        expect(page.locator(".pool-card")).to_have_count(3)
        expect(page.get_by_test_id("hardware-cpu-total")).to_have_text("192")
        expect(page.get_by_test_id("hardware-gpu-total")).to_have_text("8")
        expect(page.get_by_test_id("hardware-cpu-limit")).to_contain_text("容器 CPU 配额：96")
        expect(page.locator(".device-chip")).to_have_count(8)
        page.get_by_role("button", name="新建资源池").click()
        page.locator('[name="name"]').fill("Finance CPU")
        page.locator('[name="id"]').fill("finance-cpu")
        page.get_by_role("button", name="创建资源池").click()
        expect(page.locator(".pool-card")).to_have_count(4)
        page.get_by_role("link", name="实验队列", exact=False).first.click()
        expect(page.locator("[data-job]")).to_have_count(5)
        page.locator("[data-job]").first.click()
        expect(page.locator(".log-view")).to_contain_text("score")
        expect(page.locator(".artifact")).not_to_have_count(0)
        page.get_by_role("button", name="关闭详情", exact=True).click()
        page.get_by_role("button", name="提交实验", exact=True).click()
        page.locator('[name="name"]').fill("Browser experiment")
        page.get_by_role("button", name="提交实验", exact=True).last.click()
        expect(page.locator("[data-job]")).to_have_count(6)
        row = page.locator("[data-job]").filter(has_text="Browser experiment")
        expect(row.locator(".badge")).to_have_text("成功", timeout=20000)
        page.get_by_role("link", name="连接与配置").click()
        expect(page.get_by_role("heading", name="连接与配置")).to_be_visible()
        for _ in range(2):
            page.get_by_role("button", name="设置访问令牌").click()
            expect(page.locator("#connect-form")).to_be_visible()
            page.get_by_role("button", name="连接", exact=True).click()
            expect(page.locator("#modal")).not_to_be_visible()
        from research_cluster import literature
        from test_literature import fake_collection
        monkeypatch.setattr(literature, 'collect', fake_collection)
        monkeypatch.setattr(literature, 'read_excerpt', lambda _: (_ for _ in ()).throw(ValueError('No HTML in fixture')))
        monkeypatch.delenv('LLM_API_KEY', raising=False)
        monkeypatch.delenv('LLM_MODEL', raising=False)
        page.goto(str(client.base_url)+'#overview')
        page.get_by_role('button',name='新建研究',exact=True).click()
        expect(page.locator('#study-mode')).to_have_value('literature')
        expect(page.locator('#literature-fields')).to_be_visible()
        page.locator('[name="name"]').fill('DSpark architecture research')
        page.locator('[name="objective"]').fill('Review latest papers and propose testable architecture tasks')
        expect(page.locator('#lean-mode')).to_have_value('off')
        page.get_by_role('button',name='创建研究',exact=True).click()
        expect(page.get_by_role('heading',name='文献与研究任务',exact=True)).to_be_visible()
        expect(page.locator('.proposal-card')).to_have_count(3,timeout=15000)
        page.get_by_role('button',name='查看实验设计').first.click()
        expect(page.locator('#modal-body')).to_contain_text('对照与基线')
        page.get_by_role('button',name='关闭',exact=True).click()
        page.get_by_role('button',name='采纳任务').first.click()
        expect(page.locator('.proposal-card').first.locator('.proposal-top .badge')).to_have_text('已采纳')
        page.get_by_role('button', name='查看实验设计').first.click()
        page.get_by_role('button', name='评估证明义务').click()
        page.locator('#formal-assessment-form [name="status"]').select_option('recommended')
        page.locator('#formal-assessment-form [name="rationale"]').fill('A precise sampling invariant would benefit from a proof.')
        page.locator('#formal-assessment-form [name="obligations"]').fill('State the exact sampling assumptions')
        page.get_by_role('button', name='保存评估', exact=True).click()
        expect(page.locator('.proposal-card').first.locator('.formal-assessment')).to_contain_text('建议证明')
        assert len(client.get('/api/state').json()['jobs']) == 6
        page.get_by_role('link', name='研究树', exact=True).click()
        expect(page.get_by_role('heading', name='研究树', exact=True)).to_be_visible()
        expect(page.locator('[data-tree-node][data-kind="proposal"]')).to_have_count(3)
        expect(page.locator('[data-tree-node][data-kind="paper"]')).to_have_count(0)
        expect(page.locator('.tree-runtime')).to_contain_text('模型未配置')
        page.locator('[data-tree-node][data-kind="proposal"]').first.click()
        expect(page.locator('#tree-node-details')).to_contain_text('查看完整实验设计')
        page.get_by_role('button', name='查看完整实验设计').click()
        expect(page.locator('#modal-body')).to_contain_text('对照与基线')
        page.get_by_role('button', name='关闭', exact=True).click()
        page.get_by_role('button', name='展开全部', exact=True).click()
        expect(page.locator('[data-tree-node][data-kind="paper"]')).to_have_count(2)
        transform = page.locator('#research-tree-transform').get_attribute('transform')
        page.get_by_role('button', name='放大研究树', exact=True).click()
        zoomed = page.locator('#research-tree-transform').get_attribute('transform')
        assert zoomed != transform
        page.wait_for_timeout(3500)
        assert page.locator('#research-tree-transform').get_attribute('transform') == zoomed
        page.get_by_role('button', name='聚焦任务', exact=True).click()
        expect(page.locator('[data-tree-node][data-kind="paper"]')).to_have_count(0)
        benchmark = next(s for s in client.get('/api/state').json()['studies'] if s['mode'] == 'benchmark')
        page.locator('#tree-root').select_option(benchmark['id'])
        expect(page.locator('[data-tree-node][data-kind="candidate"]')).to_have_count(5)
        expect(page.locator('[data-tree-node][data-kind="result"]')).to_have_count(5)
        page.locator('[data-tree-node][data-kind="candidate"]').first.click()
        page.get_by_role('button', name='查看日志、源码与产物').click()
        expect(page.locator('.log-view')).to_contain_text('score')
        page.get_by_role('button', name='关闭详情', exact=True).click()
        page.set_viewport_size({"width": 390, "height": 844})
        expect(page.locator('#research-tree-canvas')).to_be_visible()
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        page.goto(str(client.base_url) + "#overview")
        expect(page.get_by_role("heading", name="研究工作台")).to_be_visible()
        assert not page.evaluate("document.documentElement.scrollWidth > innerWidth")
        assert not errors, errors
        browser.close()


def test_formal_verification_ui_checks_real_proof_and_shows_tree(lean_runtime):
    from playwright.sync_api import expect, sync_playwright
    client, _, _ = lean_runtime
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=['--no-sandbox'])
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        page.on('pageerror', lambda error: errors.append(str(error)))
        page.goto(str(client.base_url) + '#verification')
        expect(page.get_by_role('heading', name='形式验证', exact=True)).to_be_visible()
        page.get_by_role('button', name='新建研究', exact=True).click()
        page.locator('#lean-mode').select_option('required')
        expect(page.locator('#lean-policy-fields')).to_be_visible()
        expect(page.locator('[name="lean_spec_source"]')).to_have_attribute('required', '')
        page.get_by_role('button', name='取消', exact=True).click()
        page.get_by_role('button', name='运行 Lean 验证自检', exact=True).click()
        expect(page.get_by_role('heading', name='研究树', exact=True)).to_be_visible()
        expect(page.locator('[data-kind="verification"] .tree-node-status')).to_have_text('已证明', timeout=150000)
        expect(page.locator('[data-kind="obligation"] .tree-node-status')).to_have_text('已证明')
        page.locator('[data-kind="verification"]').click()
        page.get_by_role('button', name='查看证明报告与日志').click()
        expect(page.locator('#modal-body')).to_contain_text('已重检')
        expect(page.locator('#modal-body')).to_contain_text('ClusterSpec.Conserves')
        page.get_by_role('button', name='日志与证明源码').click()
        expect(page.locator('.log-view')).to_contain_text('kernel_rechecked')
        expect(page.locator('.artifact')).not_to_have_count(0)
        page.get_by_role('button', name='关闭详情', exact=True).click()
        page.get_by_role('link', name='形式验证', exact=True).click()
        expect(page.locator('.proof-card')).to_have_count(1)
        expect(page.locator('.proof-card .badge')).to_have_text('已证明')
        page.set_viewport_size({'width': 390, 'height': 844})
        assert not page.evaluate('document.documentElement.scrollWidth > innerWidth')
        assert not errors, errors
        browser.close()
