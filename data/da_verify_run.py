import asyncio, sys, os
sys.path.insert(0, "/d/APPS/agentos")
os.environ["PYTHONPATH"] = "/d/APPS/agentos"
import scripts.da_post_engine as e
async def go():
    try:
        await asyncio.wait_for(e.main(), timeout=120)
    except asyncio.TimeoutError:
        print("TIMEOUT na 120s", flush=True)
    except Exception as ex:
        print(f"EXCEPTION: {type(ex).__name__}: {ex}", flush=True)
    print("[verify] engine returned")
asyncio.run(go())
