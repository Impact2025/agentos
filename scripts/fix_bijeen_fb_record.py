import sqlite3, json

con = sqlite3.connect("data/impactos.db")
con.row_factory = sqlite3.Row
cur = con.cursor()

pid = "sp_39f89a57ca2a"
cur.execute("SELECT posted_result_json FROM social_posts WHERE id=?", (pid,))
row = cur.fetchone()
pr = json.loads(row["posted_result_json"] or "{}")

# vervang oude FB-post door de nieuwe (typografische poster)
old_fb = pr.get("facebook", {})
new_fb = {
    "success": True,
    "post_id": "122117434923393597",
    "url": "https://www.facebook.com/122117434923393597",
    "site": "Bijeen",
    "style": "typografisch-poster",
}
pr["facebook"] = new_fb
pr["_platforms"] = [p for p in pr.get("_platforms", []) if p != "facebook"] + ["facebook"]

cur.execute(
    "UPDATE social_posts SET posted_result_json=? WHERE id=?",
    (json.dumps(pr, ensure_ascii=False), pid),
)
con.commit()
print("DB-record bijgewerkt voor", pid)
print(" FB-post_id nu:", pr["facebook"]["post_id"])
