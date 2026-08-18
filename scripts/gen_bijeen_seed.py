"""Genereer src/db/seed-blogs-3.ts met de 8 wereldklasse Bijeen-artikelen uit de vault.
Leest de 8 ge-upgrade HTML-bestanden (incl. frontmatter) en schrijft een idempotent
seed-script dat exact de structuur van seed-blogs.ts volgt."""
import os, re, sys

VAULT = r"D:\APPS\Hermes Brein\Hermes Breind\10_Projects\Bijeen\content"
OUT = r"D:\apps\bijeen\welzijnsevent-starter\welzijnsevent\src\db\seed-blogs-3.ts"

# slug (vault-bestand) -> (title, excerpt, meta_title, meta_description, tags, reading_time)
ARTS = [
    ("wmo-rapportage-evenement-software",
     "WMO-rapportage voor je evenement: zo meet je impact met Bijeen",
     "Hoe welzijnsorganisaties WMO-impact meten en rapporteren met Bijeen — praktisch, zonder dikke formulieren.",
     "WMO-rapportage evenement software: impact meten met Bijeen",
     "WMO-rapportage voor je welzijnsevenement hoe je impact meet en rapporteert met Bijeen, zonder dikke formulieren.",
     ["WMO", "rapportage", "impact", "welzijn"], 7),
    ("deelnemersbeheer-buurtfestival-gratis",
     "Deelnemersbeheer voor een buurtfestival: gratis en zonder gedoe",
     "Deelnemersbeheer voor een buurtfestival regel je gratis met Bijeen: aanmelding, registratie en check-in in één tool.",
     "Deelnemersbeheer buurtfestival gratis: aanmelding en registratie",
     "Deelnemersbeheer voor een buurtfestival regel je gratis met Bijeen: aanmelding, registratie en check-in in één tool.",
     ["deelnemersbeheer", "buurtfestival", "gratis", "registratie"], 6),
    ("vrijwilligersdag-organiseren-checklist-2026",
     "Vrijwilligersdag organiseren: de complete checklist (2026)",
     "De complete checklist voor een vrijwilligersdag in 2026: van planning en aanmelding tot check-in en nazorg.",
     "Vrijwilligersdag organiseren: de complete checklist (2026)",
     "De complete checklist voor een vrijwilligersdag in 2026: van planning en aanmelding tot check-in en nazorg.",
     ["vrijwilligersdag", "checklist", "organiseren", "welzijn"], 8),
    ("buurtinitiatieven-impact-meetbaar-maken",
     "Buurtinitiatieven: meet de impact van je evenementen",
     "Meet de impact van buurtinitiatieven en evenementen — waarom tracking van aanwezigheid verbinding sterker maakt.",
     "Buurtinitiatieven: meet de impact van je evenementen",
     "Meet de impact van buurtinitiatieven en evenementen: waarom tracking van aanwezigheid verbinding sterker maakt.",
     ["buurtinitiatieven", "impact", "meten", "verbinding"], 6),
    ("gemeente-evenement-aanmelden-systeem",
     "Gemeente-evenement aanmelden: bespaar tot 2 uur administratie per bijeenkomst",
     "Gemeente-evenementen aanmelden met een digitaal systeem: bespaar tot 2 uur administratie per bijeenkomst met Bijeen.",
     "Gemeente-evenement aanmelden: digitaal systeem bespaart 2 uur",
     "Gemeente-evenementen aanmelden met een digitaal systeem: bespaar tot 2 uur administratie per bijeenkomst met Bijeen.",
     ["gemeente", "evenement", "aanmelden", "administratie"], 7),
    ("geslaagd-evenement-organiseren-van-plan-tot-nazorg",
     "Van plan tot nazorg: een geslaagd evenement organiseer je zo",
     "Alle stappen om een geslaagd welzijnsevenement te organiseren: van mindful binnenkomst tot nazorg en veilige sfeer.",
     "Van plan tot nazorg: een geslaagd evenement organiseer je zo",
     "Alle stappen om een geslaagd welzijnsevenement te organiseren: van mindful binnenkomst tot nazorg en veilige sfeer.",
     ["evenement", "organiseren", "stappenplan", "nazorg"], 9),
    # 2 Gauntlet-drafts (score 90) — in vault onder schone slug
    ("sociale-cohesie-versterken-evenement-6-aanpakken",
     "Sociale cohesie versterken met een evenement: 6 aanpakken die werken",
     "Zes aanpakken om sociale cohesie te versterken met een evenement — van laagdrempelige ontmoeting tot slimme nazorg.",
     "Sociale cohesie versterken met een evenement: 6 aanpakken die werken",
     "Zes aanpakken om sociale cohesie te versterken met een evenement: van laagdrempelige ontmoeting tot slimme nazorg.",
     ["sociale cohesie", "evenement", "welzijn", "verbinding"], 8),
    ("verbinding-en-sociale-cohesie-evenement-mensen-bij-elkaar",
     "Verbinding en sociale cohesie: zo organiseer je een evenement dat mensen écht bij elkaar brengt",
     "Zo organiseer je een evenement dat echte verbinding en sociale cohesie brengt — praktisch en vanuit welzijnsperspectief.",
     "Verbinding en sociale cohesie: een evenement dat mensen bij elkaar brengt",
     "Zo organiseer je een evenement dat echte verbinding en sociale cohesie brengt: praktisch en vanuit welzijnsperspectief.",
     ["verbinding", "sociale cohesie", "evenement", "welzijn"], 8),
]

def extract_html(path):
    txt = open(path, encoding="utf-8").read()
    # verwijder frontmatter (--- ... ---)
    if txt.startswith("---"):
        txt = txt.split("---", 2)[2]
    return txt.strip()

posts_ts = []
for slug, title, excerpt, meta_title, meta_desc, tags, rt in ARTS:
    p = os.path.join(VAULT, slug + ".html")
    if not os.path.exists(p):
        print("MISSING:", slug); continue
    html = extract_html(p)
    tags_arr = "[" + ", ".join(f'"{t}"' for t in tags) + "]"
    posts_ts.append(f"""    {{
      slug: "{slug}",
      title: "{title}",
      excerpt: "{excerpt}",
      content: `{html}`,
      metaTitle: "{meta_title}",
      metaDescription: "{meta_desc}",
      tags: {tags_arr},
      readingTime: {rt},
      status: "published",
      publishedAt: new Date("2026-08-14"),
    }},""")

header = """import { readFileSync } from "fs";
import { join } from "path";

for (const f of [".env", ".env.local"]) {
  try {
    for (const line of readFileSync(join(process.cwd(), f), "utf8").split("\\n")) {
      const m = line.match(/^\\s*([^#\\s=][^=]*)=(.*)$/);
      if (m && !process.env[m[1].trim()]) process.env[m[1].trim()] = m[2].trim().replace(/^["']|["']$/g, "");
    }
  } catch {}
}

async function seed() {
  const { db } = await import("../db/index.js");
  const { blogPosts } = await import("../db/schema.js");
  const { eq } = await import("drizzle-orm");

  console.log("🌱 Seeding wereldklasse Bijeen blog posts (batch 3)...");

  const posts = [
"""
footer = """
  ];

  let created = 0, skipped = 0;
  for (const post of posts) {
    const existing = await db.select({ id: blogPosts.id })
      .from(blogPosts)
      .where(eq(blogPosts.slug, post.slug))
      .limit(1);
    if (existing.length > 0) {
      console.log(`  ⏭️  Overgeslagen (al aanwezig): ${post.slug}`);
      skipped++;
      continue;
    }
    await db.insert(blogPosts).values(post);
    console.log(`  ✅ Aangemaakt: ${post.title.slice(0, 60)}...`);
    created++;
  }
  console.log(`\\n✅ Klaar: ${created} aangemaakt, ${skipped} overgeslagen.`);
}

seed().catch((err) => {
  console.error("❌ Seed mislukt:", err);
  process.exit(1);
});
"""

with open(OUT, "w", encoding="utf-8") as f:
    f.write(header + "\n".join(posts_ts) + footer)

print(f"KLAAR: {OUT} ({len(posts_ts)} artikelen)")
