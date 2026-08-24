"""Genereer seed-blogs-4.ts met de 2 resterende wereldklasse Bijeen-drafts (score 86-90)
uit de ImpactOS pending_review-jobs. Idempotent, zelfde structuur als seed-blogs-3.ts."""
import sqlite3, os, re, sys
sys.path.insert(0, r"D:\APPS\impactos")

VAULT = r"D:\APPS\Hermes Brein\Hermes Breind\10_Projects\Bijeen\content"
OUT = r"D:\apps\bijeen\welzijnsevent-starter\welzijnsevent\src\db\seed-blogs-4.ts"

def slugify(s):
    s = re.sub(r"[^a-z0-9\s-]", "", s.lower().strip())
    return re.sub(r"\s+", "-", s)[:70]

conn = sqlite3.connect(r"D:\APPS\impactos\data\impactos.db")
conn.row_factory = sqlite3.Row
rows = conn.execute("""
    SELECT cj.title, cj.seo_score, cj.slug, cj.blog_html, cj.keyword
    FROM content_jobs cj JOIN sites s ON cj.site_id=s.id
    WHERE s.name='Bijeen' AND cj.status='pending_review' AND cj.blog_html IS NOT NULL AND cj.blog_html != ''
""").fetchall()

posts = []
for r in rows:
    html = r["blog_html"] or ""
    if len(html) < 200:
        continue
    base = r["slug"].replace("herschrijf-het-artikel-voor-bijeen-", "").replace("herschrijf-het-artikel-", "")
    slug = slugify(base)[:70]
    title = r["title"]
    kw = r["keyword"] or ""
    excerpt = (title)[:180]
    tags = "[" + ", ".join(f'"{t.strip()}"' for t in kw.replace("  ", " ").split(" ")[:5] if t.strip()) + "]"
    rt = max(4, len(html.split()) // 200)
    posts.append(f"""    {{
      slug: "{slug}",
      title: "{title}",
      excerpt: "{excerpt}",
      content: `{html}`,
      metaTitle: "{title}",
      metaDescription: "{excerpt}",
      tags: {tags},
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

  console.log("🌱 Seeding wereldklasse Bijeen blog posts (batch 4)...");

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
    f.write(header + "\n".join(posts) + footer)
print(f"KLAAR: {OUT} ({len(posts)} artikelen)")
