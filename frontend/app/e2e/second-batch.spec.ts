import { test, expect, type Page } from "@playwright/test";

async function login(page: Page, username = "teacher") {
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill(username);
  await page.getByLabel("口令", { exact: true }).fill("test-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
}

test("teacher previews evidence and attribution groups before creating a diagnostic exam", async ({ page }, testInfo) => {
  await login(page);
  await page.goto("/c/2/students/2/diagnosis");
  const gaps = page.getByRole("region", { name: "最小补证据任务" });
  await gaps.getByRole("button", { name: "预览补证据任务", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("绝对值·理解独立检查题");
  await expect(dialog.getByRole("button", { name: "确认建卷，继续审核" })).toBeDisabled();
  await page.screenshot({ path: testInfo.outputPath("minimal-evidence.png") });
  await page.keyboard.press("Escape");
  const hypotheses = page.getByRole("button").filter({ hasText: "有理数的加法" }).first();
  await hypotheses.click();
  await page.getByRole("button", { name: "预览归因验证题组", exact: true }).click();
  await expect(dialog).toContainText("前置基础检查");
  await expect(dialog).toContainText("有理数的大小比较");
  await expect(dialog).toContainText("目标独立应用");
  await page.screenshot({ path: testInfo.outputPath("attribution-group.png") });
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("attribution-mobile.png") });
  await dialog.getByRole("checkbox").check();
  await dialog.getByRole("button", { name: "确认建卷，继续审核" }).click();
  await expect(page).toHaveURL(/\/c\/2\/exams\/\d+\/review$/);
  await expect(page.getByRole("button", { name: /批准全部待审标注/ })).toBeEnabled();
  await page.goto("/c/2");
  const workbench = page.getByRole("region", { name: "现在要处理" });
  await expect(workbench).toContainText("标注审核");
  await expect(workbench).toContainText("归因验证");
  await page.screenshot({ path: testInfo.outputPath("teacher-workbench.png"), fullPage: true });
});

test("student next task opens existing study workflow and preserves self-report on mobile", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await login(page, "student-next");
  await page.goto("/portal");
  const tasks = page.getByRole("region", { name: "学生下一步任务卡" });
  await expect(tasks).toContainText("完成标准");
  await tasks.getByRole("link", { name: "开始学习" }).first().click();
  await expect(page).toHaveURL(/\/portal\/study\?kp_code=/);
  await expect(page.getByRole("button", { name: /我学会了/ })).toBeVisible();
  await page.getByRole("button", { name: /我学会了/ }).click();
  await page.goto("/portal");
  await expect(tasks).toContainText("已自报，待复测");
  await page.reload();
  await expect(tasks).toContainText("已自报，待复测");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("student-next-mobile.png"), fullPage: true });
});


test("insufficient source remains visible and prevents diagnostic creation", async ({ page }, testInfo) => {
  await login(page);
  await page.goto("/c/1/students/1/diagnosis");
  const gaps = page.getByRole("region", { name: "最小补证据任务" });
  await gaps.getByRole("button", { name: "预览补证据任务" }).first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("缺少未作答且已审核的适用题目");
  await dialog.getByRole("checkbox").check();
  await expect(dialog.getByRole("button", { name: "确认建卷，继续审核" })).toBeDisabled();
  await page.screenshot({ path: testInfo.outputPath("source-gap.png") });
});
