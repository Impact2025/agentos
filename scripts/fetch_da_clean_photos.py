import os, urllib.request, sys
sys.path.insert(0, ".")
from backend.shared.social_image import _pexels_search

specs = [
    ("30", "couple laughing coffee date warm natural", "data/uploads/da30_clean.jpg"),
    ("40", "happy couple walking holding hands sunny park", "data/uploads/da40_clean.jpg"),
    ("50", "older couple smiling together cozy home",      "data/uploads/da50_clean.jpg"),
]
for age, q, out in specs:
    res = _pexels_search(q, per_page=1)
    photos = res.get("photos", []) if isinstance(res, dict) else []
    if photos:
        url = photos[0]["src"]["large"]
        urllib.request.urlretrieve(url, out)
        print(f"  {age}: {out}  ({url[:55]}...)")
    else:
        print(f"  {age}: geen resultaat voor '{q}' -> {str(res)[:80]}")
