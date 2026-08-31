// Iris login met video-overlay fade-out → AgentOS dashboard (localhost:1250)
// Draait met Playwright (geïnstalleerd in /d/apps/agentos/remote/node_modules)
const { chromium } = require('playwright');

const APP_PASSWORD = 'impactos+Vin1977!';
const VIDEO_PATH = 'file:///c:/users/v_mun/Downloads/Weareimpact_ai_workshop.mp4';

(async () => {
  const browser = await chromium.launch({ headless: false });
  const ctx = await browser.newContext({
    viewport: { width: 1280, height: 800 },
    baseURL: 'http://localhost:3470',
  });
  const page = await ctx.newPage();

  // ── Stap 1: Iris Remote login pagina openen ──
  await page.goto('http://localhost:3470', { waitUntil: 'networkidle' });
  console.log('✅ Iris Remote geladen op localhost:3470');

  // ── Stap 2: Video overlay injecteren (onzichtbaar tot login) ──
  await page.evaluate((videoUrl) => {
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
  console.log('✅ Video overlay geïnjecteerd');

  // ── Stap 3: Overlay zichtbaar maken + wachtwoord invullen + inloggen ──
  await page.evaluate(() => {
    document.getElementById('iris-video-overlay').style.opacity = '1';
  });

  await page.fill('#login-pw', APP_PASSWORD);
  console.log('✅ Wachtwoord ingevuld');

  // Klik inloggen en wacht op succesvolle response
  await Promise.all([
    page.waitForResponse(resp => resp.url().includes('/api/ui?op=login') && resp.status() === 200),
    page.click('#login-form button[type="submit"]'),
  ]);
  console.log('✅ Login succesvol — sessie cookie ontvangen');

  // ── Stap 4: Video blijft kort zichtbaar terwijl Iris SPA overgaat naar dashboard ──
  // Wacht 1 seconde, laat video zien, dan fade-out
  await page.waitForTimeout(1000);

  await page.evaluate(() => {
    const overlay = document.getElementById('iris-video-overlay');
    if (!overlay) return;
    overlay.style.transition = 'opacity 2s ease-in-out';
    overlay.style.opacity = '0';
  });
  console.log('✅ Fade-out gestart');

  await page.waitForTimeout(2500);
  console.log('✅ Video verdwenen');

  // ── Stap 5: Iris dashboard staat nu ingelogd → toon bevestiging ──
  const loginSection = await page.$('#view-login');
  const todaySection = await page.$('#view-today');
  const loginHidden = loginSection ? await loginSection.isHidden() : true;
  const todayVisible = todaySection ? await todaySection.isVisible() : false;

  await page.screenshot({ path: 'D:/_iris_dashboard_na_login.png', fullPage: true });
  console.log('📸 Screenshot opgeslagen: D:/_iris_dashboard_na_login.png');
  console.log('✅ Login view verborgen:', loginHidden, '| Today view zichtbaar:', todayVisible);

  // ── Stap 6: AgentOS dashboard openen in nieuw tabblad ──
  const dashboardPage = await ctx.newPage();
  await dashboardPage.goto('http://localhost:1250', { waitUntil: 'networkidle' });
  console.log('✅ AgentOS dashboard geladen op localhost:1250');

  await dashboardPage.screenshot({ path: 'D:/_agentos_dashboard_1250.png', fullPage: true });
  console.log('📸 Dashboard screenshot: D:/_agentos_dashboard_1250.png');

  console.log('\n🎬 VOLTOOID: Iris login met video-overlay → fade-out → AgentOS dashboard :1250');

  // Houd browser open voor observatie (sluit na 30s)
  await new Promise(r => setTimeout(r, 30000));
  await browser.close();
  process.exit(0);
})().catch(e => {
  console.error('❌ Fout:', e.message);
  process.exit(1);
});
