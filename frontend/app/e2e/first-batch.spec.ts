import { test, expect } from "@playwright/test";

test("quality repair, diagnostic evidence, teaching card and persisted retest date", async ({ page }, testInfo) => {
  await page.goto("/");
  await page.getByLabel("用户名", { exact: true }).fill("teacher");
  await page.getByLabel("口令", { exact: true }).fill("test-password");
  await page.getByRole("button", { name: "登录", exact: true }).click();
  await expect(page.getByRole("button", { name: "退出登录" })).toBeVisible();
  await page.goto("/c/1/exams/2/commit");
  await expect(page.getByText(/缺少第 2 题得分/)).toBeVisible();
  await expect(page.getByRole("button", { name: /提交本场考试/ })).toBeDisabled();
  await page.getByLabel("确认测试学生第1题得分", { exact: true }).fill("1");
  await page.getByRole("button", { name: "保存测试学生第1题得分", exact: true }).click();
  await expect(page.getByText(/第 1 题得分越界/)).toHaveCount(0);
  await page.getByLabel("确认测试学生第2题得分", { exact: true }).fill("2");
  await page.getByRole("button", { name: "保存测试学生第2题得分", exact: true }).click();
  await expect(page.getByRole("button", { name: /提交本场考试/ })).toBeEnabled();
  await page.getByRole("button", { name: /提交本场考试/ }).click();
  await page.getByRole("button", { name: "确认提交", exact: true }).click();
  await expect(page.getByText(/已提交 1 份作答/)).toBeVisible();

  await page.goto("/c/1/students/1/diagnosis");
  await page.getByRole("button", { name: "查看诊断依据", exact: true }).click();
  await expect(page.getByText("质量预检样例 · 第 1 题", { exact: true })).toBeVisible();
  await expect(page.getByText("1 / 10 分", { exact: true })).toBeVisible();
  await expect(page.getByText("2 / 10 分", { exact: true })).toBeVisible();
  await page.screenshot({ path: testInfo.outputPath("diagnosis-evidence.png"), fullPage: true });

  await page.goto("/c/1/exams?tab=diagnosis");
  await page.getByRole("button", { name: "查看「绝对值」的十五分钟行动卡", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("讲解示范 · 5 分钟");
  await expect(page.getByRole("dialog")).toContainText("样例题1");
  await page.screenshot({ path: testInfo.outputPath("teaching-card.png") });
  const due = new Date(); due.setDate(due.getDate() + 3);
  const dateValue = `${due.getFullYear()}-${String(due.getMonth() + 1).padStart(2, "0")}-${String(due.getDate()).padStart(2, "0")}`;
  await page.getByLabel("行动卡复测日期").fill(dateValue);
  await page.getByRole("button", { name: "已执行，安排复测", exact: true }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.getByText("待复测", { exact: true }).last()).toBeVisible();
  await page.reload();
  await expect(page.getByLabel("绝对值的复测日期", { exact: true }).first()).toHaveValue(dateValue);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  await expect(page.getByLabel("绝对值的复测日期", { exact: true }).first()).toHaveValue(dateValue);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth + 1)).toBeTruthy();
  await page.screenshot({ path: testInfo.outputPath("retest-mobile.png"), fullPage: true });
});
