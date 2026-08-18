import sqlite3, json
c=sqlite3.connect('data/agentos.db')
fix={
 'sp_daDatingAssistent_01':'107835799327006_1068403385879851',
 'sp_da40_01':'174410412641281_1377727037897800',
 'sp_da50_01':'123632714408933_1354360576842200',
}
for rid,pid in fix.items():
    good={'facebook':{'success':True,'post_id':pid,'url':'https://www.facebook.com/'+pid,'site':rid},'_platforms':['facebook']}
    c.execute('UPDATE social_posts SET posted_result_json=? WHERE id=?',(json.dumps(good,ensure_ascii=False),rid))
c.commit()
print('gecorrigeerd.')
for r in c.execute("SELECT project, campaign_post, status, substr(posted_result_json,1,55) FROM social_posts WHERE campaign='da-doelgroepen-2026' ORDER BY project, campaign_post"):
    print('  %-20s %-5s %-13s %s' % (r[0], r[1], r[2], r[3]))
c.close()
