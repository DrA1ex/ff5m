// Headless canvas capture for validated Feather UI Designer scenarios.
//
// Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

"use strict";

const fs = require("fs");
const path = require("path");

async function main() {
  const [designerRoot, serverUrl, planPath, outputDirectory] =
    process.argv.slice(2);
  if (!designerRoot || !serverUrl || !planPath || !outputDirectory) {
    throw new Error(
      "usage: designer_capture.cjs DESIGNER_ROOT URL PLAN OUTPUT");
  }
  const playwright = require(path.join(
    designerRoot, "ui_preview", "node_modules", "playwright"));
  const plan = JSON.parse(fs.readFileSync(planPath, "utf8"));
  fs.mkdirSync(outputDirectory, { recursive: true });

  const browser = await playwright.chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
  const records = [];
  const manifestPath = path.join(outputDirectory, "manifest.json");
  const writeManifest = () => {
    const temporary = `${manifestPath}.tmp`;
    fs.writeFileSync(temporary, `${JSON.stringify(records, null, 2)}\n`);
    fs.renameSync(temporary, manifestPath);
  };
  let activeScene = null;
  try {
    await page.route("**/api/render", async (route) => {
      if (activeScene) {
        await route.fulfill({
          status: 200,
          contentType: "application/json",
          body: JSON.stringify(activeScene),
        });
      } else {
        await route.continue();
      }
    });
    // The Designer keeps a live status channel open, so networkidle is not a
    // meaningful readiness signal. The bridge below is the real contract.
    await page.goto(serverUrl, { waitUntil: "domcontentloaded" });
    await page.waitForFunction(
      () => Boolean(window.FeatherDesignerV2Bridge?.scene()), null,
      { timeout: 30000 });
    await page.evaluate(() => {
      const home = document.getElementById("project-home");
      if (home) home.hidden = true;
    });

    for (let index = 0; index < plan.cases.length; index += 1) {
      const item = plan.cases[index];
      activeScene = item.scene;
      await page.evaluate(async (scenario) => {
        const request = async (route, body) => {
          const response = await fetch(route, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(body || {}),
          });
          const value = await response.json();
          if (!response.ok) {
            throw new Error(value?.error?.message || `request failed: ${route}`);
          }
          return value;
        };
        await request("/api/session/reset", {});
        await window.FeatherDesignerV2Bridge.openScreen(
          scenario.semantic_page_id, false);
      }, item);
      await page.waitForFunction(
        (screen) => window.FeatherDesignerV2Bridge?.scene()?.screen === screen,
        item.semantic_page_id, { timeout: 10000 });

      const filename = `${String(index + 1).padStart(3, "0")}-${item.id}.png`;
      const target = path.join(outputDirectory, filename);
      await page.locator("#preview-canvas").screenshot({
        path: target,
        animations: "disabled",
      });
      const scene = await page.evaluate(
        () => window.FeatherDesignerV2Bridge.scene());
      activeScene = null;
      records.push({
        case_id: item.id,
        file: filename,
        label: item.label,
        page: scene.title,
        semantic_page_id: item.semantic_page_id,
        source: "designer",
        state: scene.state,
        diagnostics: scene.diagnostics || [],
        operations: (scene.operations || []).length,
      });
      writeManifest();
      process.stdout.write(
        `FF5M_CAPTURE_PROGRESS ${JSON.stringify({
          completed: index + 1,
          total: plan.cases.length,
          case_id: item.id,
        })}\n`
      );
    }
  } finally {
    await browser.close();
  }
  writeManifest();
}

main().catch((error) => {
  process.stderr.write(`${error.stack || error}\n`);
  process.exitCode = 1;
});
