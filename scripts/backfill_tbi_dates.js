// Backfill publishedAt for de geïmporteerde Teambuilding-blogs naar de
// historische datums uit de import-state (zodat de blog chronologisch klopt).
const fs = require("fs");
const { PrismaClient } = require("@prisma/client");
const p = new PrismaClient();

const STATE = "D:/apps/impactos/scripts/.tbi_import_state.json";
const state = JSON.parse(fs.readFileSync(STATE, "utf-8"));
const dates = state.dates || {};

(async () => {
  const slugs = Object.keys(dates);
  console.log(`Backfill ${slugs.length} posts...`);
  let ok = 0;
  for (const slug of slugs) {
    const iso = dates[slug];
    const d = new Date(iso + "T09:00:00Z");
    const r = await p.blogPost.updateMany({
      where: { slug },
      data: { publishedAt: d },
    });
    if (r.count) {
      ok++;
      console.log(`  ${iso}  ${slug}`);
    } else {
      console.log(`  [NIET GEVONDEN] ${slug}`);
    }
  }
  console.log(`Klaar: ${ok}/${slugs.length} bijgewerkt.`);
  await p.$disconnect();
})().catch((e) => {
  console.error("ERR", e.message);
  process.exit(1);
});
