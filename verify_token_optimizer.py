# Ad-hoc verification script - token optimizer
import sys
sys.path.insert(0, r"D:\APPS\agentos")

from backend.shared.token_optimizer import strip_context_noise, truncate_to_token_budget, deduplicate_context, estimate_savings, optimize_prompt_messages

# Test 1
result = strip_context_noise("a\n\n\nb")
assert result == "a\nb", f"strip_context_noise failed: {repr(result)}"
print("✓ strip_context_noise")

# Test 2
trunc = truncate_to_token_budget(" ".join(["x"] * 1000), 500)
assert len(trunc.split()) <= 500
print("✓ truncate_to_token_budget")

# Test 3
dedup = deduplicate_context({"a": "same", "b": "same", "c": "other"})
assert len(dedup) == 2
print("✓ deduplicate_context")

# Test 4
savings = estimate_savings("a " * 100, "a " * 50)
assert savings == 50.0
print("✓ estimate_savings")

print("\n✅ AD-HOC VERIFICATIE GELUKT")