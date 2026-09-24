import { test, expect, type Page } from "@playwright/test";

async function login(page: Page, username = "teacher") {
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(username);
  await page.getByLabel("口令", { exact: true }).fill("test-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
}
const photo = "e2e/fixtures/paper.png";

test("role login, logout and student route boundary", async ({ page }) => {
  await login(page);
  await page.goto("/c/1");
  await expect(page.getByRole("region", { name: "优先处理" })).toBeVisible();
  await page.getByRole("button", { name: "退出登录" }).click();
  await login(page, "student");
  await page.goto("/admin/accounts");
  await expect(page).toHaveURL(/\/portal$/);
  await expect(page.getByRole("navigation", { name: "主导航" })).toHaveCount(0);
});

test("queued template upload survives reload and opens review", async ({ page }) => {
  await login(page);
  await page.goto("/c/1/exams/new");
  await page.getByPlaceholder("如：10月月考").fill("浏览器异步建卷");
  await page.locator('input[type="file"]').setInputFiles(photo);
  await page.getByRole("button", { name: /解析.*试卷|开始解析|解析并/ }).click();
  await expect(page).toHaveURL(/photo_job=\d+/);
  await page.reload();
  await expect(page.getByRole("status").filter({ hasText: "解析完成" })).toBeVisible({ timeout: 20000 });
  await page.getByRole("button", { name: "进入标注审核" }).click();
  await expect(page).toHaveURL(/\/exams\/\d+\/review$/);
  await expect(page.getByText("绝对值", { exact: true })).toBeVisible();
});

test("failed task can retry, transient poll failures are visible", async ({ page }) => {
  await login(page);
  let retried = false;
  let disconnect = true;
  await page.route("**/api/jobs/999**", async (route) => {
    if (route.request().method() === "POST") retried = true;
    if (disconnect && route.request().method() === "GET") { return route.abort("failed"); }
    await route.fulfill({ json: { id: 999, kind: "photo_template", status: retried ? "succeeded" : "failed",
      attempts: retried ? 4 : 3, max_attempts: 6, created_at: "2026-09-01T00:00:00", finished_at: null,
      error: retried ? null : "任务处理失败，请重试", result: retried ? { exam_id: 1, questions: 2, warnings: [] } : null } });
  });
  await page.goto("/c/1/exams/new?photo_job=999");
  await expect(page.getByRole("alert")).toContainText("正在重新连接");
  disconnect = false;
  await page.getByRole("button", { name: "重试解析" }).click();
  await expect(page.getByRole("button", { name: "进入标注审核" })).toBeVisible();
});

test("photo response queue completes before showing success", async ({ page }) => {
  await login(page);
  await page.goto("/c/1/exams/1/collect");
  await page.getByRole("button", { name: "拍照", exact: true }).click();
  await page.locator('input[type="file"]:not([multiple])').setInputFiles(photo);
  await expect(page).toHaveURL(/photo_job=\d+/);
  await page.reload();
  await expect(page.getByText(/解析完成.*请留意/)).toBeVisible({ timeout: 20000 });
  await expect(page.getByText("测试学生", { exact: true })).toBeVisible();
});

test("light/dark, mobile layout, graph and modal smoke screenshots", async ({ page }, testInfo) => {
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    await page.goto("/");
    await expect(page.getByRole("heading", { name: "欢迎回来" })).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await page.screenshot({ path: testInfo.outputPath(`login-${theme}.png`), fullPage: true });
  }
  await login(page);
  for (const width of [1280, 390]) {
    await page.setViewportSize({ width, height: 900 });
    for (const theme of ["light", "dark"] as const) {
      await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
      await page.goto("/c/1");
      await expect(page.getByRole("region", { name: "优先处理" })).toBeVisible();
      expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
      await page.screenshot({ path: testInfo.outputPath(`overview-${width}-${theme}.png`), fullPage: true });
    }
  }
  await page.setViewportSize({ width: 1280, height: 900 });
  await page.goto("/kb");
  await expect(page.getByRole("heading", { name: /知识库/ }).first()).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("knowledge-graph.png"), fullPage: true });
  await page.goto("/c/1/exams/1/collect");
  await page.getByRole("button", { name: /手工/ }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});


test("batch progress survives reload and reports network errors", async ({ page }) => {
  await login(page);
  let disconnected = true;
  await page.route("**/api/batch-jobs/777", async (route) => {
    if (disconnected) return route.abort("failed");
    await route.fulfill({ json: { job_id: 777, status: "done", items: [
      { id: 1, file_name: "paper.png", detected_name: "测试学生", matched_student_id: 1,
        matched_student_name: "测试学生", status: "matched", match_confidence: 0.99, warnings: [] },
    ] } });
  });
  await page.goto("/c/1/exams/1/collect?batch_job=777");
  await expect(page.getByRole("alert")).toContainText("正在重新连接");
  disconnected = false;
  await expect(page.getByText("已匹配", { exact: true }).first()).toBeVisible({ timeout: 15000 });
  await page.reload();
  await expect(page.getByText("paper.png", { exact: true })).toBeVisible();
});

test("teacher signs a report and student reads it in both themes", async ({ page }, testInfo) => {
  await login(page);
  await page.goto("/inbox");
  const draft = page.locator("div").filter({ has: page.getByText(/待签发回归报告/) })
    .filter({ has: page.getByRole("button", { name: "签发", exact: true }) }).last();
  await draft.getByRole("button", { name: "查看全文" }).click();
  await expect(page.locator("pre")).toContainText("复核后签发给学生");
  await draft.getByRole("button", { name: "签发", exact: true }).click();
  await expect(page.getByText(/待签发回归报告/)).toHaveCount(0);
  await page.getByRole("button", { name: "退出登录" }).click();
  await login(page, "student");
  await page.setViewportSize({ width: 390, height: 844 });
  const backgrounds: string[] = [];
  for (const theme of ["light", "dark"] as const) {
    await page.emulateMedia({ colorScheme: theme, reducedMotion: "reduce" });
    await page.goto("/portal/reports?report_id=2");
    await expect(page.getByRole("heading", { name: "待签发回归报告" })).toBeVisible();
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
    await expect(page.locator('nav a[aria-current="page"]')).toBeInViewport();
    backgrounds.push(await page.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--color-canvas").trim()));
    await page.screenshot({ path: testInfo.outputPath(`student-${theme}.png`), fullPage: true });
  }
  expect(backgrounds[0]).not.toBe(backgrounds[1]);
  await page.goto("/portal/study");
  await expect(page.getByText("还没有学习方案")).toBeVisible();
});


test("manual exam, tag review, score entry and final commit", async ({ page }) => {
  await login(page);
  await page.goto("/c/1/exams/new");
  await page.getByPlaceholder("如：10月月考").fill("浏览器提交闭环");
  await page.getByRole("tab", { name: "手工建卷", exact: true }).click();
  await page.getByRole("button", { name: "创建考试模板" }).click();
  await expect(page).toHaveURL(/\/exams\/\d+\/collect$/);
  await page.getByRole("link", { name: "2 审核", exact: true }).click();
  await expect(page).toHaveURL(/\/exams\/\d+\/review$/);
  const approve = page.getByRole("button", { name: /批准全部待审标注/ });
  await expect(approve).toBeVisible();
  if (await approve.isEnabled()) {
    await approve.click();
    await expect(approve).toBeDisabled();
  }
  await page.getByRole("button", { name: "下一步：采集学生卷" }).click();
  await page.getByRole("button", { name: "手工录入", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await dialog.getByRole("spinbutton").nth(0).fill("4");
  await dialog.getByRole("spinbutton").nth(1).fill("8");
  await dialog.getByRole("button", { name: "保存作答" }).click();
  await expect(dialog).toHaveCount(0);
  await expect(page.getByText("录入成功，该生总分 12")).toBeVisible();
  await page.getByRole("button", { name: "下一步：去提交" }).click();
  await page.getByRole("button", { name: /提交本场考试/ }).click();
  await page.getByRole("dialog").getByRole("button", { name: "确认提交", exact: true }).click();
  await expect(page.getByText(/已提交 1 份作答，生成 \d+ 条分析依据/)).toBeVisible();
  await page.reload();
  await expect(page.getByText("本场考试已提交并生成分析依据")).toBeVisible();
});
