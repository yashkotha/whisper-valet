// Compose the launch still: UI demo scene at 16.2s + wordmark in the top band.
const { chromium } = require('playwright-core');
const fs = require('fs'); const path = require('path');
const EXEC = '/Users/dragonaire/Library/Caches/ms-playwright/chromium-1228/chrome-mac-arm64/Google Chrome for Testing.app/Contents/MacOS/Google Chrome for Testing';
(async () => {
  const browser = await chromium.launch({ executablePath: EXEC, headless: true, args: ['--force-color-profile=srgb'] });
  const page = await browser.newPage();
  await page.goto('file://' + path.join(__dirname, 'valet_ui_demo.html'));
  await page.waitForFunction('typeof window.captureFrame === "function"');
  const dataUrl = await page.evaluate(() => {
    renderFrame(972); // 16.2s: both windows, all transcript lines, footer note
    const c = document.getElementById('c'), x = c.getContext('2d');
    // wordmark in the empty top band, left-aligned with the main window
    x.save();
    x.textBaseline = 'alphabetic';
    x.letterSpacing = '-2.8px';
    x.font = '650 104px -apple-system, "SF Pro Display", "Helvetica Neue", sans-serif';
    x.fillStyle = '#F5F5F7';
    x.fillText('Whisper ', 560, 250);
    const w1 = x.measureText('Whisper ').width;
    x.font = '350 104px -apple-system, "SF Pro Display", "Helvetica Neue", sans-serif';
    x.fillStyle = '#C9CAD1';
    x.fillText('Valet', 560 + w1, 250);
    x.letterSpacing = '0px';
    x.font = '400 40px -apple-system, "SF Pro Display", "Helvetica Neue", sans-serif';
    x.fillStyle = '#85858E';
    x.fillText('The backstage crew for MacWhisper', 564, 330);
    x.restore();
    return c.toDataURL('image/png');
  });
  fs.writeFileSync(path.join(__dirname, 'ui_still.png'), Buffer.from(dataUrl.slice(22), 'base64'));
  console.log('still written');
  await browser.close();
})();
