const { chromium } = require('playwright');

const IRIS_PASSWORD = 'WeAreImpact!Iris2026';
const DASHBOARD_PASSWORD = 'VinZeeToren78!';
const VIDEO_PATH = 'file:///c:/users/v_mun/Downloads/Weareimpact_ai_workshop.mp4';

(async () => {
  const browser = await chromium.launch({ headless: false });
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 800 },
  });

  // ═══════════════════════════════════════════════════════════
  // STAP 1: Iris Remote — login met video overlay
  // ═══════════════════════════════════════════════════════════
  const irisPage = await ctx.newPage();
  await irisPage.goto('http://localhost:3470', { waitUntil: 'networkidle' });
  console.log('✅ Stap 1: Iris Remote login pagina geladen (localhost:3470)');

  await irisPage.evaluate((videoUrl) => {
    const overlay = document.createElement('div');
    overlay.id = 'iris-video-overlay';
    overlay.style.cssText = [
      'position:fixed', 'top:0', 'left:0', 'width:100vw', 'height:100vh',
      'background:#000', 'z-index:9999', 'display:flex', 'align-items:center',
      'justify-content:center', 'opacity:0', 'pointer-events:none',
      'transition:opacity 0.3s ease',
    ].join(';');
    overlay.innerHTML = '<video id="iris-workshop-video" autoplay muted loop playsinline style="max-width:90vw;max-height:90vh;border-radius:12px;box-shadow:0 20px 60px rgba(0,0,0,0.5);"><source src="' + videoUrl + '" type="video/mp4"></video>';
    document.body.appendChild(overlay);
  }, VIDEO_PATH);
  console.log('✅ Stap 2: Workshop-video als overlay geïnjecteerd');

  await irisPage.evaluate(() => {
    document.getElementById('iris-video-overlay').style.opacity = '1';
  });
  await irisPage.fill('#login-pw', IRIS_PASSWORD);
  console.log('✅ Stap 3: Iris-wachtwoord ingevuld');

  await Promise.all([
    irisPage.waitForResponse(resp => resp.url().includes('/api/ui?op=login') && resp.status() === 200),
    irisPage.click('#login-form button[type="submit"]'),
  ]);
  console.log('✅ Stap 4: Iris login succesvol — sessie cookie ontvangen');

  await irisPage.waitForTimeout(1000);
  await irisPage.evaluate(() => {
    const overlay = document.getElementById('iris-video-overlay');
    if (!overlay) return;
    overlay.style.transition = 'opacity 2s ease-in-out';
    overlay.style.opacity = '0';
  });
  console.log('✅ Stap 5: Video fade-out gestart (2s)');
  await irisPage.waitForTimeout(2500);

  const loginHidden = await irisPage.$eval('#view-login', el => el.hidden);
  const todayVisible = await irisPage.$eval('#view-today', el => !el.hidden);
  console.log('✅ Stap 6: Video verdwenen — Iris ingelogd | Login view verborgen:', loginHidden, '| Today view zichtbaar:', todayVisible);

  // ═══════════════════════════════════════════════════════════
  // STAP 7: AgentOS dashboard openen + inloggen (localhost:1250)
  // ═══════════════════════════════════════════════════════════
  const dashboardPage = await ctx.newPage();
  await dashboardPage.goto('http://localhost:1250', { waitUntil: 'networkidle' });
  console.log('✅ Stap 7: Impact OS login pagina geladen (localhost:1250)');

  await Promise.all([
    dashboardPage.waitForResponse(resp => resp.url().includes('/api/auth/login') && resp.status() === 200),
    dashboardPage.evaluate((pw) => {
      document.querySelector('input[placeholder="Wachtwoord"]').value = pw;
      const btn = Array.from(document.querySelectorAll('button')).find(b => b.textContent.includes('Inloggen'));
      if (btn) btn.click();
    }, DASHBOARD_PASSWORD),
  ]);
  console.log('✅ Stap 8: Impact OS inloggeving verzonden');

  await dashboardPage.waitForLoadState('networkidle');
  await dashboardPage.waitForTimeout(2000);
  console.log('✅ Stap 9: Impact OS dashboard geladen — onboarding tour zichtbaar');

  console.log('\n🎬 VOLTOOID: Iris login + video-fade-out → Impact OS Control Room :1250');
  await new Promise(r => setTimeout(r, 30000));
  await browser.close();
  process.exit(0);
})().catch(e => {
  console.error('❌ Fout:', e.message);
  process.exit(1);
});
