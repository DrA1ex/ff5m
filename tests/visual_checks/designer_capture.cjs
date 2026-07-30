// Headless canvas capture for validated Feather UI Designer scenarios.
//
// Copyright (C) 2026, Alexander K <https://github.com/drA1ex>
//
// This file may be distributed under the terms of the GNU GPLv3 license

"use strict";

const fs = require("fs");
const path = require("path");

async function mapConcurrent(items, limit, callback) {
  if (!Array.isArray(items) || !Number.isInteger(limit) || limit < 1) {
    throw new Error("invalid concurrent worker configuration");
  }
  const results = new Array(items.length);
  let nextIndex = 0;
  const workers = Array.from(
    { length: Math.min(limit, items.length) },
    async () => {
      while (nextIndex < items.length) {
        const index = nextIndex;
        nextIndex += 1;
        results[index] = await callback(items[index], index);
      }
    }
  );
  await Promise.all(workers);
  return results;
}

async function main() {
  const [designerRoot, serverUrl, planPath, outputDirectory, workerValue] =
    process.argv.slice(2);
  if (!designerRoot || !serverUrl || !planPath || !outputDirectory) {
    throw new Error(
      "usage: designer_capture.cjs DESIGNER_ROOT URL PLAN OUTPUT [WORKERS]");
  }
  const workerCount = Number.parseInt(workerValue || "2", 10);
  if (!Number.isInteger(workerCount) || workerCount < 1 || workerCount > 8) {
    throw new Error("Designer capture workers must be between 1 and 8");
  }
  const playwright = require(path.join(
    designerRoot, "ui_preview", "node_modules", "playwright"));
  const plan = JSON.parse(fs.readFileSync(planPath, "utf8"));
  fs.mkdirSync(outputDirectory, { recursive: true });

  const browser = await playwright.chromium.launch({ headless: true });
  const records = new Array(plan.cases.length);
  const manifestPath = path.join(outputDirectory, "manifest.json");
  const writeManifest = () => {
    const temporary = `${manifestPath}.tmp`;
    fs.writeFileSync(
      temporary,
      `${JSON.stringify(records.filter(Boolean), null, 2)}\n`
    );
    fs.renameSync(temporary, manifestPath);
  };
  let completed = 0;
  try {
    const workerIds = Array.from(
      { length: Math.min(workerCount, plan.cases.length) },
      (_value, index) => index
    );
    await mapConcurrent(workerIds, workerCount, async () => {
      const page = await browser.newPage({
        viewport: { width: 1600, height: 1000 },
      });
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
        // The Designer keeps a live status channel open, so networkidle is not
        // a meaningful readiness signal. The bridge below is the contract.
        await page.goto(serverUrl, { waitUntil: "domcontentloaded" });
        await page.waitForFunction(
          () => Boolean(window.FeatherDesignerV2Bridge?.scene()), null,
          { timeout: 30000 }
        );
        await page.evaluate(async () => {
          const home = document.getElementById("project-home");
          if (home) home.hidden = true;
          const response = await fetch("/api/session/reset", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: "{}",
          });
          if (!response.ok) {
            throw new Error("Designer session reset failed");
          }
        });

        while (true) {
          const index = records.findIndex((value) => value === undefined);
          if (index < 0) break;
          records[index] = null;
          const item = plan.cases[index];
          activeScene = item.scene;
          await page.evaluate(
            async (scenario) => window.FeatherDesignerV2Bridge.openScreen(
              scenario.semantic_page_id, false),
            item
          );
          await page.waitForFunction(
            (screen) => (
              window.FeatherDesignerV2Bridge?.scene()?.screen === screen
            ),
            item.semantic_page_id,
            { timeout: 10000 }
          );

          const filename =
            `${String(index + 1).padStart(3, "0")}-${item.id}.png`;
          const target = path.join(outputDirectory, filename);
          await page.locator("#preview-canvas").screenshot({
            path: target,
            animations: "disabled",
          });
          const scene = await page.evaluate(
            () => window.FeatherDesignerV2Bridge.scene()
          );
          activeScene = null;
          records[index] = {
            case_id: item.id,
            file: filename,
            label: item.label,
            page: scene.title,
            semantic_page_id: item.semantic_page_id,
            source: "designer",
            state: scene.state,
            diagnostics: scene.diagnostics || [],
            operations: (scene.operations || []).length,
          };
          completed += 1;
          writeManifest();
          process.stdout.write(
            `FF5M_CAPTURE_PROGRESS ${JSON.stringify({
              completed,
              total: plan.cases.length,
              case_id: item.id,
            })}\n`
          );
        }
      } finally {
        await page.close();
      }
    });
  } finally {
    await browser.close();
  }
  writeManifest();
}

module.exports = { mapConcurrent };

if (require.main === module) {
  main().catch((error) => {
    process.stderr.write(`${error.stack || error}\n`);
    process.exitCode = 1;
  });
}
